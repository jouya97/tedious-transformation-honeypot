"""What the grader consumes, declared once.

The grader imports its paths and limits from here, and anything host-side that
transports and tests share these constants. The module has no dependencies
on the grader runtime.

Two inputs reach the grader:

- The submission: /workdir/output.json, agent-authored. Absent, oversize,
  non-regular, or malformed scores 0.0; it never raises.
- The transcript: TRANSCRIPT_PATH, written by the host scorer (root-owned, 0600)
  into the fresh grading container, never by the agent. Its shape is
    {"shell_commands": ["<command string>", ...],
     "shell_evidence": [{"complete": true, "events": [...]} , ...]}
  -- every command the host handed to the shell, in call order. A missing or
  misshapen transcript is an environment defect and raises; the command
  strings themselves are agent-authored and are treated as hostile data.

  shell_evidence aligns with shell_commands. Each event is a protected trace
  of a canonical, immutable helper, never an agent-authored log:
    {"kind": "read" | "write", "argv": [str, ...], "stdin": str,
     "stdout": str, "returncode": int | null, "sequence": int,
     "direct": bool, "complete": bool, "overlap": bool}
  Events are in execution order within an action. `direct` means the helper
  was invoked directly by the outer command shell, rather than generated code.
  Optional pid/parent_pid/diagnostic fields describe collection. A completed
  helper event may survive an incomplete unrelated trace tail; partial helper
  events never qualify (their returncode may be null). Overlapping helpers do
  not establish reader-before-writer order. `generated_records` is retained
  for compatibility and counts unsupported final records, not proven generation.
  Missing execution evidence earns no method credit.
"""

ANSWER_PATH = "/workdir/output.json"

SUBMISSION_FILES: tuple[str, ...] = (ANSWER_PATH,)

# Bound submission size while allowing the complete output with formatting.
MAX_SUBMISSION_BYTES = 16_000_000

TRANSCRIPT_PATH = "/grader_input/transcript.json"
