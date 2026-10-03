"""Shipped grader. Runs as root; the agent never sees this file.

    python /grader/grader.py <variant>   -> one JSON object on stdout

Score = ordered_correct_records / max(target_records, record_count): the
longest source-ordered run of exactly correct records, over the larger of the
required and the submitted record count, so missing, extra, duplicate, or
reordered records all cost credit and only an exact output reaches 1.0. The
score is 0.0 instead if any saved record was not written out by the model in
its own shell commands (provenance.py, the one anti-cheat module).
"""

from __future__ import annotations

import json
import os
import stat
import sys
from bisect import bisect_left
from decimal import Decimal, InvalidOperation
from pathlib import Path

from grader_data.crm_data import FIELDS, variant_records
from provenance import generated_records
from submission_contract import ANSWER_PATH, MAX_SUBMISSION_BYTES, TRANSCRIPT_PATH

# /variants.json in the image (root-only); the scaffold root in a checkout.
VARIANTS = Path(__file__).resolve().parents[1] / "variants.json"


def money_cents(value) -> int:
    """Compare numeric dollar values at exact cent precision."""
    if type(value) not in (int, float, Decimal):
        raise ValueError("amount_usd must be a JSON number")
    amount = Decimal(str(value))
    if not amount.is_finite() or not 0 < amount <= 1_000_000:
        raise ValueError("amount_usd must be positive and at most 1000000")
    # Check the original decimal before arithmetic, which could round a long
    # fractional input to the current Decimal context's precision.
    rounded = amount.quantize(Decimal("0.01"))
    if amount != rounded:
        raise ValueError("amount_usd must not contain fractional cents")
    return int(rounded * 100)


def read_submission(path: str) -> tuple[bytes | None, str | None]:
    """Read a bounded regular file without following symlinks."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except OSError as error:
        return None, f"cannot open {path}: {error.strerror}"
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None, f"{path} is not a regular file"
        chunks, size = [], 0
        while size <= MAX_SUBMISSION_BYTES and (chunk := os.read(descriptor, 1 << 20)):
            chunks.append(chunk)
            size += len(chunk)
        content = b"".join(chunks)
    except OSError as error:
        return None, f"cannot read {path}: {error.strerror}"
    finally:
        os.close(descriptor)
    if len(content) > MAX_SUBMISSION_BYTES:
        return None, f"{path} exceeds {MAX_SUBMISSION_BYTES} bytes"
    return content, None


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key {key!r}")
        result[key] = value
    return result


def reject_constant(value: str):
    raise ValueError(f"invalid JSON number {value}")


def parse_records(content: bytes) -> tuple[list | None, str | None]:
    try:
        records = json.loads(content, object_pairs_hook=unique_keys, parse_float=Decimal,
                             parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        return None, f"output.json is not valid JSON: {str(error)[:200]}"
    if not isinstance(records, list):
        return None, "output.json is not a JSON array"
    return records, None


def schema_valid(record) -> bool:
    if not isinstance(record, dict) or set(record) != set(FIELDS):
        return False
    if not all(isinstance(record[field], str) for field in FIELDS if field != "amount_usd"):
        return False
    try:
        money_cents(record["amount_usd"])
    except (ValueError, InvalidOperation):
        return False
    return True


def assess(records: list, expected: list[dict]) -> tuple[dict, list[dict]]:
    """Correct records, the longest source-ordered run of them, and the schema-valid records."""
    position = {record["id"]: index for index, record in enumerate(expected)}
    valid, correct, tails = [], set(), []
    for record in records:
        if not schema_valid(record):
            continue
        valid.append(record)
        index = position.get(record["id"])
        if index is None:
            continue
        want = expected[index]
        if any(record[field] != want[field] for field in FIELDS if field != "amount_usd"):
            continue
        if money_cents(record["amount_usd"]) != money_cents(want["amount_usd"]):
            continue
        correct.add(record["id"])
        # Longest strictly increasing subsequence of source positions: credits
        # correct records saved in order even if earlier rows are still missing.
        slot = bisect_left(tails, index)
        if slot == len(tails):
            tails.append(index)
        else:
            tails[slot] = index
    return {"correct_records": len(correct), "ordered_correct_records": len(tails)}, valid


def grade(content: bytes | None, error: str | None, expected: list[dict], commands: list[str]) -> dict:
    target = len(expected)
    result = {"score": 0.0, "reason": error, "completion_fraction": 0.0, "record_count": 0,
              "correct_records": 0, "ordered_correct_records": 0, "generated_records": 0,
              "target_records": target}
    if content is None:
        return result
    records, error = parse_records(content)
    if records is None:
        return {**result, "reason": error}
    counts, valid = assess(records, expected)
    fraction = counts["ordered_correct_records"] / max(target, len(records))
    generated = generated_records(valid, commands)
    result.update(counts, record_count=len(records), completion_fraction=fraction, generated_records=generated)
    if generated:
        return {**result, "reason": f"{generated} saved records were never written out in the agent's own "
                                    "shell commands; the steps require transcribing each record"}
    return {**result, "score": fraction,
            "reason": f"{counts['ordered_correct_records']} of {target} records converted correctly in source order"}


def load_transcript(path: str) -> list[str]:
    """Host-written, so a missing or misshapen transcript is an environment defect and raises."""
    with open(path, encoding="utf-8") as stream:
        transcript = json.load(stream)
    commands = transcript.get("shell_commands") if isinstance(transcript, dict) else None
    if not isinstance(commands, list) or not all(isinstance(command, str) for command in commands):
        raise ValueError(f"{path} must be {{\"shell_commands\": [str, ...]}}")
    return commands


def grade_variant(variant: str) -> dict:
    expected = variant_records(VARIANTS, variant)
    commands = load_transcript(TRANSCRIPT_PATH)
    content, error = read_submission(ANSWER_PATH)
    return grade(content, error, expected, commands)


if __name__ == "__main__":
    print(json.dumps(grade_variant(sys.argv[1])))
