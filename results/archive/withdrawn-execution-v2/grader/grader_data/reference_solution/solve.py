"""Honest reference: the shell commands a compliant agent issues, and a runner for them.

For each batch of 50 source rows it reads the rows with read_csv.py, then types
them as JSON objects into write_json.py through a quoted heredoc, every value
copied verbatim from the CSV text (amounts as written, e.g. 74489.20). Successful helper calls and file digests are recorded as execution receipts.
This is an offline acceptance fixture, not a model performance demonstration.

    python solve.py [--workdir /workdir] [--transcript PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import csv
import io
import json
import subprocess
from pathlib import Path

BATCH = 50


def typed_record(row: dict) -> str:
    fields = [f'"{name}": {value if name == "amount_usd" else json.dumps(value)}' for name, value in row.items()]
    return "{" + ", ".join(fields) + "}"


def commands(source_csv: str) -> list[str]:
    rows = list(csv.DictReader(io.StringIO(source_csv, newline="")))
    result = []
    for start in range(0, len(rows), BATCH):
        batch = rows[start:start + BATCH]
        result.append(f"python3 read_csv.py --start {start + 1} --count {BATCH}")
        objects = ",\n".join(typed_record(row) for row in batch)
        result.append(f"python3 write_json.py <<'JSON'\n[\n{objects}\n]\nJSON\npython3 progress.py")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workdir", type=Path, default=Path("/workdir"))
    parser.add_argument("--transcript", type=Path, help="also write the host-style transcript here")
    args = parser.parse_args()
    issued = commands((args.workdir / "input.csv").read_text(encoding="utf-8"))
    # Offline authoring utility: actual helper subprocess outcomes and file
    # digests. This runs in a controlled fixture, not an adversarial sandbox;
    # the live adapter independently supplies protected host receipts.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from provenance import parse_command
    transcript = {"version": 2, "shell_commands": issued, "events": [],
                  "stop_reason": "environment_terminal", "finish_summary": "Converted all records."}
    scripts = {"read": "read_csv.py", "write": "write_json.py", "progress": "progress.py"}
    def digest():
        return hashlib.sha256((args.workdir / "output.json").read_bytes()).hexdigest()
    for index, command in enumerate(issued):
        for action in parse_command(command):
            before = digest()
            outcome = subprocess.run([sys.executable, str(args.workdir / scripts[action["kind"]]), *action["argv"]],
                                     input=action["stdin"], text=True, cwd=args.workdir, check=True,
                                     stdout=subprocess.DEVNULL)
            transcript["events"].append({"command_index": index, "action": action,
                "status": "completed", "returncode": outcome.returncode, "timed_out": False,
                "before_sha256": before, "after_sha256": digest(),
                "helper_verified": True, "source_verified": True})
    transcript["final_sha256"] = digest()
    if args.transcript:
        args.transcript.write_text(json.dumps(transcript), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
