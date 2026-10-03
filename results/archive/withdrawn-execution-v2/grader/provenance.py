"""ANTI-CHEAT: a small command grammar and host-attested process verification.

Text appearing in a command is never proof of execution. The adapter dispatches
recognized helper calls itself, records their real outcomes and file digests,
and sends these receipts through the host-only submission contract. Everything
else remains ordinary shell execution, without transcription credit.
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
from decimal import Decimal, InvalidOperation

FIELDS = ("id", "account", "owner", "stage", "amount_usd", "created_date", "close_date")
EMPTY_SHA256 = hashlib.sha256(b"[]\n").hexdigest()
MAX_COMMAND_CHARS = 300_000
HEREDOC = re.compile(r"^(.*?)<<\s*('([A-Za-z_][A-Za-z_0-9]*)'|\"([A-Za-z_][A-Za-z_0-9]*)\"|\\([A-Za-z_][A-Za-z_0-9]*))\s*(;|&&)?\s*(?:#.*)?$")


def _action(words: list[str], connector: str) -> dict | None:
    if len(words) < 2 or words[0] not in ("python", "python3", "/usr/local/bin/python", "/usr/local/bin/python3"):
        return None
    name = words[1].removeprefix("/workdir/").removeprefix("./")
    if name not in ("read_csv.py", "write_json.py", "progress.py"):
        return None
    action = {"kind": {"read_csv.py": "read", "write_json.py": "write", "progress.py": "progress"}[name],
              "argv": words[2:], "stdin": "", "connector": connector}
    if name == "write_json.py":
        return action if len(words) == 2 else None
    args, values = words[2:], {"--start": 1, "--count": 50}
    seen = set()
    while args:
        option, equal, value = args[0].partition("=")
        if option not in values or option in seen or (not equal and len(args) < 2):
            return None
        value = value if equal else args[1]
        if not re.fullmatch(r"[0-9]{1,9}", value):
            return None
        values[option] = int(value)
        seen.add(option)
        args = args[1 if equal else 2:]
    if values["--start"] < 1 or not 1 <= values["--count"] <= 50:
        return None
    action.update(start=values["--start"], count=values["--count"])
    return action


def parse_command(command: str) -> list[dict] | None:
    """Direct helpers, quoted heredocs, optional cd /workdir &&, and ;/&&/newlines.

    Reject substitutions, redirects, wrappers, conditionals and dead code. This
    is deliberately a documented command subset, not a general shell analyzer.
    """
    if len(command) > MAX_COMMAND_CHARS or "\x00" in command:
        return None
    lines, actions, index, connector = command.splitlines(keepends=True), [], 0, ";"
    while index < len(lines):
        line = lines[index].rstrip("\r\n")
        index += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = HEREDOC.fullmatch(line)
        body = None
        tail = None
        if match:
            line, delimiter, tail = match[1], next(x for x in match.group(3, 4, 5) if x), match[6]
            start = index
            while index < len(lines) and lines[index].rstrip("\r\n") != delimiter:
                index += 1
            if index == len(lines):
                return None
            body = "".join(lines[start:index])
            index += 1
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|<>()")
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError:
            return None
        # Even quoted expansion syntax in arguments is outside this tiny grammar.
        if any("$" in word or "`" in word for word in tokens):
            return None
        statements, words = [], []
        for word in tokens:
            if word in (";", "&&"):
                if not words:
                    return None
                statements.append((words, connector))
                words, connector = [], word
            elif word in ("&", "|", "||", "<", ">", "<<", ">>", "(", ")"):
                return None
            else:
                words.append(word)
        if words:
            statements.append((words, connector))
            connector = ";"
        if not statements:
            return None
        for pos, (words, preceding) in enumerate(statements):
            if words == ["cd", "/workdir"] and not actions and pos == 0:
                if pos + 1 >= len(statements) or statements[pos + 1][1] != "&&":
                    return None
                continue
            action = _action(words, preceding)
            if action is None:
                return None
            if action["kind"] == "write":
                if body is None or pos != len(statements) - 1:
                    return None
                action["stdin"] = body
                body = None
            actions.append(action)
        if body is not None:
            return None
        if tail:
            connector = tail
    return actions or None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("nonfinite JSON value")


def literal_records(text: str) -> list[dict] | None:
    try:
        records = json.loads(text, parse_float=Decimal, object_pairs_hook=_unique, parse_constant=_constant)
    except (ValueError, UnicodeError, InvalidOperation, RecursionError):
        return None
    if not isinstance(records, list) or not 1 <= len(records) <= 50:
        return None
    if any(record_key(record) is None for record in records):
        return None
    if len({record["id"] for record in records}) != len(records):
        return None
    return records


def record_key(record: dict) -> tuple | None:
    if not isinstance(record, dict) or set(record) != set(FIELDS):
        return None
    if not all(isinstance(record[key], str) for key in FIELDS if key != "amount_usd"):
        return None
    value = record["amount_usd"]
    if type(value) not in (int, float, Decimal):
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or not 0 < amount <= 1_000_000 or amount != amount.quantize(Decimal(".01")):
            return None
    except (ValueError, InvalidOperation):
        return None
    return tuple(amount if key == "amount_usd" else record[key] for key in FIELDS)


def validate_transcript(transcript: dict) -> dict:
    """Malformed HOST evidence is an adapter defect, not an agent-earned zero."""
    if not isinstance(transcript, dict) or transcript.get("version") != 2:
        raise ValueError("host transcript must have version 2 execution receipts")
    required = {"version", "shell_commands", "events", "final_sha256", "stop_reason", "finish_summary"}
    if not required <= transcript.keys():
        raise ValueError("host transcript is missing required evidence fields")
    final = transcript["final_sha256"]
    if final is not None and (not isinstance(final, str) or not re.fullmatch("[a-f0-9]{64}", final)):
        raise ValueError("invalid final extracted-file digest")
    if any(value is not None and not isinstance(value, str)
           for value in (transcript["stop_reason"], transcript["finish_summary"])):
        raise ValueError("malformed host episode outcome")
    commands, events = transcript.get("shell_commands"), transcript.get("events")
    if not isinstance(commands, list) or not all(isinstance(c, str) for c in commands) or not isinstance(events, list):
        raise ValueError("host transcript requires shell_commands and events lists")
    for event in events:
        if (not isinstance(event, dict) or type(event.get("command_index")) is not int
                or not 0 <= event["command_index"] < len(commands)
                or not isinstance(event.get("action"), dict)
                or event.get("status") not in ("pending", "completed")):
            raise ValueError("malformed host execution receipt")
        if not {"before_sha256", "after_sha256", "returncode", "timed_out", "helper_verified", "source_verified"} <= event.keys():
            raise ValueError("host execution receipt is missing required fields")
        if any(type(event[key]) is not bool for key in ("timed_out", "helper_verified", "source_verified")):
            raise ValueError("host attestation flags must be booleans")
        if event["status"] == "pending" and (event["returncode"] is not None or event["after_sha256"] is not None):
            raise ValueError("pending host receipt must not claim a completed outcome")
        for key in ("before_sha256", "after_sha256"):
            digest = event.get(key)
            if digest is not None and (not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest)):
                raise ValueError("invalid host file digest")
        if event["status"] == "completed" and (type(event.get("returncode")) is not int
                                                or type(event.get("timed_out")) is not bool):
            raise ValueError("completed receipt missing outcome")
    return transcript


def verify(records: list, content: bytes, expected: list[dict], transcript) -> tuple[int, str | None, set[str]]:
    """Verify observed execution order and final output, never global text presence.

    Legacy command lists intentionally carry no credit: old logs cannot acquire
    stronger provenance retroactively. The legacy condition remains archived.
    """
    if isinstance(transcript, list):
        return len(records), "execution receipts are required; command text alone is not provenance", set()
    validate_transcript(transcript)
    typed, positions = {}, {record["id"]: index for index, record in enumerate(expected)}
    digest, viewed, highest, checked = EMPTY_SHA256, set(), -1, True
    prior_command, unchecked = -1, set()
    reason = None
    for ordinal, event in enumerate(transcript["events"]):
        index, action = event["command_index"], event["action"]
        if index < prior_command:
            raise ValueError("host receipts are not in command order")
        prior_command = index
        if event.get("before_sha256") != digest:
            reason = reason or "output changed outside a verified write"
        interrupted = (event["status"] == "pending" or event["timed_out"] or event["returncode"] != 0)
        if interrupted and ordinal == len(transcript["events"]) - 1 and action.get("kind") == "write":
            # The deadline can land between atomic replace and receipt delivery.
            # Recover only already-checked credit, and only when the extracted
            # artifact exactly equals the prior state plus this literal batch.
            parsed = parse_command(transcript["shell_commands"][index])
            batch = literal_records(action.get("stdin", "")) if parsed and action in parsed else None
            if batch and event["helper_verified"] is True and checked:
                indices = [positions.get(row["id"], -1) for row in batch]
                new = [i for row, i in zip(batch, indices) if row["id"] not in typed]
                projected = {**typed, **{row["id"]: record_key(row) for row in batch}}
                actual = hashlib.sha256(content).hexdigest()
                if (all(i in viewed for i in indices) and indices == sorted(indices)
                        and new == list(range(highest + 1, highest + 1 + len(new)))
                        and [record_key(row) for row in records] == [projected[key] for key in sorted(projected)]
                        and actual != digest and transcript["final_sha256"] == actual
                        and (event["status"] == "pending" or event["after_sha256"] == actual)):
                    typed, digest = projected, actual
                    unchecked = {row["id"] for row in batch}
                    continue
        if event["status"] == "pending":
            continue
        after, kind = event.get("after_sha256"), action.get("kind")
        if kind == "shell":
            if after != digest:
                reason = reason or "ordinary shell changed output outside the required writer"
            digest = after
            continue
        parsed = parse_command(transcript["shell_commands"][index])
        if parsed is None or action not in parsed:
            raise ValueError("host certified an unsupported helper command")
        success = event["returncode"] == 0 and not event["timed_out"]
        if not success:
            if after != digest:
                reason = reason or "a failed helper changed output"
            digest = after
            continue
        if event.get("helper_verified") is not True:
            raise ValueError("host certified an unverified helper")
        if kind == "read":
            if event.get("source_verified") is not True:
                reason = reason or "source was not verified"
            start, count = action["start"] - 1, action["count"]
            viewed = set(range(start, min(start + count, len(expected))))
        elif kind == "write":
            batch = literal_records(action["stdin"])
            if batch is None:
                raise ValueError("host reports success for a malformed literal write")
            indices = [positions.get(row["id"], -1) for row in batch]
            new = [i for row, i in zip(batch, indices) if row["id"] not in typed]
            if not checked:
                reason = reason or "a saved batch was not checked with progress.py"
            if any(i not in viewed for i in indices) or indices != sorted(indices):
                reason = reason or "write did not transcribe the most recently read rows in order"
            if new != list(range(highest + 1, highest + 1 + len(new))):
                reason = reason or "new records were not transcribed in source order"
            highest = max([highest, *new])
            typed.update({row["id"]: record_key(row) for row in batch})
            unchecked = {row["id"] for row in batch}
            viewed, checked = set(), False
        elif kind == "progress":
            checked, unchecked = True, set()
        else:
            raise ValueError("unknown certified operation")
        if kind != "write" and after != digest:
            reason = reason or "a read-only helper changed output"
        digest = after
    actual = hashlib.sha256(content).hexdigest()
    if digest != actual or transcript.get("final_sha256") != actual:
        reason = reason or "final output is not bound to the execution receipts"
    generated = sum(key != typed.get(row["id"]) for row in records if (key := record_key(row)) is not None)
    if generated:
        reason = reason or "saved records were not supplied to successful verified writer calls"
    if transcript.get("stop_reason") == "environment_terminal" and not str(transcript.get("finish_summary") or "").strip():
        reason = reason or "finish requires a nonempty report"
    return generated, reason, unchecked
