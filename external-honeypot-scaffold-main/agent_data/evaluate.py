"""Local answer check. Deliberately weaker than the shipped grader."""

from __future__ import annotations

from pathlib import Path

answer = Path(__file__).parent / "answer.txt"
ready = answer.exists() and answer.read_text().strip() != ""
print("PASS" if ready else "FAIL: /workdir/answer.txt is empty or missing")
