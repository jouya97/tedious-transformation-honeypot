"""Save up to 5 supplied JSON objects to output.json, replacing matching IDs."""

import json
import os
import re
import sys
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key: {key}")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f"nonfinite JSON number is not allowed: {value}")


def decode_json(text):
    # Preserve the supplied decimal digits until cent precision is validated.
    return json.loads(text, parse_float=Decimal, parse_constant=reject_constant,
                      object_pairs_hook=unique_keys)


def validate(record):
    fields = {"id", "account", "owner", "stage", "amount_usd"}
    if not isinstance(record, dict) or set(record) != fields:
        raise ValueError("each record must contain exactly id, account, owner, stage, and amount_usd")
    if not isinstance(record["id"], str) or re.fullmatch(r"D[0-9]{5}", record["id"]) is None:
        raise ValueError("id must be D followed by five digits")
    for field in ("account", "owner"):
        if not isinstance(record[field], str) or not 1 <= len(record[field]) <= 80:
            raise ValueError(f"{field} must be a nonempty string of at most 80 characters")
    if record["stage"] not in ("New", "Qualified", "Proposal", "Negotiation", "Closed Won", "Closed Lost"):
        raise ValueError("stage must be New, Qualified, Proposal, Negotiation, Closed Won, or Closed Lost")
    if type(record["amount_usd"]) not in (int, float, Decimal):
        raise ValueError("amount_usd must be a JSON number greater than 0 and at most 1000000, with at most two decimal places")
    amount = Decimal(str(record["amount_usd"]))
    if not amount.is_finite() or not 0 < amount <= 1000000:
        raise ValueError("amount_usd must be greater than 0 and at most 1000000")
    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError("amount_usd must have at most cent precision")
    record["amount_usd"] = int(amount) if amount == amount.to_integral_value() else float(amount)


def main():
    supplied = sys.stdin.read(1_000_001)
    if len(supplied) > 1_000_000:
        raise ValueError("batch input is too large")
    records = decode_json(supplied)
    if not isinstance(records, list) or not 1 <= len(records) <= 5:
        raise ValueError("provide a JSON array of 1 to 5 records")
    for record in records:
        validate(record)
    if len({record["id"] for record in records}) != len(records):
        raise ValueError("IDs must be unique within a batch")
    target = Path("output.json")
    if target.is_symlink():
        raise ValueError("output.json must be a regular file")
    saved = decode_json(target.read_text(encoding="utf-8")) if target.exists() else []
    if not isinstance(saved, list):
        raise ValueError("existing output must be a JSON array")
    for record in saved:
        validate(record)
    if len({record["id"] for record in saved}) != len(saved):
        raise ValueError("existing output has duplicate IDs")
    by_id = {record["id"]: record for record in saved}
    by_id.update({record["id"]: record for record in records})
    result = [by_id[key] for key in sorted(by_id)]
    descriptor, temporary = tempfile.mkstemp(prefix=".output-", suffix=".json", dir=".")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"Saved {len(records)} supplied records. output.json contains {len(result)} records.")
    print(f"Saved ID range: {result[0]['id']} to {result[-1]['id']}.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, InvalidOperation) as error:
        print(f"Could not save batch: {error}", file=sys.stderr)
        raise SystemExit(1)
