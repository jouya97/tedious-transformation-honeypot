# Comment-laundering regression fixture

These four source files reproduce a vulnerable command-text provenance check.
They contain no model results and are not shipped into the runtime container.

Run `.venv/bin/python -B results/audit/reproduce_comment_laundering.py` from the
repository root. The attack requires every complete record in unused comments;
it demonstrates missing process enforcement, not a cheap conversion shortcut.
