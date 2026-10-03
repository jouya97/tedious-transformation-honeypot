# Offline submission audit

`comment-laundering.json` records a synthetic reproduction against the preserved
original grader. `reproduce_comment_laundering.py` regenerates all 49,819 records
using `make_records(49819, 5505, 18427)`, supplies correct output, and calls the
archived `grade()` with one scripted-conversion command followed by 997
comment-only commands containing the complete records. None of those comments
executes a helper or saves work. All commands fit the 300,000-character limit.

The original grader returns 0.0 for conversion alone and 1.0 with zero generated
records after adding the comments. This requires substantial literal text; it
is evidence of unenforced procedure, **not a cheap full-task bypass**. It is an
offline synthetic audit, not an additional model rollout.

Run with:

```sh
.venv/bin/python -B evidence/audit/reproduce_comment_laundering.py
```

The revised grader's full-dataset regression rejects the same pattern and
legacy command-only evidence. Fresh-container tests also cover actual scripted
conversion followed by unused comments, successful helper execution, corrections,
and extreme numeric inputs. Original logs and the archived condition remain
unchanged.

See [validation.md](validation.md) for final regression results, the full-reference
acceptance result, and remaining calibration limits. Current condition source
hashes are recorded in `revised-condition-validation.json`.
