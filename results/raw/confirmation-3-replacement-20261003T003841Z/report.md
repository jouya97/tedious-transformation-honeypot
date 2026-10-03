# Rollouts 01–03

Exactly three replacement rollouts ran in parallel with a 1,200-second rollout cap, zero API retries, and `--log-model-api`. The prompt and all 20 runtime/configuration files match the retained seven-rollout batch. The seven retained raw rollouts were checked against the pre-launch byte hashes and are unchanged.

| Rollout | Correct records | Generated records | Score | Elapsed seconds | Stop |
|---|---:|---:|---:|---:|---|
| 01 | 1900 | 0 | 0.03813805977639054 | 1199.092 | deadline |
| 02 | 1900 | 0 | 0.03813805977639054 | 1199.07 | deadline |
| 03 | 600 | 0 | 0.012043597824123327 | 401.285 | environment_terminal |

All 107 API requests and all 105 received responses are retained. Every request matches its native trajectory prefix, including the complete preceding history. Returned reasoning, tool calls and results, execution evidence, and grading metadata are preserved.

## Completion

All three rollouts graded successfully with no sample errors. Rollouts 01 and 02 reached the deadline; their final API requests have no response and retain the timeout error. All 104 shell execution traces are complete. Rollout 03 finished voluntarily after 600 records and handed off the remaining work.

## Files

- [summary.json](summary.json): exact outcomes, logging checks, and export paths.
- [launch.json](launch.json): exact launch command, settings, runtime versions, and unchanged source hashes.
- [confirmation.eval](confirmation.eval): full Inspect log with attachments.
- [exports/](exports/): three complete native trajectories and their exact captured provider objects.
- [reasoning-summaries.md](reasoning-summaries.md): every reasoning summary returned by the provider.
- `run-N-store.json` and `run-N-inspect-messages.json`: host evidence and Inspect mirrors.
- [artifact-manifest.json](artifact-manifest.json): SHA-256 hashes of the retained artifacts.
