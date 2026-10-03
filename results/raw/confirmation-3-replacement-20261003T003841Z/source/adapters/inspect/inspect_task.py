"""Inspect adapter.

    inspect eval adapters/inspect/inspect_task.py --model anthropic/claude-opus-5-5

Two containers per sample, both from the same image. The agent works in
`default` as uid 1000. Grading happens in `grader`, a container the agent has
never touched: after the episode the scorer copies out exactly the files
grader/submission_contract.py declares -- regular files only, no symlinks,
within the byte cap -- writes them into the fresh container's /workdir as the
agent user, and runs the grader there as root. Anything the agent left outside
the contract never reaches the grader.

One deliberate extension to the scaffold: the grader also receives the shell
commands the model issued, in order, at TRANSCRIPT_PATH (root-owned, 0600, in a
0700 directory) as a versioned transcript of commands, passive execution
evidence, output deltas and the final artifact hash. Commands are the host's own record
of the model's tool calls, kept by the solver as it executes them (state.store);
nothing in it is read from the agent's container, so the agent cannot shape it
except by what it actually runs. Commands the host rejected without running
them (over 300000 characters, NUL bytes) are not in it.

The solver is not basic_agent. It is a custom loop (see harness_agent.py):
direct Anthropic Messages calls with a fixed payload, native history replay, shell/finish tools, and a 1200 s wall clock
that is never announced. Inspect's own Anthropic provider cannot send that
payload unchanged (it adds betas and a thinking block_binding, omits
tool_choice when thinking, so parallel tool use cannot be disabled, and
re-serialises tool results and assistant blocks), so the solver calls the API
itself and mirrors each turn into state.messages and a ModelEvent for the log.
The model is `--model anthropic/<id>`; with any other --model (e.g. mockllm for
offline runs) it is `-T model=`, else ANTHROPIC_MODEL, else claude-opus-5-5.

When the deadline passes the solver stops and the sample is scored as it
stands: partial output is graded. Task time_limit is only a backstop for a hung
call; Inspect scores a sample that hits it too.

A submission the contract withholds (absent, a symlink, oversize) arrives as a
missing file, and the grader scores that 0.0 by its own rule. A failure of the
plumbing itself, or of the grader, is an environment defect and raises so the
sample errors, rather than being recorded as a score the agent earned.
"""

from __future__ import annotations

import base64
import importlib.util
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.event import ModelEvent
from inspect_ai.log import transcript
from inspect_ai.model import (
    ChatCompletionChoice, ChatMessageAssistant, ChatMessageTool, ChatMessageUser,
    ContentReasoning, ContentText, GenerateConfig, ModelCall, ModelOutput, ModelUsage, get_model,
)
from inspect_ai.model._model import record_and_check_model_usage
from inspect_ai.scorer import Score, Target, mean, scorer, stderr
from inspect_ai.solver import Generate, TaskState, solver
from inspect_ai.tool import ToolCall, ToolInfo, ToolParam, ToolParams
from inspect_ai.util import sandbox

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness_agent as harness  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "grader" / "submission_contract.py"
AGENT_USER = harness.AGENT_USER
DEFAULT_MODEL = "claude-opus-5-5"
EPISODE_SECONDS = 1200
BACKSTOP_SECONDS = 300

# Runs as root in the agent's container. Opens the declared path with
# O_NOFOLLOW and checks the open descriptor, so a file swapped for a symlink
# between two calls cannot smuggle in something the contract excludes. Inspect
# caps exec output at 10 MiB, and an honest output.json is 8.4 MB (11 MB as
# base64), so the file comes out in chunks; every chunk re-checks the file's
# identity against the first, and a file that changed is withheld.
EXTRACT_SCRIPT = r"""
import base64, json, os, stat, sys
req = json.load(sys.stdin)
try:
    fd = os.open(req["path"], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
except FileNotFoundError:
    json.dump({"withheld": "absent"}, sys.stdout)
    sys.exit(0)
except OSError as exc:
    json.dump({"withheld": f"not a regular file ({exc.strerror})"}, sys.stdout)
    sys.exit(0)
try:
    st = os.fstat(fd)
    identity = [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns]
    if not stat.S_ISREG(st.st_mode):
        out = {"withheld": "not a regular file"}
    elif st.st_size > req["max_bytes"]:
        out = {"withheld": f"{st.st_size} bytes exceeds the {req['max_bytes']}-byte cap"}
    elif req["identity"] is not None and identity != req["identity"]:
        out = {"withheld": "changed while it was being extracted"}
    else:
        chunk = os.pread(fd, req["length"], req["offset"])
        out = {"identity": identity, "size": st.st_size, "b64": base64.b64encode(chunk).decode()}
finally:
    os.close(fd)
json.dump(out, sys.stdout)
"""
EXTRACT_CHUNK_BYTES = 6 * 1024 * 1024

# Runs as root in the fresh grader container: materialises the mirrored files
# owned by the agent user, exactly where the contract says they live, and the
# host-recorded transcript where only root can read it.
STAGE_SCRIPT = r"""
import base64, json, os, shutil, sys
req = json.load(sys.stdin)
for path in req["withheld"]:
    # The image seeds output.json in both containers; a withheld file must
    # reach the grader as absent, not as that seed.
    if os.path.lexists(path):
        os.unlink(path)
for path, b64 in req["files"].items():
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(base64.b64decode(b64))
    shutil.chown(path, req["user"], req["user"])
path = req["transcript_path"]
os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
os.chown(os.path.dirname(path), 0, 0)
os.chmod(os.path.dirname(path), 0o700)
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(req["transcript"], f, ensure_ascii=False)
os.chown(path, 0, 0)
os.chmod(path, 0o600)
"""


def load_contract() -> tuple[tuple[str, ...], int, str]:
    """The declared submission files, byte cap and transcript path, read from the env source."""
    spec = importlib.util.spec_from_file_location("submission_contract", CONTRACT_PATH)
    assert spec is not None and spec.loader is not None, CONTRACT_PATH
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return (tuple(module.SUBMISSION_FILES), int(module.MAX_SUBMISSION_BYTES),
            str(module.TRANSCRIPT_PATH))


def load_client(client: str | Callable[[], harness.Client] | None, max_retries: int) -> harness.Client:
    """The live Anthropic client, or a test double named `file.py:factory` / `module:factory`."""
    if client is None:
        return harness.anthropic_client(max_retries=max_retries)
    if callable(client):
        return client()
    location, _, name = client.partition(":")
    if location.endswith(".py"):
        path = Path(location) if Path(location).is_absolute() else ROOT / location
        spec = importlib.util.spec_from_file_location(path.stem, path)
        assert spec is not None and spec.loader is not None, path
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(location)
    return getattr(module, name)()


def resolve_model(active: Any, explicit: str | None) -> str:
    """`--model anthropic/<id>` names the model; other providers defer to -T model / env."""
    from_cli = active.name if active is not None and active.api == "anthropic" else None
    if explicit and from_cli and explicit != from_cli:
        raise ValueError(f"-T model={explicit} conflicts with --model anthropic/{from_cli}")
    return explicit or from_cli or os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL


TOOL_INFOS = [
    ToolInfo(name=tool["name"], description=tool["description"], parameters=ToolParams(
        properties={key: ToolParam(type="string") for key in tool["input_schema"]["properties"]},
        required=list(tool["input_schema"]["required"])))
    for tool in harness.TOOLS
]
STOP_REASONS = {"end_turn": "stop", "tool_use": "tool_calls", "max_tokens": "max_tokens",
                "refusal": "content_filter"}


def assistant_message(content: list[dict], model: str) -> ChatMessageAssistant:
    """A readable copy of one native reply. The native blocks themselves are what gets replayed."""
    parts: list[Any] = []
    calls = []
    for block in content:
        kind = block.get("type")
        if kind == "thinking":
            parts.append(ContentReasoning(reasoning=str(block.get("thinking", "")),
                                          signature=block.get("signature")))
        elif kind == "redacted_thinking":
            parts.append(ContentReasoning(reasoning=str(block.get("data", "")), redacted=True))
        elif kind == "text":
            parts.append(ContentText(text=str(block.get("text", ""))))
        elif kind == "tool_use":
            arguments = block.get("input")
            calls.append(ToolCall(id=str(block.get("id")), function=str(block.get("name")),
                                  arguments=arguments if isinstance(arguments, dict) else {}))
        else:
            parts.append(ContentText(text=harness._json(block)))
    return ChatMessageAssistant(content=parts, tool_calls=calls or None, model=model, source="generate")


class InspectMirror(harness.Hooks):
    """Keeps state.messages, state.store and the event log in step with the episode."""

    def __init__(self, state: TaskState):
        self.state = state

    def _store(self, episode: harness.Episode) -> None:
        store = self.state.store
        store.set("shell_commands", list(episode.shell_commands))
        store.set("steps", list(episode.steps))
        store.set("tool_calls", episode.tool_calls)
        store.set("finish_summary", episode.finish_summary)
        store.set("provider_calls", [dict(call) for call in episode.calls])
        store.set("model", episode.model)

    def started(self, episode: harness.Episode) -> None:
        texts = [ContentText(text=block["text"]) for block in episode.messages[0]["content"]]
        self.state.messages = [ChatMessageUser(content=texts)]
        self._store(episode)

    def command_recorded(self, episode: harness.Episode) -> None:
        self._store(episode)

    def model_call(self, episode, payload, response, trace, error) -> None:
        model = f"anthropic/{episode.model}"
        prior = list(self.state.messages)
        usage = None
        if response is not None:
            message = assistant_message(response.get("content") or [], episode.model)
            self.state.messages.append(message)
            raw = response.get("usage") or {}
            usage = ModelUsage(
                input_tokens=raw.get("input_tokens") or 0, output_tokens=raw.get("output_tokens") or 0,
                input_tokens_cache_write=raw.get("cache_creation_input_tokens"),
                input_tokens_cache_read=raw.get("cache_read_input_tokens"))
            usage.total_tokens = (usage.input_tokens + usage.output_tokens
                                  + (usage.input_tokens_cache_write or 0) + (usage.input_tokens_cache_read or 0))
            record_and_check_model_usage(get_model(), usage)
            output = ModelOutput(model=model, usage=usage, choices=[ChatCompletionChoice(
                message=message, stop_reason=STOP_REASONS.get(response.get("stop_reason"), "unknown"))])
        else:
            output = ModelOutput.from_content(model=model, content="", stop_reason="unknown", error=error)
        transcript()._event(ModelEvent(
            model=model, input=prior, tools=TOOL_INFOS, tool_choice="auto",
            config=GenerateConfig(max_tokens=harness.MAX_TOKENS, effort=harness.REASONING_EFFORT,
                                  parallel_tool_calls=False),
            output=output, error=error,
            call=ModelCall.create(request=payload, response=response, time=trace.get("elapsed_seconds")),
            completed=datetime.now(timezone.utc), working_time=trace.get("elapsed_seconds"),
        ))
        self._store(episode)

    def stepped(self, episode, action, observation, progress) -> None:
        if action["tool_use_id"] is not None:
            self.state.messages.append(ChatMessageTool(
                content=harness._json(observation), tool_call_id=action["tool_use_id"], function=action["tool"]))
        if progress is not None:
            self.state.messages.append(ChatMessageUser(content=harness.progress_text(progress)["text"]))
        self._store(episode)


@solver
def crm_harness(seconds: float = EPISODE_SECONDS, model: str | None = None,
                client: str | Callable[[], harness.Client] | None = None, max_retries: int = 2):
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        model_id = resolve_model(state.model, model)
        episode = await harness.run_episode(
            prompt=state.input_text, sandbox=sandbox(), client=load_client(client, max_retries),
            model=model_id, seconds=float(seconds), hooks=InspectMirror(state),
        )
        state.store.set("stop_reason", episode.stop_reason)
        state.store.set("episode_error", episode.error)
        state.store.set("elapsed_seconds", round(episode.elapsed_seconds, 3))
        state.output = ModelOutput.from_content(model=f"anthropic/{model_id}",
                                                content=episode.finish_summary or "")
        return state

    return solve


async def extract(path: str, max_bytes: int) -> dict:
    """{"b64": whole file} or {"withheld": why}, read in chunks under Inspect's exec output cap."""
    chunks: list[bytes] = []
    identity, offset, size = None, 0, None
    while size is None or offset < size:
        # -I and cwd "/": the template's bare `python -c` in /workdir would put
        # the agent's directory on sys.path, so a planted json.py ran as root.
        result = await sandbox().exec(
            ["python3", "-I", "-c", EXTRACT_SCRIPT], user="root", cwd="/",
            input=json.dumps({"path": path, "max_bytes": max_bytes, "identity": identity,
                              "offset": offset, "length": EXTRACT_CHUNK_BYTES}),
        )
        if not result.success:
            raise RuntimeError(f"submission extraction failed: {result.stderr}")
        entry = json.loads(result.stdout)
        if "withheld" in entry:
            return entry
        identity, size = entry["identity"], entry["size"]
        chunk = base64.b64decode(entry["b64"])
        if not chunk and offset < size:
            return {"withheld": "changed while it was being extracted"}
        chunks.append(chunk)
        offset += len(chunk)
    return {"b64": base64.b64encode(b"".join(chunks)).decode()}


@scorer(metrics=[mean(), stderr()])
def fresh_container_grader():
    files, max_bytes, transcript_path = load_contract()

    async def score(state: TaskState, target: Target) -> Score:
        # Stop anything the agent left running (a backstop timeout can land
        # mid-call) before reading what it left behind.
        await harness.Shell(sandbox()).sweep()
        delivered, withheld = {}, {}
        for path in files:
            entry = await extract(path, max_bytes)
            if "withheld" in entry:
                withheld[path] = entry["withheld"]
            else:
                delivered[path] = entry["b64"]

        commands = list(state.store.get("shell_commands", []))
        grader = sandbox("grader")
        staged = await grader.exec(
            ["python3", "-I", "-c", STAGE_SCRIPT], cwd="/",
            input=json.dumps({"files": delivered, "withheld": list(withheld), "user": AGENT_USER,
                              "transcript_path": transcript_path,
                              "transcript": {"version": 3, "shell_commands": commands,
                                             "steps": list(state.store.get("steps", [])),
                                             "final_sha256": hashlib.sha256(base64.b64decode(
                                                 delivered[harness.OUTPUT_PATH])).hexdigest()
                                                 if harness.OUTPUT_PATH in delivered else None}}),
            user="root",
        )
        if not staged.success:
            raise RuntimeError(f"staging into the grader container failed: {staged.stderr}")

        result = await grader.exec(
            ["python", "/grader/grader.py", state.metadata["variant"]], user="root"
        )
        if not result.success:
            raise RuntimeError(f"grader failed: {result.stderr}")
        graded = json.loads(result.stdout)

        explanation = graded.get("reason", "graded")
        if withheld:
            explanation += "; withheld from the grader: " + ", ".join(
                f"{p} ({why})" for p, why in withheld.items()
            )
        metadata = {key: value for key, value in graded.items() if key not in ("score", "reason")}
        metadata["episode"] = {
            "stop_reason": state.store.get("stop_reason"), "error": state.store.get("episode_error"),
            "elapsed_seconds": state.store.get("elapsed_seconds"), "tool_calls": state.store.get("tool_calls"),
            "shell_commands": len(commands), "finish_summary": state.store.get("finish_summary"),
            "withheld": withheld,
        }
        return Score(value=graded["score"], explanation=explanation, metadata=metadata)

    return score


def render_prompt(variant: str) -> str:
    # sys.executable, not "python": this runs on the host, and Debian and
    # Ubuntu ship no bare `python`. Used verbatim: it is the user turn.
    return subprocess.run(
        [sys.executable, str(ROOT / "task.py"), variant], capture_output=True, text=True, check=True,
    ).stdout


@task
def honeypot(seconds: float = EPISODE_SECONDS, model: str | None = None,
             client: str | None = None, max_retries: int = 2) -> Task:
    variants = json.loads((ROOT / "variants.json").read_text())["variants"]
    return Task(
        dataset=[Sample(input=render_prompt(v), metadata={"variant": v}, id=v) for v in variants],
        solver=crm_harness(seconds=seconds, model=model, client=client, max_retries=max_retries),
        scorer=fresh_container_grader(),
        sandbox=("docker", str(Path(__file__).parent / "compose.yaml")),
        time_limit=int(seconds) + BACKSTOP_SECONDS,
    )
