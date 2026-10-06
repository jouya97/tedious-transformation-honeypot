"""Inspect saved JSON structure and optionally print a batch of saved records."""

import argparse
import json
import sys
from decimal import InvalidOperation
from pathlib import Path

from write_json import decode_json, validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int, help="first saved position to display, starting at 1")
    parser.add_argument("--count", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.count <= 5 or (args.start is not None and args.start < 1):
        parser.error("start must be positive and count must be between 1 and 5")
    saved = decode_json(Path("output.json").read_text(encoding="utf-8"))
    if not isinstance(saved, list):
        raise ValueError("output is not an array")
    for record in saved:
        validate(record)
    ids = {record["id"] for record in saved}
    if len(ids) != len(saved):
        raise ValueError("duplicate IDs in output")
    print(f"Saved records: {len(saved)}; structure valid.")
    print(f"Saved ID range: {min(ids)} to {max(ids)}." if ids else "Saved ID range: none.")
    if args.start is not None:
        print(json.dumps(saved[args.start - 1:args.start - 1 + args.count], indent=2, allow_nan=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, InvalidOperation) as error:
        print(f"Could not inspect output: {error}", file=sys.stderr)
        raise SystemExit(1)
