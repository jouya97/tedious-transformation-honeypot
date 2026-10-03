"""Make the shipped grader, the reference solution, and task.py importable."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "grader", ROOT / "grader" / "grader_data" / "reference_solution"):
    sys.path.insert(0, str(path))
