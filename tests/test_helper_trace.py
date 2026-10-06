"""Kernel-trace parser invariants and real protected-controller adversarial tests."""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import asyncio

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters" / "inspect"))
import harness_agent as harness
from helper_trace import parse_trace


def hex_string(value):
    return '"' + ''.join(f'\\x{byte:02x}' for byte in value.encode()) + '"'


def exec_line(pid, executable, argv):
    return f'{pid} execve({hex_string(executable)}, [{", ".join(map(hex_string, argv))}], 0x0) = 0'


def test_exec_identity_and_late_clone_parent_are_not_guessed():
    prefix = exec_line(10, "/bin/sh", ["/bin/sh", "-c", "command"])
    helper = exec_line(11, "/usr/local/bin/python3.13", ["python3.13", "-I", "/workdir/read_csv.py"])
    result = parse_trace('\n'.join([prefix, '10 vfork(<unfinished ...>', helper,
                                    '10 <... vfork resumed>) = 11', '11 +++ exited with 0 +++',
                                    '10 +++ exited with 0 +++']))
    assert result["complete"] and result["events"][0]["direct"]
    assert result["events"][0]["parent_pid"] == 10
    spoofed = exec_line(11, "/bin/false", ["python3.13", "-I", "/workdir/read_csv.py"])
    assert parse_trace(prefix + '\n' + spoofed)["events"] == []


def test_python_program_reexec_cannot_become_direct_helper():
    lines = [exec_line(10, "/bin/sh", ["/bin/sh", "-c", "command"]),
             '10 clone() = 11',
             exec_line(11, "/usr/local/bin/python3.13", ["python3.13", "-c", "os.execve(...)"]),
             exec_line(11, "/usr/local/bin/python3.13", ["python3.13", "-I", "/workdir/read_csv.py"]),
             '11 +++ exited with 0 +++']
    assert parse_trace('\n'.join(lines))["events"][0]["direct"] is False


def test_missing_dump_or_exit_is_incomplete_evidence():
    lines = [exec_line(10, "/bin/sh", ["/bin/sh", "-c", "command"]), '10 clone() = 11',
             exec_line(11, "/usr/local/bin/python3.13", ["python3.13", "-I", "/workdir/write_json.py"]),
             '11 read(0, 0x100, 0x1000) = 0x3', '11 +++ exited with 0 +++']
    result = parse_trace('\n'.join(lines))
    assert not result["complete"] and not result["events"][0]["complete"]
    result = parse_trace('\n'.join(lines[:-2]))
    assert not result["complete"] and result["events"][0]["returncode"] is None


def test_overlapping_reader_writer_are_marked_ambiguous():
    lines = [exec_line(10, "/bin/sh", ["/bin/sh", "-c", "command"]), '10 clone() = 11',
             exec_line(11, "/usr/local/bin/python3.13", ["python3.13", "-I", "/workdir/read_csv.py"]),
             '10 clone() = 12',
             exec_line(12, "/usr/local/bin/python3.13", ["python3.13", "-I", "/workdir/write_json.py"]),
             '11 +++ exited with 0 +++', '12 +++ exited with 0 +++']
    assert all(event['overlap'] for event in parse_trace('\n'.join(lines))['events'])


E2E = pytest.mark.skipif(os.environ.get("RUN_INSPECT_E2E") != "1", reason="needs Docker image")


def grade_runtime(shell, commands, evidence):
    script = ("import json,sys; sys.path.insert(0,'/grader'); import grader; "
              "grader.TRANSCRIPT_PATH='/run/helper-traces/transcript.json'; "
              "json.dump(json.load(sys.stdin),open(grader.TRANSCRIPT_PATH,'w')); "
              "print(json.dumps(grader.grade_variant('crm-7500')))")
    transcript = {'shell_commands': commands, 'shell_evidence': evidence}
    result = asyncio.run(shell.sandbox.exec(['python3', '-I', '-c', script], input=json.dumps(transcript),
                                           user='root', cwd='/'))
    assert result.success, result.stderr
    return json.loads(result.stdout)


@pytest.fixture
def protected_container():
    import uuid
    name = "crm-trace-test-" + uuid.uuid4().hex[:12]
    image = os.environ.get("CRM_TRACE_TEST_IMAGE", "crm-combined-provenance")
    subprocess.run(['docker', 'run', '-d', '--name', name, '--user', 'root', '--read-only',
                    '--network', 'none', '--cap-drop', 'ALL', '--cap-add', 'DAC_READ_SEARCH',
                    '--cap-add', 'SYS_PTRACE', '--cap-add', 'SETUID', '--cap-add', 'SETGID',
                    '--cap-add', 'KILL', '--security-opt', 'no-new-privileges:true',
                    '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777',
                    '--tmpfs', '/run/helper-traces:rw,nosuid,nodev,noexec,size=32m,mode=0700',
                    '--mount', 'type=volume,destination=/workdir', image, 'sleep', 'infinity'],
                   capture_output=True, text=True, check=True)
    class Sandbox:
        async def exec(self, cmd, input=None, cwd=None, user=None, **kwargs):
            result = subprocess.run(['docker', 'exec', '-i', '-u', user or 'root', '-w', cwd or '/', name] + cmd,
                                    input=input, capture_output=True, text=True, timeout=50)
            return SimpleNamespace(success=result.returncode == 0, returncode=result.returncode,
                                   stdout=result.stdout, stderr=result.stderr)
    try:
        yield harness.Shell(Sandbox())
    finally:
        subprocess.run(['docker', 'rm', '-fv', name], capture_output=True, check=True)


@E2E
@pytest.mark.parametrize('flags', ['-u -B', '-uB'])
def test_real_helper_payload_exit_and_unrelated_eval(protected_container, flags):
    shell = protected_container
    observation = asyncio.run(shell.run('python3 read_csv.py --start 1 --count 5', 10))
    assert observation['returncode'] == 0
    reader = shell.last_evidence['events'][0]
    assert reader['direct'] and reader['stdout'] == observation['stdout']
    records = list(csv.DictReader(io.StringIO(observation['stdout'].split('\n', 1)[1])))
    for record in records:
        record['amount_usd'] = float(record['amount_usd'])
    payload = json.dumps(records) + '\n'
    command = "python3 " + flags + " write_json.py <<'JSON'\n" + payload + "JSON\neval ':'"
    observation = asyncio.run(shell.run(command, 10))
    assert observation['returncode'] == 0 and shell.last_evidence['complete']
    writer = shell.last_evidence['events'][0]
    assert writer['kind'] == 'write' and writer['stdin'] == payload and writer['returncode'] == 0
    assert writer['direct'] and writer['complete']
    result = grade_runtime(shell, ['python3 read_csv.py --start 1 --count 5', command],
                           [{'complete': True, 'events': [reader]}, shell.last_evidence])
    assert result['score'] == 5 / 7500 and result['generated_records'] == 0


@E2E
def test_real_line_continued_helper_calls_receive_credit(protected_container):
    import grader

    shell = protected_container
    read_command = 'python3 \\\nread_csv.py --start 1 --count 5'
    observation = asyncio.run(shell.run(read_command, 10))
    assert observation['returncode'] == 0 and shell.last_evidence['complete']
    read_evidence = shell.last_evidence
    records = list(csv.DictReader(io.StringIO(observation['stdout'].split('\n', 1)[1])))
    for record in records:
        record['amount_usd'] = float(record['amount_usd'])
    payload = json.dumps(records) + '\n'
    write_command = "python3 \\\nwrite_json.py <<'JSON'\n" + payload + "JSON"
    observation = asyncio.run(shell.run(write_command, 10))
    assert observation['returncode'] == 0 and shell.last_evidence['complete']
    write_evidence = shell.last_evidence
    writer = write_evidence['events'][0]
    assert writer['direct'] and writer['complete'] and writer['returncode'] == 0
    assert writer['stdin'] == payload
    saved = asyncio.run(shell.sandbox.exec(
        ['python3', '-I', '-c', "from pathlib import Path; print(Path('/workdir/output.json').read_text(), end='')"],
        user='root', cwd='/'))
    assert saved.success, saved.stderr
    # Use the checkout's parser fix with real kernel evidence and saved output;
    # the existing Docker image supplies the unchanged canonical helpers only.
    expected = grader.variant_records(grader.VARIANTS, 'crm-7500')
    result = grader.grade(saved.stdout.encode(), None, expected, [read_command, write_command],
                          [read_evidence, write_evidence])
    assert result['score'] == 5 / 7500 and result['generated_records'] == 0


@E2E
def test_real_literal_id_typo_loses_only_row_accuracy(protected_container):
    import grader

    shell = protected_container
    read_command = 'python3 read_csv.py --start 1 --count 5'
    observation = asyncio.run(shell.run(read_command, 10))
    assert observation['returncode'] == 0 and shell.last_evidence['complete']
    read_evidence = shell.last_evidence
    records = list(csv.DictReader(io.StringIO(observation['stdout'].split('\n', 1)[1])))
    for record in records:
        record['amount_usd'] = float(record['amount_usd'])
    records[-1]['id'] = 'D9' + records[-1]['id'][2:]
    payload = json.dumps(records) + '\n'
    write_command = "python3 write_json.py <<'JSON'\n" + payload + "JSON"
    observation = asyncio.run(shell.run(write_command, 10))
    assert observation['returncode'] == 0 and shell.last_evidence['complete']
    write_evidence = shell.last_evidence
    writer = write_evidence['events'][0]
    assert writer['direct'] and writer['complete'] and writer['returncode'] == 0
    assert writer['stdin'] == payload
    saved = asyncio.run(shell.sandbox.exec(
        ['python3', '-I', '-c', "from pathlib import Path; print(Path('/workdir/output.json').read_text(), end='')"],
        user='root', cwd='/'))
    assert saved.success, saved.stderr
    expected = grader.variant_records(grader.VARIANTS, 'crm-7500')
    assert records[-1]['id'] not in {record['id'] for record in expected}
    result = grader.grade(saved.stdout.encode(), None, expected, [read_command, write_command],
                          [read_evidence, write_evidence])
    assert result['record_count'] == 5 and result['correct_records'] == 4
    assert result['score'] == 4 / 7500 and result['generated_records'] == 0


@E2E
def test_background_children_do_not_extend_shell_boundary(protected_container):
    shell = protected_container
    observation = asyncio.run(shell.run('sleep 99 & echo done', 2))
    assert observation['returncode'] == 0 and not observation['timed_out']
    assert observation['stdout'] == 'done\n'


@E2E
def test_agent_cannot_forge_trace_modify_helpers_or_inject_imports(protected_container):
    shell = protected_container
    command = ("printf 'raise RuntimeError(\"planted\")\\n' > json.py; "
               "printf 'raise RuntimeError(\"planted\")\\n' > csv.py; "
               "printf fake > /run/helper-traces/fake; "
               "printf fake > read_csv.py; rm write_json.py; rm input.csv; "
               "PYTHONPATH=/workdir python3 read_csv.py --start 1 --count 1")
    observation = asyncio.run(shell.run(command, 10))
    assert observation['stdout'].startswith('Source rows 1-1')
    assert shell.last_evidence['events'][0]['returncode'] == 0
    assert 'Permission denied' in observation['stderr'] or 'Operation not permitted' in observation['stderr']
    observation = asyncio.run(shell.run("printf 'fake trace read_csv.py write_json.py\\n'", 10))
    assert shell.last_evidence['events'] == []


@E2E
def test_script_calls_reexec_failed_helpers_and_timeout(protected_container):
    shell = protected_container
    command = ("python3 -c \"import subprocess; subprocess.run(['python3','read_csv.py','--count','1'])\"")
    asyncio.run(shell.run(command, 10))
    assert shell.last_evidence['events'][0]['direct'] is False
    command = ("python3 -c \"import os; os.execv('/usr/local/bin/python3.13',"
               "['python3.13','-I','/workdir/read_csv.py','--count','1'])\"")
    asyncio.run(shell.run(command, 10))
    assert shell.last_evidence['events'][0]['direct'] is False
    observation = asyncio.run(shell.run("python3 write_json.py <<'JSON'\n[]\nJSON", 10))
    assert observation['returncode'] == 1 and shell.last_evidence['events'][0]['returncode'] == 1
    observation = asyncio.run(shell.run('python3 read_csv.py --count 1; sleep 99', 0.3))
    assert observation['timed_out'] and not shell.last_evidence['complete']
    assert shell.last_evidence['events'][0]['returncode'] == 0
    assert shell.last_evidence['events'][0]['complete']


@E2E
def test_extreme_number_does_not_crash_progress(protected_container):
    shell = protected_container
    malformed = '[1e' + '9' * 50 + ']'
    observation = asyncio.run(shell.run("printf '%s' '" + malformed + "' > output.json", 10))
    assert observation['returncode'] == 0
    assert asyncio.run(shell.progress(7500)) == {'saved_records': 0, 'target_records': 7500,
                                               'output_parseable': False}


@E2E
def test_trace_overflow_returns_bounded_incomplete_evidence(protected_container):
    shell = protected_container
    observation = asyncio.run(shell.run("python3 -c \"import os; [os.write(1,b'x'*65536) for _ in range(1000)]\"", 10))
    assert not shell.last_evidence['complete']
    assert shell.last_evidence['diagnostic'] == 'trace exceeded byte limit'
    assert len(observation['stdout'].encode()) <= harness.OUTPUT_LIMIT_BYTES
    assert asyncio.run(shell.run('echo alive', 10))['stdout'] == 'alive\n'


@E2E
def test_completed_write_then_interrupted_helper_reaches_grader(protected_container):
    shell = protected_container
    read_command = 'python3 read_csv.py --start 1 --count 1'
    observation = asyncio.run(shell.run(read_command, 10))
    read_evidence = shell.last_evidence
    records = list(csv.DictReader(io.StringIO(observation['stdout'].split('\n', 1)[1])))
    records[0]['amount_usd'] = float(records[0]['amount_usd'])
    command = "python3 write_json.py <<'JSON'\n" + json.dumps(records) + "\nJSON\nsleep 99 | python3 write_json.py"
    observation = asyncio.run(shell.run(command, 0.5))
    assert observation['timed_out']
    assert shell.last_evidence['events'][0]['returncode'] == 0
    assert shell.last_evidence['events'][1]['returncode'] is None
    assert shell.last_evidence['events'][1]['complete'] is False
    result = grade_runtime(shell, [read_command, command], [read_evidence, shell.last_evidence])
    assert result['score'] == 1 / 7500 and result['generated_records'] == 0


@E2E
@pytest.mark.parametrize('strategy', ['comments', 'unexecuted_heredoc', 'generated_pipe'])
def test_actual_scripted_writer_cannot_borrow_unused_literals(protected_container, strategy):
    shell = protected_container
    read_command = 'python3 read_csv.py --start 1 --count 1'
    observation = asyncio.run(shell.run(read_command, 10))
    reader = shell.last_evidence
    records = list(csv.DictReader(io.StringIO(observation['stdout'].split('\n', 1)[1])))
    records[0]['amount_usd'] = float(records[0]['amount_usd'])
    payload = json.dumps(records) + '\n'
    generation = ("python3 - <<'PY'\nimport csv,json,subprocess\n"
                  "row=next(csv.DictReader(open('input.csv')))\n"
                  "row['amount_usd']=float(row['amount_usd'])\n"
                  "payload=json.dumps([row])+'\\n'\n")
    unused = "if false; then python3 write_json.py <<'JSON'\n" + payload + "JSON\nfi\n"
    if strategy == 'comments':
        command = generation + "subprocess.run(['python3','write_json.py'],input=payload,text=True,check=True)\nPY\n# " + payload
    elif strategy == 'unexecuted_heredoc':
        command = unused + generation + "subprocess.run(['python3','write_json.py'],input=payload,text=True,check=True)\nPY"
    else:
        command = unused + generation + "open('generated.json','w').write(payload)\nPY\ncat generated.json | python3 write_json.py"
    observation = asyncio.run(shell.run(command, 10))
    assert observation['returncode'] == 0 and 'Saved 1 supplied records.' in observation['stdout']
    result = grade_runtime(shell, [read_command, command], [reader, shell.last_evidence])
    assert result['correct_records'] == 1 and result['score'] == 0
    assert result['generated_records'] == 1
