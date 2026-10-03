# Obsolete evidence cleanup — 2026-10-02

This is the state at the time of cleanup. The seven then-pending rollouts have
since finished; the current [evidence index](README.md) records all ten retained
hardened-condition rollouts. The deletion inventory and validation below are unchanged.

The user requested removal of the old ten-rollout evidence because the active
setup uses hardened provenance instrumentation. Deleted:

- `evidence/runs-01-03.eval`
- `evidence/runs-04-10.eval`
- `evidence/conditions/original-ten-rollouts/`, including its source archive,
  prompt, manifest, settings and extracted per-rollout outcomes
- `evidence/audit/withdrawn-execution-v2/evidence/README.md`, an obsolete index
  consisting solely of those ten results

No copies of the two old paid-rollout logs were found under `logs/`. Retained:

- The completed `confirmation-3-20261002T192909Z` batch, source snapshot,
  complete Inspect conversation, returned reasoning summaries, host witnesses,
  scores, root-level trajectories and provider exports.
- Offline e2e logs and synthetic regression evidence. Four vulnerable grader
  source files now live in `audit/fixtures/text-only-grader/` exclusively to
  reproduce the synthetic comment-laundering defect. They contain no model-rollout
  evidence or original experiment configuration.
- The withdrawn implementation and its offline validation history.

Active documentation and `qc.json` now describe only the retained three-rollout
finding: two scripted completions and one manual partial completion. Seven
newly requested rollouts are pending and are not included in that count. The
qc thresholds are unchanged. Runtime files, prompt, helpers and model settings
were not edited during cleanup.

The retained three-rollout snapshot, report, summary, launch record and artifact
manifest remain byte-for-byte intact. They include historical references to
old results and hashes; these frozen metadata are not current evidence for
the removed experiments. The batch exporter no longer requires the deleted
logs. Immutable source snapshots and withdrawn historical documents may mention
removed locations; active documentation and tests do not depend on them.

Cleanup validation:

- `.venv/bin/python -B -m pytest tests -q -p no:cacheprovider`:
  **156 passed, 12 Docker tests skipped** in 21.77 seconds.
- The self-contained offline reproduction still scores conversion alone at 0
  and conversion plus 997 unused comment commands at 1 under the vulnerable
  fixture. Its generated report exactly matches `audit/comment-laundering.json`.
  The current grader's rejection regression passes in the suite above.
- All retained three-rollout evidence and root export hashes match their existing
  artifact manifest. Prompt, helper, input-generation and runtime snapshot
  consistency checks pass.
- Deleted paths are absent; hashing all remaining `.eval` files in `evidence/`
  and `logs/` found no duplicate old logs. `git diff --check` passes.

Docker e2e tests were not rerun for this documentation, fixture and test cleanup;
no runtime code or agent-visible instruction changed.
