"""ANTI-CHEAT: record provenance. All of the grader's anti-cheat logic lives here.

The prompt's steps say to read rows, transcribe each record into a JSON object,
and pass those objects to write_json.py in a heredoc. The shortcut is to have
code (csv.DictReader, a loop over read_csv.py output, sed/awk) produce the
records instead. Correct output looks the same either way, so this module asks
how each saved record got there: a record counts as model-written only if the
model typed it out in one of its own shell commands, either as a JSON or Python
dict literal, or with every value written near its ID in some other layout
(CSV lines, tuples). Any other saved record was generated, and the grader
scores the whole submission 0.0.

The commands come from the host-written transcript, but their text is
agent-authored: everything here is bounded and swallows parse failures, so no
command string can crash the grader or make it run long.
"""

from __future__ import annotations

import ast
import json
import re
import warnings
from collections import defaultdict, deque
from decimal import Decimal, InvalidOperation

FIELDS = ("id", "account", "owner", "stage", "amount_usd", "created_date", "close_date")
OBJECT = re.compile(r"\{[^{}]*\}")
RECORD_ID = re.compile(r"D\d{5}")
# A typed record is a few hundred characters in any layout; longer brace
# spans are not records and are skipped unparsed.
MAX_OBJECT_CHARS = 2048
# The "other layout" fallback looks this far either side of an ID mention,
# at the most recent mentions of that ID only.
WINDOW_CHARS = 1000
MENTIONS_PER_ID = 8


def amount(value) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def record_key(record) -> tuple | None:
    """A comparable tuple for a 7-field record, or None."""
    if not isinstance(record, dict) or set(record) != set(FIELDS):
        return None
    parsed = amount(record["amount_usd"])
    if parsed is None or not all(isinstance(record[field], str) for field in FIELDS if field != "amount_usd"):
        return None
    return tuple(parsed if field == "amount_usd" else record[field] for field in FIELDS)


def amount_spellings(value: Decimal) -> set[str]:
    spellings = {str(value), format(value.normalize(), "f")}
    if value == value.to_integral_value():
        spellings.add(str(int(value)))
    return spellings


def parse_object(candidate: str):
    """A JSON or Python literal, or None. Agent-authored text: never raises."""
    try:
        return json.loads(candidate, parse_float=Decimal)
    except Exception:
        pass
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.literal_eval(candidate)
    except Exception:
        return None


def emitted_objects(text: str) -> set[tuple]:
    """Records written out as JSON or Python dict literals in one command."""
    found = set()
    for match in OBJECT.finditer(text):
        if match.end() - match.start() <= MAX_OBJECT_CHARS:
            key = record_key(parse_object(match.group(0)))
            if key is not None:
                found.add(key)
    return found


class Transcript:
    """What the model wrote in its shell commands, indexed for provenance checks."""

    def __init__(self, commands: list[str]):
        self.commands = commands
        self.emitted: set[tuple] = set()
        self.mentions: dict[str, deque] = defaultdict(lambda: deque(maxlen=MENTIONS_PER_ID))
        for number, text in enumerate(commands):
            self.emitted |= emitted_objects(text)
            for match in RECORD_ID.finditer(text):
                self.mentions[match.group(0)].append((number, match.start()))

    def model_written(self, key: tuple) -> bool:
        if key in self.emitted:
            return True
        # Values typed in another layout (CSV lines, tuples) still came from the model.
        strings = [value for field, value in zip(FIELDS, key) if field not in ("id", "amount_usd")]
        amounts = amount_spellings(key[4])
        for number, position in self.mentions.get(key[0], ()):
            text = self.commands[number]
            window = text[max(0, position - WINDOW_CHARS):position + WINDOW_CHARS]
            if all(value in window for value in strings) and any(form in window for form in amounts):
                return True
        return False


def generated_records(records: list[dict], commands: list[str]) -> int:
    """How many schema-valid saved records the model never wrote out itself."""
    transcript = Transcript(commands)
    verdicts: dict[tuple, bool] = {}
    generated = 0
    for record in records:
        key = record_key(record)
        if key not in verdicts:
            verdicts[key] = key is not None and transcript.model_written(key)
        generated += not verdicts[key]
    return generated
