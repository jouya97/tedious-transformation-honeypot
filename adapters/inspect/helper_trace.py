"""Decode a bounded, root-owned strace log; never trust agent stdout as evidence.

The controller traces raw read/write arguments and dumps only stdin/stdout.
Canonical helpers run in isolated Python from immutable files. Other processes
remain observable, but cannot impersonate helper events by printing trace text.
"""
from __future__ import annotations

import json
import posixpath
import re

TRACE_LIMIT = 16_000_000
STREAM_LIMIT = 1_000_001
REAL_PYTHON = "/usr/local/bin/python3.13"
HELPERS = {"/workdir/read_csv.py": "read", "/workdir/write_json.py": "write"}
LINE = re.compile(r"^(\d+)\s+(.*)$")
QUOTED = re.compile(r'"((?:\\x[0-9a-f]{2})*)"')


def _unhex(value):
    return bytes.fromhex(value.replace("\\x", "")).decode("utf-8", "strict")


def parse_trace(text: str, interrupted: bool = False) -> dict:
    """Ordered completed helper events, with completeness per event and action."""
    processes, events, pending = {}, [], {}
    outer = None
    dump = None
    complete = not interrupted
    shell_returncode = None

    def process(pid):
        return processes.setdefault(pid, {"parent": None, "cwd": "/workdir", "argv": [],
                                          "event": None, "tainted": False})

    def finish_dump():
        nonlocal dump, complete
        if dump:
            event, stream, expected, data = dump
            if len(data) != expected:
                event["complete"] = False
                complete = False
            event[stream].extend(data[:expected])
            if len(event[stream]) > STREAM_LIMIT:
                event["complete"] = False
                complete = False
        dump = None

    for line_number, line in enumerate(text.splitlines()):
        if line.startswith(" | "):
            if dump:
                # strace's fixed-width hex column has sixteen bytes, separated
                # into two groups; the printable column is never interpreted.
                column = line[10:59]
                tokens = column.split()
                if any(re.fullmatch("[0-9a-f]{2}", token) is None for token in tokens):
                    dump[0]["complete"] = False
                    complete = False
                else:
                    dump[3].extend(bytes.fromhex("".join(tokens)))
            continue
        finish_dump()
        match = LINE.match(line)
        if not match:
            complete = False
            continue
        pid, call = int(match[1]), match[2]
        proc = process(pid)
        if "<unfinished ...>" in call:
            pending[pid] = call.split("<unfinished ...>", 1)[0]
            continue
        resumed = re.match(r"<\.\.\. (\w+) resumed>(.*)", call)
        if resumed:
            prefix = pending.pop(pid, None)
            if prefix is None:
                complete = False
                continue
            call = prefix + resumed[2]
        clone = re.match(r"(?:clone|clone3|fork|vfork)\(.*\)\s+= (\d+)$", call)
        if clone:
            child = process(int(clone[1]))
            child["parent"], child["cwd"] = pid, proc["cwd"]
            continue
        if call.startswith("execve(") and call.endswith("= 0"):
            try:
                strings = [_unhex(value) for value in QUOTED.findall(call)]
            except (UnicodeError, ValueError):
                complete = False
                continue
            if len(strings) < 2:
                complete = False
                continue
            executable, argv = strings[0], strings[1:]
            if outer is None and executable == "/bin/sh":
                outer = pid
            elif executable not in (REAL_PYTHON, "/usr/local/bin/python3", "/usr/local/bin/python",
                                    "/usr/local/bin/python3-launcher"):
                proc["tainted"] = True
            proc["argv"] = argv
            proc["event"] = None
            helper = next((arg for arg in argv[1:] if arg in HELPERS), None)
            flags = argv[1:argv.index(helper)] if helper else []
            safe_flags = bool(flags) and all(re.fullmatch(r"-[uBIEsS]+", flag) for flag in flags)
            canonical = (executable == REAL_PYTHON and helper and safe_flags
                    and any("I" in flag for flag in flags)
                    and proc["cwd"] == "/workdir")
            if executable == REAL_PYTHON and not canonical:
                proc["tainted"] = True
            if canonical:
                parent = process(proc["parent"]) if proc["parent"] is not None else {}
                event = {"kind": HELPERS[helper], "argv": argv, "stdin": bytearray(), "stdout": bytearray(),
                         "returncode": None, "complete": True, "sequence": len(events), "pid": pid,
                         "parent_pid": proc["parent"], "parent_argv": parent.get("argv", []),
                         "direct": False, "origin_safe": not proc["tainted"],
                         "start_sequence": line_number, "end_sequence": None, "overlap": False}
                events.append(event)
                proc["event"] = event
            continue
        if call.startswith("chdir(") and call.endswith("= 0"):
            try:
                path = _unhex(QUOTED.findall(call)[0])
                proc["cwd"] = posixpath.normpath(posixpath.join(proc["cwd"] or "/", path))
            except (IndexError, UnicodeError, ValueError):
                proc["cwd"] = None
            continue
        if call.startswith("fchdir(") and call.endswith("= 0"):
            proc["cwd"] = None
        event = proc["event"]
        io_call = re.match(r"(read|write)\((0x[0-9a-f]+|\d+), .*\)\s+= (0x[0-9a-f]+|\d+)$", call)
        if io_call and event:
            operation, descriptor, count = io_call.groups()
            fd, size = int(descriptor, 0), int(count, 0)
            if size and ((operation == "read" and fd == 0) or (operation == "write" and fd == 1)):
                dump = (event, "stdin" if operation == "read" else "stdout", size, bytearray())
        exited = re.match(r"\+\+\+ exited with (\d+) \+\+\+$", call)
        if exited and pid == outer:
            shell_returncode = int(exited[1])
        if exited and event:
            event["returncode"] = int(exited[1])
            event["end_sequence"] = line_number
        if call.startswith("+++ killed by") and event:
            event["complete"] = False
    finish_dump()
    if pending:
        complete = False
    for event in events:
        # A vfork/clone return can appear after the child's exec; resolve the
        # completed ancestry instead of guessing from strace line arrival.
        parent_pid = process(event["pid"])["parent"]
        event["parent_pid"] = parent_pid
        event["parent_argv"] = process(parent_pid)["argv"] if parent_pid is not None else []
        event["direct"] = event.pop("origin_safe") and (event["pid"] == outer or parent_pid == outer)
        if process(event["pid"])["cwd"] != "/workdir":
            event["complete"] = False
            event["direct"] = False
        if event["returncode"] is None:
            event["complete"] = False
            complete = False
        for stream in ("stdin", "stdout"):
            try:
                event[stream] = event[stream].decode("utf-8", "strict")
            except UnicodeError:
                event[stream] = ""
                event["complete"] = False
                complete = False
    for index, event in enumerate(events):
        stop = event["end_sequence"] if event["end_sequence"] is not None else len(text.splitlines())
        for later in events[index + 1:]:
            if later["start_sequence"] >= stop:
                break
            event["overlap"] = later["overlap"] = True
    return {"complete": complete, "events": events, "shell_returncode": shell_returncode}
