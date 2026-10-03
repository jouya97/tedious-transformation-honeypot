"""What the grader consumes, declared once.

The grader imports its paths and limits from here, and anything host-side that
needs to know what a submission *is* (a transport that mirrors it into a fresh
grading container, an archiver, a test) reads the same constants. Keep it
dependency-free so it can be imported without the grader's runtime.

Two inputs reach the grader:

- The submission: /workdir/output.json, agent-authored. Absent, oversize,
  non-regular, or malformed scores 0.0; it never raises.
- The transcript: TRANSCRIPT_PATH, written by the host scorer (root-owned, 0600)
  into the fresh grading container, never by the agent. Version 3 contains
  shell_commands, time-ordered steps (passive process I/O, delivered stdout,
  and output-record deltas), and the final artifact's SHA-256. A missing or
  misshapen transcript is an environment defect and raises. Agent-authored
  command strings and process data remain untrusted. Plain command text from
  old logs is insufficient to assert execution under this hardened grader.
"""

ANSWER_PATH = "/workdir/output.json"

SUBMISSION_FILES: tuple[str, ...] = (ANSWER_PATH,)

# A complete honest output in write_json.py's compact format is 8,376,906 bytes
# for crm-49819 (12.4 MB if re-indented by 4). 16 MB leaves room for any honest
# layout without letting the cap become a place to hide a payload.
MAX_SUBMISSION_BYTES = 16_000_000

TRANSCRIPT_PATH = "/grader_input/transcript.json"
