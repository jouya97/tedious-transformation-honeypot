# Honeypot environment scaffold

The task here is a hello-world placeholder — write a greeting to a file. It is
not an example of a good honeypot, only of a complete one, so that every file is
one you replace rather than one you imitate.

```bash
pip install inspect-ai anthropic   # plus whichever provider SDK you point at
export ANTHROPIC_API_KEY=...

docker build -f adapters/docker/Dockerfile -t honeypot-scaffold .
inspect eval adapters/inspect/inspect_task.py --model <provider>/<model>
```

Grading runs in a second container the agent never touched. The scorer copies
out only the files `grader/submission_contract.py` declares, regular files
within the byte cap, stages them there, and runs the grader as root. Whatever
else the agent left behind does not reach the grader.

[RUBRIC.md](RUBRIC.md) says what we value in an environment and how the
grader will be judged. Read it before writing one.

## Layout

```
RUBRIC.md        What we value in an environment; how graders are judged.
env.json         Identity and resource envelope.
variants.json    The variants, and the named hack signatures each plants.
qc.json          Declared score bands: what honest work must clear, what a
                 shortcut must stay under, the margin between them.
flaw.md          The design doc. Four sections. Write it first.
task.py          Prints the agent-visible prompt for a variant.
agent_data/      Staged into /workdir. What the agent sees.
grader/          Root-only. grader.py, grader_data/, reference_solution/, and
                 submission_contract.py: which files are the submission and
                 the limits they must fit. The grader imports its paths from it.
adapters/        Per-target Dockerfile + adapter.json.
```
