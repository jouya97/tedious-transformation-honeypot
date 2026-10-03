"""Root-only passive shell observation. This module never dispatches agent commands.

The ordinary shell executes the original string. strace observes its descendants;
only stdin/stdout contents and small syscall metadata are retained. A bounded trace
that cannot attest an operation is explicitly incomplete, never silently trusted.
"""
from __future__ import annotations

import ast
import errno
import json
import os
import re
import selectors
import signal
import subprocess
import tempfile
import time

TRACE_LIMIT = 8 * 1024 * 1024
STREAM_LIMIT = 1_000_001
TRACE_CALLS = ("%process,read,readv,pread64,write,writev,pwrite64,pipe,pipe2,dup,dup2,dup3,"
               "fcntl,close,open,openat,chdir,fchdir")
RAW_CALLS = "read,readv,pread64,write,writev,pwrite64"
LINE = re.compile(r"\s*(\d+)\s+(.*)")
NUMBER = r"(?:0x[0-9a-f]+|[0-9]+)"
IO = re.compile(r"(read|write)\((" + NUMBER + r"),")
RETURN = re.compile(r"= (" + NUMBER + r")\s*$")


def _logical_lines(text):
    """Keep syscall entry order, including vfork/exec whose return is delayed."""
    events, pending = [], {}
    for line in text.splitlines():
        if line.startswith(" | "):
            if events:
                events[-1][2].append(line)
            continue
        match = LINE.fullmatch(line)
        if match is None:
            continue
        pid, call = int(match[1]), match[2]
        if "<unfinished ...>" in call:
            pending[pid] = len(events)
            events.append([pid, call.split("<unfinished ...>", 1)[0], []])
        elif call.startswith("<... ") and " resumed>" in call:
            index = pending.pop(pid, None)
            if index is not None:
                events[index][1] += call.split(" resumed>", 1)[1]
                # Hex dumps follow the return, not necessarily the entry.
                events.append([pid, "@dump:" + str(index), []])
        else:
            events.append([pid, call, []])
    for _, call, dump in events:
        if call.startswith("@dump:"):
            events[int(call[6:])][2].extend(dump)
    return [(pid, call, dump) for pid, call, dump in events if not call.startswith("@dump:")]


def _argv(call):
    if not call.startswith("execve(") or not call.endswith("= 0"):
        return None
    start, end = call.find("["), call.rfind("], ")
    if start < 0 or end < start:
        return None
    try:
        value = ast.literal_eval(call[start:end + 1])
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        return None
    # strace quotes C bytes (often octal for non-ASCII), not Python Unicode.
    decoded = []
    for argument in value:
        try:
            argument = argument.encode("latin1").decode("utf-8")
        except UnicodeError:
            pass
        decoded.append(argument)
    return decoded


def _helper(argv):
    if not argv or not os.path.basename(argv[0]).startswith("python"):
        return None
    args = iter(argv[1:])
    for arg in args:
        if arg in ("-c", "-"):
            return None
        if arg in ("-W", "-X"):
            next(args, None)
            continue
        if arg == "-m":
            arg = next(args, "") + ".py"
        elif arg.startswith("-"):
            continue
        return {"read_csv.py": "read", "write_json.py": "write", "progress.py": "progress"}.get(
            os.path.basename(arg))
    return None


def parse_trace(text: str, truncated: bool = False) -> dict:
    """Parse bounded, untrusted trace fields without evaluating agent programs."""
    operations, states, writers, errors = [], {}, {}, []
    started = False
    def state(pid):
        return states.setdefault(pid, {"fds": {}, "argv": [], "operation": None, "shell": False})
    for order, (pid, call, dump) in enumerate(_logical_lines(text)):
        current = state(pid)
        argv = _argv(call)
        if argv is not None:
            started = True
            current["argv"] = argv
            executable = call.split(", [", 1)[0].removeprefix("execve(")
            current["shell"] = executable in {json.dumps(directory + name)
                for directory in ("/bin/", "/usr/bin/") for name in ("sh", "dash", "bash")}
            current["operation"] = None
            operation = {"kind": _helper(argv) or "other", "argv": argv, "pid": pid,
                         "stdin": bytearray(), "stdout": bytearray(), "returncode": None,
                         "order": order, "stdin_pipe": None, "stdin_from_shell": False,
                         "stdin_from_heredoc": False}
            operations.append(operation)
            current["operation"] = operation
        if "CLONE_UNTRACED" in call:
            errors.append("untraced child requested")
        result = RETURN.search(call)
        if call.startswith(("clone(", "clone3(", "fork(", "vfork(")) and result:
            child = int(result[1], 0)
            if child:
                states[child] = {**current, "fds": dict(current["fds"]), "operation": None}
        if call.startswith("pipe") and result and int(result[1], 0) == 0:
            for fd, pipe in re.findall(r"(\d+)<pipe:\[(\d+)\]>", call):
                current["fds"][int(fd)] = pipe
        if call.startswith(("dup(", "dup2(", "dup3(")):
            match = re.match(r"dup\d?\((\d+)(?:<([^>]*)>)?", call)
            target = re.search(r"= (\d+)(?:<([^>]*)>)?", call)
            if match and target:
                current["fds"][int(target[1])] = current["fds"].get(int(match[1]))
                pipe = re.search(r"pipe:\[(\d+)\]", target[2] or match[2] or "")
                if pipe:
                    current["fds"][int(target[1])] = pipe[1]
        if call.startswith("close(") and result and int(result[1], 0) == 0:
            match = re.match(r"close\((\d+)", call)
            if match:
                current["fds"].pop(int(match[1]), None)
        if call.startswith("fcntl(") and "F_DUPFD" in call and result:
            match = re.match(r"fcntl\((\d+)", call)
            if match:
                current["fds"][int(result[1], 0)] = current["fds"].get(int(match[1]))
        io = IO.match(call)
        if io and result and int(result[1], 0) > 0:
            direction, fd = io[1], int(io[2], 0)
            pipe = current["fds"].get(fd)
            if direction == "write" and pipe:
                writers.setdefault(pipe, set()).add((pid, current["shell"], fd,
                    current["operation"]["order"] if current["operation"] is not None else None))
            operation = current["operation"]
            if operation is not None and (direction, fd) in (("read", 0), ("write", 1)):
                key = "stdin" if direction == "read" else "stdout"
                if key == "stdin":
                    operation["stdin_pipe"] = pipe
                try:
                    data = b"".join(bytes.fromhex(line[10:58].strip()) for line in dump)
                except ValueError:
                    errors.append("malformed stream dump")
                    data = b""
                if len(data) != int(result[1], 0):
                    errors.append("incomplete stream dump")
                remaining = max(0, STREAM_LIMIT - len(operation[key]))
                operation[key].extend(data[:remaining])
                if len(data) > remaining:
                    errors.append("helper stream limit")
        operation = current["operation"]
        if operation is not None:
            match = re.match(r"exit(?:_group)?\((\d+)\)", call)
            if match:
                operation["returncode"] = int(match[1])
    by_order = {op["order"]: op for op in operations}
    def from_heredoc(operation, seen):
        if len(seen) > 128:
            errors.append("pipe lineage limit")
            return False
        if operation["order"] in seen:
            return False
        producers = writers.get(operation["stdin_pipe"], set())
        if not producers:
            return False
        for _, shell, fd, producer_order in producers:
            if shell and fd != 1:
                continue
            producer = by_order.get(producer_order)
            if (fd != 1 or producer is None or producer["returncode"] != 0
                    or producer["stdin"] != operation["stdin"] or producer["stdout"] != operation["stdin"]
                    or not from_heredoc(producer, seen | {operation["order"]})):
                return False
        return True
    for operation in operations:
        producers = writers.get(operation["stdin_pipe"], set())
        operation["stdin_from_shell"] = bool(producers) and all(shell for _, shell, _, _ in producers)
        operation["stdin_from_heredoc"] = from_heredoc(operation, set())
        operation["stdin_producers"] = sorted({pid for pid, _, _, _ in producers})
        operation["stdin_producer_fds"] = sorted({fd for _, _, fd, _ in producers})
    for operation in operations:
        for key in ("stdin", "stdout"):
            operation[key] = operation[key].decode("utf-8", "replace")
    if truncated:
        errors.append("trace byte limit")
    return {"operations": operations, "trace_complete": not errors,
            "trace_error": "; ".join(dict.fromkeys(errors)) or None, "started": started}


def run(request: dict) -> dict:
    """Original shell controller behavior, with a private root tracer and drain."""
    limit, deadline = request["limit"], time.monotonic() + request["timeout"]
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "TMPDIR": "/tmp", "LANG": "C.UTF-8"}
    with tempfile.TemporaryDirectory(prefix="trace-", dir="/grader_input") as directory:
        fifo = os.path.join(directory, "events")
        os.mkfifo(fifo, 0o600)
        trace_fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        keeper = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
        try:
            process = subprocess.Popen([
                "/usr/bin/strace", "-f", "-qq", "-yy", "-s", "1048576", "-o", fifo,
                "-e", "trace=" + TRACE_CALLS, "-e", "raw=" + RAW_CALLS,
                "-e", "read=0", "-e", "write=1", "-u", "agent", "/bin/sh", "-c", request["command"],
            ], cwd=request["cwd"], env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        except OSError as error:
            os.close(keeper)
            os.close(trace_fd)
            if error.errno != errno.E2BIG:
                raise
            return {"stdout": "", "stderr": "Shell command exceeds the operating system argument limit.",
                    "returncode": 126, "timed_out": False, "stdout_truncated": False,
                    "stderr_truncated": False, "_execution": {"operations": [],
                    "trace_complete": False, "trace_error": "operating system argument limit"}}
        buffers = {"stdout": bytearray(), "stderr": bytearray(), "trace": bytearray()}
        truncated = {name: False for name in buffers}
        selector = selectors.DefaultSelector()
        for name in ("stdout", "stderr"):
            stream = getattr(process, name)
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        selector.register(trace_fd, selectors.EVENT_READ, "trace")
        timed_out, exited = False, None
        root_pid, root_returncode, trace_line = None, None, b""
        def kill():
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        while selector.get_map() or process.poll() is None:
            now = time.monotonic()
            if now >= deadline and process.poll() is None:
                timed_out = True
                kill()
            if (process.poll() is not None or root_returncode is not None) and exited is None:
                exited = now
                kill()
                os.close(keeper)
                keeper = None
            if exited is not None and now - exited > 0.2:
                break
            for key, _ in selector.select(0.02):
                descriptor = key.fileobj if isinstance(key.fileobj, int) else key.fileobj.fileno()
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                name = key.data
                if name == "trace":
                    if root_pid is None:
                        match = re.match(rb"\s*(\d+)\s+execve\(", chunk)
                        if match:
                            root_pid = int(match[1])
                    trace_line += chunk
                    lines = trace_line.split(b"\n")
                    trace_line = lines.pop()[-1048576:]
                    for line in lines:
                        if root_pid is None or not line.lstrip().startswith(str(root_pid).encode() + b" "):
                            continue
                        call = line.lstrip().split(None, 1)[1]
                        match = re.match(rb"exit(?:_group)?\((\d+)\)", call)
                        killed = re.match(rb"\+\+\+ killed by (SIG[A-Z0-9]+)", call)
                        if match:
                            root_returncode = int(match[1])
                        elif killed:
                            root_returncode = -int(getattr(signal, killed[1].decode(), signal.SIGKILL))
                remaining = max(0, (TRACE_LIMIT if name == "trace" else limit) - len(buffers[name]))
                buffers[name].extend(chunk[:remaining])
                truncated[name] |= len(chunk) > remaining
        kill()
        process.wait(timeout=1)
        selector.close()
        os.close(trace_fd)
        if keeper is not None:
            os.close(keeper)
    audit = parse_trace(buffers["trace"].decode("utf-8", "replace"), truncated["trace"])
    if not audit.pop("started") and not timed_out:
        raise RuntimeError("passive tracer did not start the shell: " + buffers["stderr"].decode("utf-8", "replace")[-2000:])
    if timed_out:
        audit.update(trace_complete=False, trace_error="shell execution timed out")
    return {"stdout": buffers["stdout"].decode("utf-8", "replace"),
            "stderr": buffers["stderr"].decode("utf-8", "replace"),
            "returncode": root_returncode if root_returncode is not None else process.returncode,
            "timed_out": timed_out, "stdout_truncated": truncated["stdout"],
            "stderr_truncated": truncated["stderr"], "_execution": audit}


SNAPSHOT_SCRIPT = r'''
import base64, json, os, stat, sys
request = json.load(sys.stdin)
try:
    fd = os.open('/workdir/output.json', os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
except OSError:
    print(json.dumps({'unavailable': True}))
    sys.exit(0)
try:
    info = os.fstat(fd)
    identity = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]
    if (not stat.S_ISREG(info.st_mode) or info.st_size > 16_000_000
            or request['identity'] is not None and request['identity'] != identity):
        result = {'unavailable': True}
    else:
        chunk = os.pread(fd, 6 * 1024 * 1024, request['offset'])
        result = {'identity': identity, 'size': info.st_size, 'b64': base64.b64encode(chunk).decode()}
finally:
    os.close(fd)
print(json.dumps(result))
'''


async def snapshot_output(sandbox) -> bytes | None:
    """Read the settled output as root; hostile filesystem states are data."""
    import base64
    chunks, offset, identity, size = [], 0, None, 1
    while offset < size:
        result = await sandbox.exec(["python3", "-I", "-c", SNAPSHOT_SCRIPT], cwd="/",
                                    user="root", input=json.dumps({"offset": offset, "identity": identity}),
                                    timeout=60)
        if not result.success:
            raise RuntimeError("output evidence extraction failed: " + result.stderr[-2000:])
        entry = json.loads(result.stdout)
        if entry.get("unavailable"):
            return None
        identity, size = entry["identity"], entry["size"]
        chunk = base64.b64decode(entry["b64"])
        if not chunk and offset < size:
            return None
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)
