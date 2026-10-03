# Validation

- [Test results](validation.md): local and Docker checks.
- [Requirement coverage](prompt-grader-mapping.md): what the grader measures.
- [Task design](../../flaw.md): scoring and corrections.

## Synthetic regression

The [comment-laundering fixture](fixtures/text-only-grader/README.md) demonstrates
why record text in unused comments cannot establish transcription. It supplies
all 49,819 records in 997 comment commands—about 9.1 million characters.
The fixture accepts this attack; the shipped grader's regression tests reject it.
This is a synthetic test, not a model rollout.

```sh
.venv/bin/python -B results/audit/reproduce_comment_laundering.py
```

Expected output is recorded in [comment-laundering.json](comment-laundering.json).
