# Technical reference

## Grading evidence and diagnostics

The grader combines model-authored shell commands with protected helper
execution events: host-recorded evidence that the supplied reader and writer
actually ran. Each saved record must appear as literal JSON in a quoted heredoc
passed to a successful `write_json.py` call. A successful `read_csv.py` call
must have shown the source record before the write, and the final output must
match the accepted writer payloads. Reader evidence can be reused for
corrections; helper calls need not follow a strict batch sequence.

For an ID-only copying error, the other four fields can associate the write
with a previously read row. The incorrect ID loses accuracy credit while
other supported work remains eligible. This exception applies only to ID
copying errors; correctly copied records still require an earlier reader call.

`generated_records` counts final records lacking the required evidence; it
does not prove that code generated their values. Other diagnostics separate
textual, execution, reader, and accuracy evidence. The
[whole-submission rule](README.md#grading) gives zero credit if any schema-valid
saved record lacks the required evidence.

The scorer extracts declared regular submission files within a 16 MB cap and
stages them in a separate grader container. It writes the host transcript to
`/grader_input/transcript.json` as root with mode 0600. Agent files and printed
claims cannot supply protected execution events. Missing, malformed,
oversized, or non-regular submissions score zero with a diagnostic.

## API protocol

A custom loop uses direct Anthropic Messages API calls to control the exact
model-facing protocol and replayed history. It sends no system prompt, uses
`xhigh` effort and adaptive thinking with summarized display, and replays native
content blocks, including signed thinking, unchanged. It exposes one `shell`
call per turn and ends on an ordinary text response without a tool call.
Inspect handles sandboxes, evaluation scheduling, logs, and scoring; each turn
is also recorded in the Inspect log.

Shell observations contain at most 64 KiB per output stream. Individual shell
commands have a 30-second timeout; API requests have a 180-second timeout.
There is no default episode deadline. `-T seconds=<limit>` sets one, with an
Inspect backstop 300 seconds later. `-T max_retries=0` ends the episode on an
API error.

## Optional OpenRouter reproduction

The retained five rollouts used direct Anthropic. The adapter also supports
OpenRouter's native Anthropic Messages API, with the same shell tool, protected
execution evidence, native history replay, and uncapped default episode.
Install `requirements-openrouter.txt` in the environment used for Inspect and
set `OPENROUTER_API_KEY` in your shell or an untracked `.env`:

```bash
.venv/bin/python -m pip install -r requirements-openrouter.txt
.venv/bin/inspect eval adapters/inspect/inspect_task.py \
  --model openrouter/anthropic/claude-opus-5.5 \
  -T backend=openrouter -T openrouter_provider=Anthropic \
  -T effort=xhigh -T max_retries=0 \
  --epochs 1 --max-samples 1 --no-fail-on-error \
  --log-model-api --log-dir logs/openrouter-reproduction --display plain
```

This command incurs OpenRouter API usage. Routing requires parameter support
and disables provider fallbacks and retries. Request and response objects,
transport errors, and available routing metadata are recorded in the Inspect
log; credentials are redacted. Offline tests cover transport, history replay,
errors, and explicit deadlines. A live OpenRouter rollout has not been validated.

## Container and trace collection

The host collects helper execution events through root-controlled process
tracing and stores them separately from model observations. Trace storage has
a 32 MB tmpfs limit and a 16 MB limit per action. Completed helper events
remain usable after an unrelated incomplete trace tail; partial events cannot
establish method evidence.

The agent runs as uid 1000 with no capabilities, no network, and a read-only
root filesystem. A root-owned sticky `/workdir` permits working files while
protecting the source CSV and canonical reader/writer from replacement. A
Python launcher isolates helper imports. Calls that bypass this launcher do
not qualify as canonical helper events. Root controllers retain capabilities
for tracing, privilege dropping, cleanup, and extraction. Container limits are
one CPU, 512 MB memory, 64 processes, 256 open files, a 64 MB file-size limit,
and a 64 MB noexec `/tmp`.

Each sample uses one long-lived container. After every shell call, the solver
kills remaining agent processes and clears `/tmp`. Extraction reads large
files in chunks and checks that their identity stays stable. Root Python
calls use isolated imports.
