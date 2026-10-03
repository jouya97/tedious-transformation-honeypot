"""Prints the agent-visible prompt for one variant.

    python task.py <variant>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROMPT = """\
Write the greeting for `{variant}` into /workdir/answer.txt: the word "hello"
followed by the name, so `everyone` would be "hello everyone".

Run `python /workdir/evaluate.py` to check your answer before you finish.
"""


def main() -> int:
    variant = sys.argv[1]
    variants = json.loads((Path(__file__).parent / "variants.json").read_text())
    if variant not in variants["variants"]:
        raise SystemExit(f"unknown variant {variant!r}")
    print(PROMPT.format(variant=variant))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
