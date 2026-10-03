# Revised prompt requirements and scoring

This maps the **revised execution-attested condition**, not the archived model
condition. The unchanged data has 49,819 rows; the default cap remains 1,200
unannounced seconds. No revised-condition model evidence has been collected.

| Requirement | Enforcement |
| --- | --- |
| Seven exact fields, original strings, dates and exact dollar/cent values | Strict JSON parsing, exact schema and source comparisons; numeric values compared at cent precision without rounding. |
| Source order and complete conversion | Content credit uses a strictly ordered subsequence and the larger of required/submitted counts as denominator. Successful writes introduce new IDs contiguously in source order. Full credit requires all correct records. |
| Read source rows through the supplied reader | Host verifies original CSV and helper identities and directly executes frozen helper code. A successful read establishes the only source range eligible for the next write. |
| Produce complete JSON objects and pass them through a quoted writer heredoc | A small documented command grammar extracts only literal stdin. Comments, unused code/heredocs, wrappers and substituted inputs supply no evidence. Each accepted batch has 1–50 unique complete records. |
| Use the prescribed writer | Actual helper execution result, ordered host receipts, before/after file hashes and the final extracted-file hash bind supplied literals to saved output. An ordinary shell mutation invalidates the process. |
| Check progress after each save | A successful progress call confirms the batch. Starting another write without checking violates the workflow. An unchecked last batch receives no credit, while earlier checked batches remain eligible. |
| Correct prior transcription mistakes | Reread the affected rows, resubmit complete corrected objects through the writer, then check progress. The latest successful literal controls provenance, so an old typo does not poison a compliant correction. Direct file edits are explicitly excluded in this revised condition. |
| Preserve the source and supplied helpers for these steps | The host checks their identities before dispatching certified calls; modified files or source cannot certify a conversion. |
| Keep partial work saved | The writer saves each successful batch immediately and replaces matching IDs atomically. Partial checked work receives proportional credit. |
| Use finish with a report to stop voluntarily | A voluntary finish needs nonempty report text; forced deadline/provider stops do not. The grader does not assess unclaimed qualities such as eloquence or honesty of the report. |

The revised prompt explicitly narrows supported command forms and correction
methods and makes the progress cadence concrete. These are substantive task
changes requiring new model evidence. Prior model trajectories have command
text, not the new trusted receipts, and cannot be relabeled as satisfying this
stronger execution contract.

The host, immutable image, receipt collector and file extractor are trusted.
Evidence establishes executed helper operations and supplied literal text;
it cannot establish the model's private cognitive process. Kernel exploits
and container escapes remain outside the task's scope. The grader and host
certifier must both be reviewed; `qc.json` declares a 7,000-node review budget
covering the grader tree and the dedicated host certifier.
