"""Host-side certification of directly dispatched, unmodified task helpers.

A shell command is never its own execution evidence. Only the small grammar
in provenance.parse_command can reach this runner; the code it executes comes
from this checkout, and its result comes from sandbox.exec rather than an
agent-owned shell controller. All filesystem attestations run as root with an
isolated interpreter and bounded, nonblocking, no-follow reads.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PYTHON = "/usr/local/bin/python3"
WORKDIR = "/workdir"
OUTPUT_LIMIT_BYTES = 65536
SOURCES = {name: (ROOT / "agent_data" / f"{name}.py").read_text(encoding="utf-8")
           for name in ("read_csv", "write_json", "progress")}
SOURCE_HASHES = {f"/workdir/{name}.py": hashlib.sha256(source.encode()).hexdigest()
                 for name, source in SOURCES.items()}
HELPERS = {"read": "read_csv", "write": "write_json", "progress": "progress"}

SNAPSHOT_SCRIPT = r'''
import hashlib, json, os, stat, sys
request = json.load(sys.stdin)
def digest(path, limit):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                return None
            hasher, size = hashlib.sha256(), 0
            while True:
                block = stream.read(min(1048576, limit + 1 - size))
                if not block:
                    break
                size += len(block)
                if size > limit:
                    return None
                hasher.update(block)
            return hasher.hexdigest()
    except OSError:
        return None
out = {"sha256": digest("/workdir/output.json", 16000000)}
if request.get("verify"):
    out["helpers_verified"] = all(digest(path, 1000000) == expected
        for path, expected in request["helpers"].items())
    visible = digest("/workdir/input.csv", 32000000)
    original = digest("/trusted_helpers/input.csv", 32000000)
    out["source_verified"] = visible is not None and visible == original
json.dump(out, sys.stdout)
'''


def parse_command(command: str) -> list[dict] | None:
    # This module is also imported by adapter tests before the grader is on
    # sys.path. Load the one authoritative parser by its explicit host path.
    spec = importlib.util.spec_from_file_location("_adapter_provenance", ROOT / "grader" / "provenance.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_command(command)


async def snapshot(sandbox: Any, *, verify: bool = False) -> dict:
    result = await sandbox.exec(
        [PYTHON, "-I", "-c", SNAPSHOT_SCRIPT], user="root", cwd="/", timeout=60,
        input=json.dumps({"verify": verify, "helpers": SOURCE_HASHES}),
    )
    if not result.success:
        raise RuntimeError(f"trusted filesystem snapshot failed: {result.stderr}")
    facts = json.loads(result.stdout)
    if not isinstance(facts, dict) or "sha256" not in facts:
        raise RuntimeError("trusted filesystem snapshot returned malformed evidence")
    return facts


def program(action: dict) -> str:
    """Build an isolated invocation with a frozen progress dependency."""
    name = HELPERS[action["kind"]]
    dependency = SOURCES["write_json"]
    source = SOURCES[name]
    # -I prevents planted modules, and these scripts use only the standard
    # library. progress imports the frozen writer installed here in sys.modules.
    return ("import os, sys, types\n"
            "os.environ.clear()\n"
            "os.environ.update({'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/tmp',"
            "'TMPDIR':'/tmp','LANG':'C.UTF-8'})\n"
            f"sys.argv = {[name + '.py', *action['argv']]!r}\n"
            + ("dependency = types.ModuleType('write_json')\n"
               f"exec(compile({dependency!r}, '<trusted write_json.py>', 'exec'), dependency.__dict__)\n"
               "sys.modules['write_json'] = dependency\n" if name == "progress" else "")
            + f"exec(compile({source!r}, '<trusted {name}.py>', 'exec'), {{'__name__':'__main__'}})\n")


async def run(sandbox: Any, action: dict, timeout: float) -> dict:
    try:
        result = await sandbox.exec(
            [PYTHON, "-I", "-u", "-c", program(action)], input=action.get("stdin", ""),
            cwd=WORKDIR, user="agent", timeout=max(1, math.ceil(timeout)), timeout_retry=False,
        )
    except TimeoutError:
        return {"stdout": "", "stderr": "Shell execution timed out.", "returncode": 124,
                "timed_out": True, "stdout_truncated": False, "stderr_truncated": False}
    out = {"returncode": result.returncode, "timed_out": False}
    for key in ("stdout", "stderr"):
        content = getattr(result, key).encode("utf-8", "replace")
        out[key] = content[:OUTPUT_LIMIT_BYTES].decode("utf-8", "replace")
        out[f"{key}_truncated"] = len(content) > OUTPUT_LIMIT_BYTES
    return out
