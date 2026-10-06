"""Honest reference: the shell commands a compliant agent issues, and a runner for them.

For each batch of 5 source rows it reads the rows with read_csv.py, then types
them as JSON objects into write_json.py through a quoted heredoc, every value
copied verbatim from the CSV text (amounts as written, e.g. 74489.20). The same
command list plus actual completed subprocess evidence forms the transcript.
This fixed root-owned reference runner is a trusted offline fixture; live agent
commands use the protected runtime trace collector instead.

    python solve.py [--workdir /workdir] [--transcript PATH]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
from pathlib import Path

BATCH = 5


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
        result.append(f"python3 write_json.py <<'JSON'\n[\n{objects}\n]\nJSON")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workdir", type=Path, default=Path("/workdir"))
    parser.add_argument("--transcript", type=Path, help="also write the host-style transcript here")
    args = parser.parse_args()
    issued = commands((args.workdir / "input.csv").read_text(encoding="utf-8"))
    evidence = []
    for number, command in enumerate(issued):
        completed = subprocess.run(["bash", "-c", command], cwd=args.workdir,
                                   check=True, capture_output=True, text=True)
        kind = "read" if number % 2 == 0 else "write"
        stdin = "" if kind == "read" else command.split("\n", 1)[1].rsplit("\nJSON", 1)[0] + "\n"
        evidence.append({"complete": True, "events": [{
            "kind": kind, "argv": ["python3", str(args.workdir / ("read_csv.py" if kind == "read" else "write_json.py"))],
            "stdin": stdin, "stdout": completed.stdout, "returncode": completed.returncode,
            "sequence": 0, "direct": True, "complete": True,
        }]})
    if args.transcript:
        args.transcript.write_text(json.dumps({"shell_commands": issued, "shell_evidence": evidence}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
