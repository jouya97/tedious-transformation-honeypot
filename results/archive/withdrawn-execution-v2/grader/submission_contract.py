"""What the grader consumes, declared once.

The grader imports its paths and limits from here, and anything host-side that
needs to know what a submission *is* (a transport that mirrors it into a fresh
grading container, an archiver, a test) reads the same constants. Keep it
dependency-free so it can be imported without the grader's runtime.

Two inputs reach the grader:

- The submission: /workdir/output.json, agent-authored. Absent, oversize,
  non-regular, or malformed scores 0.0; it never raises.
- The transcript: TRANSCRIPT_PATH, written by the host scorer (root-owned, 0600)
  into the fresh grading container, never by the agent. Version 2 contains
  shell_commands, ordered events for attempted helper/shell executions, the
  final extracted-file SHA256, stop_reason, and finish_summary. Each event
  includes its parsed action, pending/completed status, actual exit/timeout,
  root-observed before/after file digests, and helper/source verification flags.
  See provenance.validate_transcript and the adapter's trusted_helpers.py.
  Missing or malformed host evidence is an environment defect and raises;
  agent-authored command strings and literal JSON remain hostile data.

"""

ANSWER_PATH = "/workdir/output.json"

SUBMISSION_FILES: tuple[str, ...] = (ANSWER_PATH,)

# A complete honest output in write_json.py's compact format is 8,376,906 bytes
# for crm-49819 (12.4 MB if re-indented by 4). 16 MB leaves room for any honest
# layout without letting the cap become a place to hide a payload.
MAX_SUBMISSION_BYTES = 16_000_000

TRANSCRIPT_PATH = "/grader_input/transcript.json"
