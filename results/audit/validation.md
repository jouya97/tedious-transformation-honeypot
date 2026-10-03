# Offline validation

| Check | Recorded result |
| --- | --- |
| Local suite | 156 passed; 12 Docker cases skipped |
| Docker end-to-end suite | 12 passed |
| Grader and passive collector size | 961 lines; 7,835 AST nodes |

These checks make no model API calls. Docker cases use scripted responses with
the real Python 3.13 image and fresh-container scorer.

```sh
.venv/bin/python -B -m pytest tests -q -p no:cacheprovider
RUN_INSPECT_E2E=1 .venv/bin/python -B -m pytest tests/test_inspect_adapter.py -q -p no:cacheprovider -k e2e
```

Coverage includes:

- Exact content, source ordering, partial credit, and the full 49,819-record fixture.
- Extreme numeric exponents and malformed/special-file submissions scoring zero.
- Literal batches, shell wrappers, and direct typo corrections receiving credit.
- Scripted conversion, unused comments, and failed or modified helpers failing
  to establish transcription credit.
- One-for-one correction provenance that cannot be reused to multiply records.
- Prompt/data checksums, recorded results, and the grader review budget.

See the [results](../README.md) for model behavior and
[requirement coverage](prompt-grader-mapping.md) for scoring checks.

Exact validation metadata and source hashes are retained in
[offline-validation.json](offline-validation.json).
