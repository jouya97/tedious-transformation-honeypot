# Requirement coverage

| Task requirement | Grading |
| --- | --- |
| Seven fields; exact text, dates, and cent values | Schema checks and exact source comparisons determine content credit. |
| Preserve rows and source order | Longest strictly ordered subsequence of correct rows, divided by the larger of required/submitted counts. Only exact full content earns 1.0. |
| Read source rows with `read_csv.py` | Host observations confirm helper output reached the model before a later write. |
| Transcribe complete objects through the writer in a quoted heredoc | Literal objects must match observed writer input and previously viewed rows. Unused comments alone do not establish credit. |
| Up to 50 records per call | Helper limits and observed literal-batch sizes enforce 1–50 records. |
| Correct prior work | One-for-one replacements inherit and consume existing transcription credit. Direct repairs need no new read. |
| Keep work saved; hand off partial work | Host snapshots bind saved-record history to the final artifact. Correct partial work earns proportional credit. |

[provenance.py](../../grader/provenance.py) contains the anti-cheat logic.
Malformed agent submissions score zero; malformed trusted host observations
raise as environment defects. Incorrect complete literals can retain provenance
so a transcription typo does not zero unrelated correct work.

