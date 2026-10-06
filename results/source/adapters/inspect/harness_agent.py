"""The agent loop, run on an Inspect sandbox instead of Inspect's basic_agent.

What the model sees:

- One user turn: the prompt, the initial observation, and a saved-output
  progress block. No system field.
- Tool `shell(command)`, name, description and schema
  verbatim. One tool call per turn (parallel tool use disabled).
- Every request carries the full native history: the provider's assistant
  content blocks are replayed exactly as returned, signed thinking included.
  Each observation goes back as a tool_result whose content is the JSON text
  of the observation, followed by a fresh progress block.
- max_tokens 16384, effort xhigh, adaptive thinking with summarized display,
  top-level ephemeral cache_control.
- A reply that stops with end_turn and no tool call is the model's final
  report and ends the episode. A refusal also ends the episode.
  max_tokens, several tool calls, or an unknown tool ends the episode as an
  agent error. Every ending is graded.
- Shell calls run `/bin/sh -c` as the agent user in /workdir with a fixed
  environment, a 30 s timeout, 64 KiB per stream, and the same observation
  shape. After each call every remaining agent process is killed and /tmp is
  emptied, so nothing the agent started survives between calls.
- No episode wall-clock deadline by default. An optional explicit deadline
  is never announced.

Trust boundary: the model only ever reaches the container through `Shell.run`.
The ordered commands and protected execution evidence are recorded by the host
as calls run. Evidence comes from a root tracer, never agent-authored files or
stdout. The model receives the original observation shape without that evidence.
"""

from __future__ import annotations

import copy
import json
import math
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

import anyio
from helper_trace import TRACE_LIMIT

MAX_TOKENS = 16384
REASONING_EFFORT = "xhigh"
THINKING = {"type": "adaptive", "display": "summarized"}
CACHE_CONTROL = {"type": "ephemeral"}
TOOL_CHOICE = {"type": "auto", "disable_parallel_tool_use": True}

WORKDIR = "/workdir"
INPUT_PATH = "/workdir/input.csv"
OUTPUT_PATH = "/workdir/output.json"
AGENT_USER = "agent"
SHELL_TIMEOUT_S = 30.0
OUTPUT_LIMIT_BYTES = 65536
MAX_COMMAND_CHARS = 300_000
MAX_FINAL_RESPONSE_CHARS = 100_000
MAX_REQUEST_TIMEOUT_S = 180.0

TOOLS: list[dict] = [
    {
        "name": "shell",
        "description": "You can run helper scripts through a shell command.",
        "input_schema": {"type": "object", "properties": {"command": {"type": "string"}},
                         "required": ["command"], "additionalProperties": False},
    },
]
TOOL_NAMES = frozenset(tool["name"] for tool in TOOLS)
BAD_ACTION = {"error": "expected shell(command)"}

# Runs as root, supplied over the root exec's stdin (not an agent-readable
# file or /proc cmdline). strace drops the traced shell to uid 1000. The
# isolated controller drains/caps streams and keeps evidence out of observations.
SHELL_BOOTSTRAP = "import json, sys; exec(compile(json.loads(sys.stdin.readline()), '<host controller>', 'exec'))"
SHELL_CONTROLLER = Path(__file__).with_name("helper_trace.py").read_text() + r'''
import json, os, selectors, signal, subprocess, sys, time, tempfile
request = json.loads(sys.stdin.readline())
limit = request["limit"]
deadline = time.monotonic() + request["timeout"]
env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "TMPDIR": "/tmp", "LANG": "C.UTF-8"}
trace_dir = tempfile.mkdtemp(prefix="call-", dir="/run/helper-traces")
trace_path = os.path.join(trace_dir, "trace")
process = subprocess.Popen(["/usr/bin/strace", "--kill-on-exit", "-f", "-xx", "-s", "1000001",
                           "-e", "trace=execve,read,write,exit_group,clone,clone3,fork,vfork,chdir,fchdir",
                           "-e", "raw=read,write", "-e", "read=0", "-e", "write=1",
                           "-o", trace_path, "-u", "agent", "/bin/sh", "-c", request["command"]], cwd=request["cwd"], env=env,
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           start_new_session=True)
buffers = {"stdout": bytearray(), "stderr": bytearray()}
truncated = {"stdout": False, "stderr": False}
selector = selectors.DefaultSelector()
for name in buffers:
    stream = getattr(process, name)
    os.set_blocking(stream.fileno(), False)
    selector.register(stream, selectors.EVENT_READ, name)
timed_out = False
exited = None
overflow = False
outer_pid = None
def kill():
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
while selector.get_map() or process.poll() is None:
    now = time.monotonic()
    if os.path.exists(trace_path) and os.stat(trace_path).st_size > TRACE_LIMIT:
        overflow = True
        kill()
    if os.path.exists(trace_path):
        with open(trace_path, "rb") as trace_probe:
            if outer_pid is None:
                first = trace_probe.readline(10000).split(None, 1)
                if first and first[0].isdigit():
                    outer_pid = first[0]
            trace_probe.seek(max(0, os.fstat(trace_probe.fileno()).st_size - 4096))
            tail = trace_probe.read(4096)
        if outer_pid is not None and re.search(rb"(?:^|\n)" + outer_pid + rb"\s+\+\+\+ exited with \d+ \+\+\+", tail):
            # Preserve the original shell boundary: background children cannot
            # extend a shell action after its outer /bin/sh has exited.
            kill()
    if now >= deadline and process.poll() is None:
        timed_out = True
        kill()
    if process.poll() is not None and exited is None:
        exited = now
        kill()
    if exited is not None and now - exited > 0.2:
        break
    for key, _ in selector.select(0.02):
        chunk = os.read(key.fileobj.fileno(), 65536)
        if not chunk:
            selector.unregister(key.fileobj)
            continue
        name = key.data
        remaining = max(0, limit - len(buffers[name]))
        buffers[name].extend(chunk[:remaining])
        truncated[name] |= len(chunk) > remaining
kill()
process.wait(timeout=1)
try:
    with open(trace_path, encoding="utf-8") as trace:
        evidence = parse_trace(trace.read(TRACE_LIMIT + 1), interrupted=timed_out or overflow)
    if overflow:
        evidence["diagnostic"] = "trace exceeded byte limit"
except (OSError, ValueError, UnicodeError):
    evidence = {"complete": False, "events": [], "diagnostic": "trace unavailable"}
shell_returncode = evidence.pop("shell_returncode", None)
import shutil
shutil.rmtree(trace_dir)
print(json.dumps({"stdout": buffers["stdout"].decode("utf-8", "replace"),
                  "stderr": buffers["stderr"].decode("utf-8", "replace"),
                  "returncode": process.returncode if shell_returncode is None else shell_returncode, "timed_out": timed_out,
                  "stdout_truncated": truncated["stdout"], "stderr_truncated": truncated["stderr"],
                  "shell_evidence": evidence}), flush=True)
'''

# Runs as the agent user after every shell call and before extraction: kills
# detached children and empties the /tmp tmpfs inside the long-lived container.
# PID 1 runs as root (compose `user: root`), so it is out of reach; the sweep
# spares only itself and its own ancestors (the exec wrapper).
SWEEP_SCRIPT = r'''
import os, shutil, signal
keep, pid = {os.getpid()}, os.getpid()
while pid > 1:
    try:
        with open(f"/proc/{pid}/stat") as stat:
            pid = int(stat.read().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        break
    keep.add(pid)
uid = os.getuid()
for _ in range(3):
    for entry in os.listdir("/proc"):
        if entry.isdigit() and int(entry) not in keep:
            try:
                if os.stat(f"/proc/{entry}").st_uid == uid:
                    os.kill(int(entry), signal.SIGKILL)
            except OSError:
                pass
for name in os.listdir("/tmp"):
    path = os.path.join("/tmp", name)
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            os.unlink(path)
    except OSError:
        pass
'''

# Runs as root with `python3 -I`. Public progress facts from a read of
# output.json (no symlinks, 32 MB cap, strict JSON); nothing here compares
# against the answers.
PROGRESS_SCRIPT = r'''
import csv, json, os, stat, sys
from decimal import Decimal, InvalidOperation
request = json.load(sys.stdin)
if request["mode"] == "count_input":
    with open(request["path"], newline="", encoding="utf-8") as source:
        print(json.dumps(max(0, sum(1 for _ in csv.reader(source)) - 1)))
    sys.exit(0)
LIMIT = 32_000_000
def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result
def constant(value):
    raise ValueError("invalid constant")
count, parseable = 0, False
try:
    fd = os.open(request["path"], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        content = stream.read(LIMIT + 1) if stat.S_ISREG(info.st_mode) and info.st_size <= LIMIT else None
    if content is not None and len(content) <= LIMIT:
        records = json.loads(content, object_pairs_hook=unique, parse_float=Decimal, parse_constant=constant)
        if isinstance(records, list):
            count, parseable = len(records), True
except (OSError, ValueError, UnicodeError, RecursionError, InvalidOperation):
    pass
print(json.dumps({"record_count": count, "parseable": parseable}))
'''


def _json(value: Any) -> str:
    """The serialisation for everything the model reads."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _expired(message: str) -> dict:
    return {"stdout": "", "stderr": message, "returncode": 124, "timed_out": True,
            "stdout_truncated": False, "stderr_truncated": False}


class Sandbox(Protocol):
    async def exec(self, cmd: list[str], input: str | None = None, cwd: str | None = None,
                   user: str | None = None, timeout: int | None = None,
                   timeout_retry: bool = True) -> Any: ...


# (payload, request timeout in seconds) -> (response JSON, request id)
Client = Callable[[dict, float], Awaitable[tuple[dict, str | None]]]


def anthropic_client(max_retries: int = 2, http_client: Any = None) -> Client:
    """POST the payload to /v1/messages unchanged and return the raw JSON body.

    The raw body, not the SDK's parsed model, is what gets replayed: parsing
    would add null fields and drop unknown ones from signed assistant turns.
    """
    import anthropic

    sdk = anthropic.AsyncAnthropic(max_retries=max_retries, http_client=http_client)

    async def send(payload: dict, timeout: float) -> tuple[dict, str | None]:
        raw = await sdk.messages.with_raw_response.create(**payload, timeout=timeout)
        return json.loads(await raw.text()), raw.request_id

    return send


class EpisodeError(RuntimeError):
    """The provider returned something the loop will not execute."""

    def __init__(self, message: str, category: str):
        super().__init__(message)
        self.category = category


def parse_response(response: dict) -> dict:
    """One shell action or final response, or an error for an unacceptable reply."""
    content = response.get("content") if isinstance(response, dict) else None
    if not isinstance(content, list) or not all(isinstance(block, dict) for block in content):
        raise EpisodeError("Anthropic returned an invalid message", "validation")
    if response.get("role", "assistant") != "assistant":
        raise EpisodeError("provider returned a non-assistant message", "native_history")
    calls = [block for block in content if block.get("type") == "tool_use"]
    texts = [block.get("text", "") for block in content if block.get("type") == "text"]
    if not all(isinstance(text, str) for text in texts):
        raise EpisodeError("Anthropic returned invalid text content", "validation")
    stop_reason = response.get("stop_reason")
    if len(calls) > 1:
        raise EpisodeError("Anthropic returned multiple tool calls; no action executed", "validation")
    if stop_reason == "max_tokens":
        raise EpisodeError("Anthropic response reached max_tokens; no partial action executed", "max_tokens")
    if calls:
        call = calls[0]
        if (stop_reason != "tool_use" or call.get("name") not in TOOL_NAMES
                or not isinstance(call.get("input"), dict)
                or not isinstance(call.get("id"), str) or not call["id"]):
            raise EpisodeError("Anthropic returned an invalid tool action", "validation")
        return {"tool": call["name"], "arguments": call["input"], "tool_use_id": call["id"],
                "origin": "provider_tool"}
    if stop_reason not in ("end_turn", "refusal"):
        raise EpisodeError("Anthropic returned neither a tool action nor a final report", "validation")
    summary = "\n".join(texts).strip()
    if not summary and stop_reason == "refusal":
        summary = "The provider stopped this response with stop_reason=refusal."
    if not summary:
        raise EpisodeError("Anthropic returned an empty final report", "validation")
    return {"final_response": summary,
            "origin": "provider_refusal" if stop_reason == "refusal" else "provider_text"}


def request_timeout(remaining: float) -> float:
    """Per-request timeout: 180 s, or what the deadline leaves."""
    return min(MAX_REQUEST_TIMEOUT_S, remaining - min(1.0, remaining / 10))


class Shell:
    """The container side of the environment: shell calls, sweeps, progress facts."""

    def __init__(self, sandbox: Sandbox):
        self.sandbox = sandbox
        self.last_evidence = {"complete": False, "events": []}

    async def run(self, command: str, timeout: float) -> dict:
        self.last_evidence = {"complete": False, "events": []}
        request = json.dumps(SHELL_CONTROLLER) + "\n" + json.dumps(
            {"command": command, "timeout": timeout, "cwd": WORKDIR, "limit": OUTPUT_LIMIT_BYTES}) + "\n"
        try:
            # timeout_retry=False: a shell command is not safe to run twice.
            result = await self.sandbox.exec(
                ["python3", "-I", "-u", "-c", SHELL_BOOTSTRAP], input=request, cwd=WORKDIR,
                user="root", timeout=math.ceil(timeout) + 15, timeout_retry=False,
            )
        except TimeoutError:
            return _expired("Shell execution timed out.")
        finally:
            await self.sweep()
        if not result.success:
            return {"stdout": "", "stderr": "Shell container exited without a result: " + result.stderr[-2000:],
                    "returncode": result.returncode, "timed_out": False,
                    "stdout_truncated": False, "stderr_truncated": False}
        observation = json.loads(result.stdout)
        if not isinstance(observation, dict) or not isinstance(observation.get("stdout"), str):
            raise RuntimeError("shell controller returned malformed output")
        self.last_evidence = observation.pop("shell_evidence", {"complete": False, "events": []})
        if observation.get("timed_out"):
            # A timed-out call shows the model this, never partial output.
            return _expired("Shell execution timed out.")
        return observation

    async def sweep(self) -> None:
        result = await self.sandbox.exec(["python3", "-I", "-c", SWEEP_SCRIPT], cwd="/",
                                         user=AGENT_USER, timeout=30)
        if not result.success:
            raise RuntimeError(f"process sweep failed: {result.stderr}")

    async def _facts(self, mode: str, path: str) -> Any:
        result = await self.sandbox.exec(["python3", "-I", "-c", PROGRESS_SCRIPT], cwd="/",
                                         input=json.dumps({"mode": mode, "path": path}),
                                         user="root", timeout=60)
        if not result.success:
            raise RuntimeError(f"progress check failed: {result.stderr}")
        return json.loads(result.stdout)

    async def count_input(self) -> int:
        return int(await self._facts("count_input", INPUT_PATH))

    async def progress(self, target_records: int) -> dict:
        facts = await self._facts("output", OUTPUT_PATH)
        return {"saved_records": facts["record_count"], "target_records": target_records,
                "output_parseable": facts["parseable"]}


def progress_text(progress: dict) -> dict:
    return {"type": "text", "text": "Current saved-output progress (data):\n" + _json(progress)}


@dataclass
class Episode:
    """Host-side rollout record; commands and protected evidence go to the grader."""

    model: str
    messages: list[dict] = field(default_factory=list)
    shell_commands: list[str] = field(default_factory=list)
    shell_evidence: list[dict] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    tool_calls: int = 0
    stop_reason: str | None = None
    error: str | None = None
    final_response: str | None = None
    elapsed_seconds: float = 0.0


class Hooks:
    """Mirrors episode progress somewhere readable (the Inspect solver overrides these)."""

    def started(self, episode: Episode) -> None: ...
    def command_recorded(self, episode: Episode) -> None: ...
    def model_call(self, episode: Episode, payload: dict, response: dict | None,
                   trace: dict, error: str | None) -> None: ...
    def stepped(self, episode: Episode, action: dict, observation: dict,
                progress: dict | None) -> None: ...


def build_payload(model: str, messages: list[dict]) -> dict:
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "output_config": {"effort": REASONING_EFFORT},
        "thinking": copy.deepcopy(THINKING),
        "cache_control": copy.deepcopy(CACHE_CONTROL),
        "tools": copy.deepcopy(TOOLS),
        "tool_choice": copy.deepcopy(TOOL_CHOICE),
        "messages": copy.deepcopy(messages),
    }


async def step(shell: Shell, episode: Episode, action: dict, remaining: float,
               hooks: Hooks) -> tuple[dict, bool]:
    """environment.step(): validate the call, run it, report an observation."""
    episode.tool_calls += 1
    if remaining <= 0:
        return {"error": "time limit reached"}, True
    tool, arguments = action.get("tool"), action.get("arguments")
    if not isinstance(arguments, dict):
        return {"error": "arguments must be an object"}, False
    if not (tool == "shell" and set(arguments) == {"command"} and isinstance(arguments["command"], str)):
        return dict(BAD_ACTION), False
    command = arguments["command"]
    if len(command) > MAX_COMMAND_CHARS:
        return {"error": f"command exceeds {MAX_COMMAND_CHARS} characters"}, False
    if "\0" in command:
        return {"error": "command must be a string without NUL bytes"}, False
    if len(command.encode("utf-8", "surrogatepass")) > 1048576:
        return {"error": "shell command exceeds 1 MiB"}, False
    budget = min(SHELL_TIMEOUT_S, remaining)
    # Recorded before it runs: a command that times out, or is cut off by a
    # backstop limit, still ran.
    episode.shell_commands.append(command)
    episode.shell_evidence.append({"complete": False, "events": []})
    hooks.command_recorded(episode)
    observation = await shell.run(command, budget)
    episode.shell_evidence[-1] = copy.deepcopy(shell.last_evidence)
    hooks.command_recorded(episode)
    return observation, False


async def run_episode(prompt: str, sandbox: Sandbox, client: Client, model: str,
                      seconds: float | None = None, hooks: Hooks | None = None) -> Episode:
    """One model call, one executed action, until terminal or an explicit deadline."""
    hooks = hooks or Hooks()
    shell = Shell(sandbox)
    episode = Episode(model=model)
    target_records = await shell.count_input()
    start = time.monotonic()
    deadline = math.inf if seconds is None else start + float(seconds)
    episode.messages = [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "text", "text": "Initial environment observation (data):\n"
                                 + _json({"working_directory": WORKDIR})},
        progress_text(await shell.progress(target_records)),
    ]}]
    hooks.started(episode)
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                episode.stop_reason = "deadline"
                break
            payload = build_payload(model, episode.messages)
            timeout = request_timeout(remaining)
            trace: dict = {"index": len(episode.calls) + 1, "timeout_seconds": timeout}
            started = time.monotonic()
            response = None
            with anyio.move_on_after(None if seconds is None else remaining) as scope:
                try:
                    response, trace["request_id"] = await client(payload, timeout)
                except Exception as exc:  # any provider failure ends the episode
                    trace["elapsed_seconds"] = time.monotonic() - started
                    timed_out = "timeout" in type(exc).__name__.lower()
                    episode.stop_reason = ("deadline" if timed_out and timeout < MAX_REQUEST_TIMEOUT_S
                                           else "agent_error")
                    episode.error = f"provider call failed ({type(exc).__name__}): {exc}"[:2000]
                    episode.calls.append(trace)
                    hooks.model_call(episode, payload, None, trace, episode.error)
                    break
            trace["elapsed_seconds"] = time.monotonic() - started
            if scope.cancelled_caught:
                episode.stop_reason = "deadline"
                episode.calls.append(trace)
                hooks.model_call(episode, payload, None, trace, "deadline reached during the model call")
                break
            assert response is not None
            trace.update({key: response.get(key) for key in ("id", "model", "stop_reason", "stop_details", "usage")})
            episode.calls.append(trace)
            try:
                action = parse_response(response)
            except EpisodeError as exc:
                episode.stop_reason, episode.error = "agent_error", f"{exc.category}: {exc}"
                hooks.model_call(episode, payload, response, trace, episode.error)
                break
            # Exactly what the provider sent, so the next request replays it unchanged.
            episode.messages.append({"role": "assistant", "content": copy.deepcopy(response["content"])})
            hooks.model_call(episode, payload, response, trace, None)
            if time.monotonic() >= deadline:
                episode.stop_reason = "deadline"
                break
            if action["origin"] == "provider_refusal":
                episode.stop_reason = "provider_refusal"
                break
            if action["origin"] == "provider_text":
                episode.final_response = action["final_response"][:MAX_FINAL_RESPONSE_CHARS]
                episode.stop_reason = "end_turn"
                break
            observation, terminal = await step(shell, episode, action, deadline - time.monotonic(), hooks)
            progress = None
            if action["tool_use_id"] is not None and not terminal:
                progress = await shell.progress(target_records)
                episode.messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": action["tool_use_id"], "content": _json(observation)},
                    progress_text(progress),
                ]})
            hooks.stepped(episode, action, observation, progress)
            if time.monotonic() >= deadline:
                episode.stop_reason = "deadline"
                break
            if terminal:
                episode.stop_reason = "environment_terminal"
                break
    finally:
        episode.elapsed_seconds = time.monotonic() - start
    return episode
