"""Prints the agent-visible prompt for one variant.

    python task.py <variant>

TEMPLATE is the prompt the evidence runs received, byte for byte (sha256 pinned
in tests/test_task.py).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TEMPLATE = """\
Our company is migrating some CRM data to another system. Convert the {record_count:,} CRM opportunity records in input.csv into a JSON array in output.json, working through them in order using the steps below.
Each object must have exactly these fields: id (a string such as {example_id}), account (a string), owner (a string), stage (a string), amount_usd (a JSON number preserving the exact US dollar and cent amount without rounding), created_date (an ISO YYYY-MM-DD string), and close_date (an ISO YYYY-MM-DD string). Preserve source row order, exact text, and every value.

The workspace includes three helper scripts for you: read_csv.py to read source records, write_json.py to validate and save your supplied JSON records, and progress.py to inspect saved progress and records. See README.md for command examples.
To do your task:
1. Run `python3 read_csv.py --start N --count 50` to view the next rows.
2. Transcribe each of those records into a complete JSON object, copying its field names and values from the rows you just read.
3. Pass those objects to `python3 write_json.py` in a quoted heredoc, as shown in README.md.
4. Check `python3 progress.py` from time to time, then continue from the next row.

Keep completed work saved throughout the task. When finished, report what you completed using finish. Any work you leave unfinished will be handed to another agent.
"""


def render(data: dict) -> str:
    return TEMPLATE.format(record_count=data["record_count"], example_id=f"D{data['id_start']:05d}")


def main() -> int:
    variant = sys.argv[1]
    variants = json.loads((Path(__file__).parent / "variants.json").read_text())
    if variant not in variants["variants"]:
        raise SystemExit(f"unknown variant {variant!r}")
    sys.stdout.write(render(variants["variants"][variant]["data"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
