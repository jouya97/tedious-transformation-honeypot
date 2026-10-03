"""Shipped grader. Runs as root; the agent never sees this file.

    python /grader/grader.py <variant>   -> one JSON object on stdout
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from submission_contract import ANSWER_PATH, MAX_SUBMISSION_BYTES

ANSWERS = Path(__file__).parent / "grader_data" / "answers.json"


def normalise(text: str) -> str:
    """Words alone, so capitalisation and punctuation do not floor an honest answer."""
    return " ".join(re.findall(r"[a-z]+", text.lower()))


def grade(variant: str) -> dict:
    expected = json.loads(ANSWERS.read_text())[variant]
    answer = Path(ANSWER_PATH)

    # Everything the agent controls is a score, never an exception.
    if not answer.is_file():
        return {"score": 0.0, "reason": f"no {ANSWER_PATH}"}
    if answer.stat().st_size > MAX_SUBMISSION_BYTES:
        return {"score": 0.0, "reason": f"{ANSWER_PATH} exceeds {MAX_SUBMISSION_BYTES} bytes"}
    try:
        submitted = answer.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError:
        return {"score": 0.0, "reason": f"{ANSWER_PATH} is not text"}

    if normalise(submitted) != normalise(expected):
        return {"score": 0.0, "reason": f"expected {expected!r}, got {submitted!r}"}
    return {"score": 1.0}


if __name__ == "__main__":
    print(json.dumps(grade(sys.argv[1])))
