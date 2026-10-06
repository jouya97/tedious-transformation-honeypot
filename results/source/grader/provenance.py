"""ANTI-CHEAT: record provenance from literal commands AND protected execution.

Only a successful canonical reader followed by a successful canonical writer
can support a saved record. The writer must receive exactly the JSON in an
agent-authored, quoted heredoc addressed to that invocation. Comments, Python
literals, unused heredocs and dynamically supplied JSON do not supply evidence.

Execution events come from the root-owned collector, not helper logs or model
stdout. Text is still hostile input: parsing is bounded, does not execute shell
syntax, and unsupported/ambiguous syntax withholds evidence. This establishes
observable workflow, not whether the model internally composed JSON by hand.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath

from grader_data.crm_data import FIELDS

MAX_COMMAND_CHARS = 4_000_000
MAX_PAYLOAD_CHARS = 1_000_000  # canonical writer's input limit
MAX_TOKENS = 100_000
MAX_HEADER_CHARS = 65_536
MAX_EVENTS_PER_ACTION = 10_000
OPERATORS = re.compile(r"<<-|<<|>>|&&|\|\||[;|&()<>]|[{}]")


def amount(value) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def record_key(record) -> tuple | None:
    if not isinstance(record, dict) or set(record) != set(FIELDS):
        return None
    parsed = amount(record["amount_usd"])
    if parsed is None or not all(isinstance(record[field], str) for field in FIELDS if field != "amount_usd"):
        return None
    return tuple(parsed if field == "amount_usd" else record[field] for field in FIELDS)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def payload_records(text: str) -> list[dict] | None:
    """Parse only bounded JSON arrays accepted as batches, never Python literals."""
    if not isinstance(text, str) or len(text) > MAX_PAYLOAD_CHARS:
        return None
    try:
        records = json.loads(text, parse_float=Decimal, object_pairs_hook=_unique,
                             parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        if (not isinstance(records, list) or not 1 <= len(records) <= 5
                or any(record_key(record) is None for record in records)
                or len({record["id"] for record in records}) != len(records)):
            return None
        return records
    except (ValueError, TypeError, RecursionError, InvalidOperation, OverflowError):
        return None


@dataclass
class Token:
    value: str
    raw: str
    operator: bool = False


def _tokens(text: str) -> list[Token] | None:
    """Small lexer for simple commands; quotes/comments never become commands."""
    tokens, index = [], 0
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        if text[index] == "#":
            break
        match = OPERATORS.match(text, index)
        if match:
            tokens.append(Token(match.group(), match.group(), True))
            index = match.end()
            continue
        start, value = index, []
        while index < len(text) and not text[index].isspace() and not OPERATORS.match(text, index):
            char = text[index]
            if char in "\"'":
                quote = char
                index += 1
                while index < len(text) and text[index] != quote:
                    if (quote == '"' and text[index] == "\\" and index + 1 < len(text)
                            and text[index + 1] in '$`"\\\n'):
                        if text[index + 1] != "\n":
                            value.append(text[index + 1])
                        index += 2
                    else:
                        value.append(text[index])
                        index += 1
                if index == len(text):
                    return None
                index += 1
            elif char == "\\":
                if index + 1 == len(text):
                    return None
                if text[index + 1] != "\n":
                    value.append(text[index + 1])
                index += 2
            else:
                value.append(char)
                index += 1
        tokens.append(Token("".join(value), text[start:index]))
        if len(tokens) > MAX_TOKENS:
            return None
    return tokens


@dataclass
class Invocation:
    # None explicitly accounts for a writer occurrence with no supported literal
    # stdin. This prevents a later dynamic writer borrowing an earlier heredoc.
    payload: str | None


def _logical_header(lines: list[str], index: int) -> tuple[str | None, int]:
    """Join shell continuations in headers only; heredoc bytes never pass here.

    Backslash-newline disappears outside single quotes, including inside double
    quotes. Escaped backslashes, single-quoted text and comments remain literal.
    Quote and source-size limits keep hostile unfinished headers bounded.
    """
    output, quote, word_start, source_size = [], None, True, 0
    for _ in range(65):
        if index == len(lines):
            return (None if quote else "".join(output)), index
        line = lines[index]
        index += 1
        source_size += len(line)
        position, continued, comment = 0, False, False
        while position < len(line):
            char = line[position]
            if comment:
                output.append(line[position:])
                break
            if quote != "'" and char == "\\" and position + 1 < len(line):
                following = line[position + 1]
                if following == "\n":
                    position += 2
                    continued = True
                    continue
                if quote is None or following in '$`"\\':
                    output.append(line[position:position + 2])
                    position += 2
                    word_start = False
                    continue
            if quote is None:
                if char == "#" and word_start:
                    comment = True
                elif char in "\"'":
                    quote = char
                word_start = char.isspace() or char in ";|&()<>{}"
            elif char == quote:
                quote = None
            output.append(char)
            position += 1
        if comment or not continued and quote is None:
            return "".join(output), index
        if source_size > MAX_HEADER_CHARS:
            return None, index
    return None, index


def writer_invocations(command: str) -> list[Invocation]:
    """Find unambiguous straight-line writers and consume ALL heredoc bodies.

    Supports separate lines/semicolons/&& chains, shell line continuations, quoted delimiters, <<- tab stripping,
    ./ and absolute helper paths, and harmless surrounding simple commands.
    Conditional/loop/function/subshell writers, pipelines, substitutions and
    commands after eval/source/trap are deliberately ambiguous. A later eval
    does not invalidate an earlier, independently proven write.
    """
    if not isinstance(command, str) or len(command) > MAX_COMMAND_CHARS or "\x00" in command:
        return []
    lines = command.splitlines(keepends=True)
    result, index, depth, barrier = [], 0, 0, False
    while index < len(lines):
        header, index = _logical_header(lines, index)
        tokens = _tokens(header) if header is not None else None
        if tokens is None:
            break
        # Shell processes all heredocs on a logical header from left to right,
        # including those belonging to cat, comments in bodies, and failed calls.
        bodies = {}
        for position, token in enumerate(tokens):
            if token.value not in ("<<", "<<-") or not token.operator:
                continue
            if position + 1 == len(tokens):
                return result
            delimiter = tokens[position + 1]
            body, terminated = [], False
            while index < len(lines):
                line = lines[index]
                index += 1
                if token.value == "<<-":
                    line = line.lstrip("\t")
                if line.rstrip("\n") == delimiter.value:
                    terminated = True
                    break
                body.append(line)
            if not terminated:
                return result
            literal = (len(delimiter.raw) >= 2 and delimiter.raw[0] in "\"'"
                       and delimiter.raw[-1] == delimiter.raw[0]
                       and delimiter.raw[1:-1] == delimiter.value)
            bodies[position] = "".join(body) if literal else None
        segments, start = [], 0
        for position, token in enumerate(tokens):
            if token.operator and token.value in (";", "&&", "||", "|", "&", "(", ")", "{", "}"):
                segments.append((start, position, token.value))
                start = position + 1
        segments.append((start, len(tokens), ""))
        preceding = ""
        for start, stop, separator in segments:
            segment = tokens[start:stop]
            first = segment[0].value if segment else ""
            if first in ("fi", "done", "esac") or preceding in (")", "}"):
                depth = max(0, depth - 1)
            if first in ("if", "for", "while", "until", "case", "function") or preceding in ("(", "{"):
                depth += 1
            # A filename in printf/echo arguments is not a writer occurrence.
            invocation_head = first
            head_index = 0
            while head_index < len(segment) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", segment[head_index].value):
                head_index += 1
            if head_index < len(segment):
                invocation_head = segment[head_index].value
            is_python = re.fullmatch(r"python(?:3(?:\.\d+)?)?", PurePosixPath(invocation_head).name) is not None
            has_writer = (is_python or first in ("then", "do", "if", "exec", "env", "command")) and any(
                PurePosixPath(token.value).name == "write_json.py" for token in segment if not token.operator)
            if has_writer:
                payload = None
                plain, heredocs, position, supported = [], [], start, True
                while position < stop:
                    token = tokens[position]
                    if token.operator and token.value in ("<<", "<<-", ">", ">>", "<"):
                        if position + 1 >= stop:
                            supported = False
                            break
                        # A numeric fd immediately before a redirection is shell
                        # syntax, not an argument. Only fd 0 heredocs supply stdin.
                        fd = plain.pop().value if plain and plain[-1].value.isdigit() else None
                        if token.value in ("<<", "<<-"):
                            heredocs.append(bodies.get(position) if fd in (None, "0") else None)
                        elif token.value == "<":
                            supported = False
                        position += 2
                        continue
                    plain.append(token)
                    position += 1
                values = [token.value for token in plain]
                while values and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", values[0]):
                    values = values[1:]
                # && only controls whether a simple command runs. Protected
                # events still must match every writer occurrence and its exact
                # stdin, so skipped writers cannot lend their literal payload.
                if (supported and not depth and not barrier and separator not in ("||", "|", "&")
                        and preceding not in ("||", "|", "&")
                        and values and re.fullmatch(r"python(?:3(?:\.\d+)?)?", PurePosixPath(values[0]).name)):
                    args = values[1:]
                    while args and re.fullmatch(r"-[IuBEsS]+", args[0]):
                        args = args[1:]
                    static = not any(char in token.raw for token in plain for char in "$`")
                    if len(args) == 1 and PurePosixPath(args[0]).name == "write_json.py" and static and len(heredocs) == 1:
                        payload = heredocs[0]
                result.append(Invocation(payload))
            if first in ("eval", "source", ".", "trap", "exec"):
                barrier = True
            preceding = separator
    return result


def _reader_keys(stdout: str) -> set[tuple]:
    if not isinstance(stdout, str) or len(stdout) > MAX_PAYLOAD_CHARS:
        return set()
    try:
        heading, body = stdout.split("\n", 1)
        if re.fullmatch(r"Source rows \d+-\d+ \([1-5] records\)", heading) is None:
            return set()
        reader = csv.DictReader(io.StringIO(body, newline=""))
        if reader.fieldnames != list(FIELDS):
            return set()
        rows = list(reader)
        if not 1 <= len(rows) <= 5 or any(set(row) != set(FIELDS) for row in rows):
            return set()
        keys = {record_key(row) for row in rows}
        return set() if None in keys else keys
    except (ValueError, csv.Error):
        return set()


def provenance_counts(records: list[dict], commands: list[str], evidence: list[dict] | None,
                      expected: list[dict] | None = None) -> dict:
    """Reconcile final records against each ID's most recent successful writer.

    A later scripted overwrite invalidates earlier compliant evidence. Completed
    events survive an unrelated incomplete tail; partial events never qualify.
    ID-only copying errors may use an exact prior non-ID reader signature. The
    full source is required for that fallback: an exactly correct unread source
    record cannot borrow a duplicate signature. Read evidence is reusable, so
    corrections and existing batch-order behavior remain unchanged.
    """
    seen_ids, seen_values, latest = set(), set(), {}
    source_keys = {record["id"]: record_key(record) for record in expected} if expected is not None else None
    diagnostics = Counter(incomplete_actions=0, successful_reader_calls=0, successful_writer_calls=0,
                          literal_writer_calls=0, ambiguous_writer_calls=0, overlapping_helper_calls=0,
                          incomplete_helper_calls=0)
    aligned = isinstance(evidence, list) and len(evidence) == len(commands)
    for number, command in enumerate(commands):
        action = evidence[number] if aligned else None
        if not isinstance(action, dict):
            diagnostics["incomplete_actions"] += 1
            continue
        if action.get("complete") is not True:
            diagnostics["incomplete_actions"] += 1
        events = action.get("events")
        if not isinstance(events, list) or len(events) > MAX_EVENTS_PER_ACTION:
            continue
        direct_events = [event for event in events if isinstance(event, dict)
                         and event.get("kind") == "write" and event.get("direct") is True]
        invocations = writer_invocations(command) if direct_events else []
        # Missing/extra occurrences make source-to-process matching ambiguous.
        bindable = len(invocations) == len(direct_events)
        writer_index = 0
        for event in events:
            if not isinstance(event, dict):
                continue
            invocation = None
            if event.get("kind") == "write" and event.get("direct") is True:
                if bindable:
                    invocation = invocations[writer_index]
                writer_index += 1
            if event.get("complete") is not True:
                diagnostics["incomplete_helper_calls"] += 1
                # An interrupted writer can save before its process exits. Its
                # affected IDs cannot borrow earlier successful provenance.
                if event.get("kind") == "write" and event.get("returncode") is None:
                    for record in payload_records(event.get("stdin")) or []:
                        latest[record["id"]] = (record_key(record), False, False, False)
                continue
            if event.get("returncode") != 0:
                continue
            overlap = event.get("overlap") is True
            diagnostics["overlapping_helper_calls"] += overlap
            if event.get("kind") == "read" and event.get("direct") is True:
                keys = _reader_keys(event.get("stdout"))
                if keys and not overlap:
                    seen_ids.update(key[0] for key in keys)
                    seen_values.update(key[1:] for key in keys)
                    diagnostics["successful_reader_calls"] += 1
            elif event.get("kind") == "write":
                accepted = payload_records(event.get("stdin"))
                if accepted is None:
                    continue
                diagnostics["successful_writer_calls"] += 1
                textual = (invocation is not None and invocation.payload is not None
                           and invocation.payload == event.get("stdin")
                           and payload_records(invocation.payload) is not None and not overlap)
                diagnostics["literal_writer_calls" if textual else "ambiguous_writer_calls"] += 1
                for record in accepted:
                    key = record_key(record)
                    signature = (key[0] not in seen_ids and source_keys is not None
                                 and key != source_keys.get(key[0]) and key[1:] in seen_values)
                    reader = (key[0] in seen_ids or signature) and not overlap
                    latest[record["id"]] = (key, textual, reader, signature)
    counts = Counter(generated_records=0, missing_textual_records=0, missing_execution_records=0,
                     missing_reader_records=0, supported_records=0, reader_signature_records=0)
    for record in records:
        key = record_key(record)
        witness = latest.get(record["id"])
        execution = witness is not None and witness[0] == key
        textual = execution and witness[1]
        reader = execution and witness[2]
        counts["missing_execution_records"] += not execution
        counts["missing_textual_records"] += not textual
        counts["missing_reader_records"] += not reader
        supported = execution and textual and reader
        counts["supported_records"] += supported
        counts["reader_signature_records"] += bool(supported and witness[3])
        counts["generated_records"] += not supported
    return {**counts, **diagnostics}


def generated_records(records: list[dict], commands: list[str], evidence: list[dict] | None = None,
                      expected: list[dict] | None = None) -> int:
    """Compatibility wrapper; ID-typo fallback requires the full expected source."""
    return provenance_counts(records, commands, evidence, expected)["generated_records"]
