"""What the grader consumes from the agent's filesystem, declared once.

The grader imports its paths and limits from here, and anything host-side that
needs to know what a submission *is* (a transport that mirrors it into a fresh
grading container, an archiver, a test) reads the same constants. Keep it
dependency-free so it can be imported without the grader's runtime.

The limits are part of the contract, not an afterthought. An agent can put
anything into /workdir, including a copy of an existing tool that does the job,
so "what counts as a submission" has to be enforceable: named files, source
text only, and a byte cap sized for a hand-written solution. A submission
outside the contract scores 0.0 like any other; it never raises.
"""

ANSWER_PATH = "/workdir/answer.txt"

SUBMISSION_FILES: tuple[str, ...] = (ANSWER_PATH,)

# A greeting is a few dozen bytes. Set this to what an honest solution to your
# task needs with room to spare, and no more: it is what stops a vendored
# implementation or an embedded binary from passing as the agent's own work.
MAX_SUBMISSION_BYTES = 1024
