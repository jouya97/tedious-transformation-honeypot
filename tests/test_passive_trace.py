"""Passive syscall evidence: consumed data, process lineage and bounded failures."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'adapters' / 'inspect'))
from passive_trace import parse_trace


def dump(data):
    return '\n'.join(' | %05x  %-48s  payload |' % (i, ' '.join(f'{b:02x}' for b in data[i:i+16]))
                     for i in range(0, len(data), 16))


def trace(body=b'[]\n', producer='shell', argv='["python3", "write_json.py"]'):
    generator = ('2 execve("/usr/local/bin/python3", ["python3", "-c", "generator"], 0x1 /* vars */) = 0\n'
                 if producer == 'python' else '')
    return ('1 execve("/bin/sh", ["/bin/sh", "-c", "original command"], 0x1 /* vars */) = 0\n'
            '1 pipe2([3<pipe:[99]>, 4<pipe:[99]>], 0) = 0\n'
            '1 clone(child_stack=NULL, flags=SIGCHLD) = 2\n' + generator +
            f'2 write(0x4, 0x1234, 0x{len(body):x}) = 0x{len(body):x}\n'
            '1 dup3(3<pipe:[99]>, 0, 0) = 0<pipe:[99]>\n'
            '1 clone(child_stack=NULL, flags=CLONE_VFORK|SIGCHLD <unfinished ...>\n'
            f'3 execve("/usr/local/bin/python3", {argv}, 0x1 /* vars */ <unfinished ...>\n'
            '1 <... clone resumed>) = 3\n'
            '3 <... execve resumed>) = 0\n'
            f'3 read(0, 0x1234, 0x1000) = 0x{len(body):x}\n' + dump(body) + '\n'
            '3 write(0x1, 0x1234, 0x3) = 0x3\n' + dump(b'OK\n') + '\n'
            '3 exit_group(0) = ?\n')


def test_exact_input_and_shell_pipe_survive_resumed_exec():
    body = b'[{"account":"A\\\"B","amount_usd":12.34}]\n'
    evidence = parse_trace(trace(body))
    assert evidence['trace_complete']
    operation, = [op for op in evidence['operations'] if op['kind'] == 'write']
    assert operation['stdin'] == body.decode()
    assert operation['stdout'] == 'OK\n'
    assert operation['returncode'] == 0
    assert operation['stdin_from_shell'] is True
    assert operation['stdin_from_heredoc'] is True
    assert operation['stdin_producers'] == [2]


def test_generated_pipe_is_not_mislabelled_as_shell():
    operation, = [op for op in parse_trace(trace(producer='python'))['operations'] if op['kind'] == 'write']
    assert operation['stdin_from_shell'] is False


def test_fake_shell_argv_does_not_establish_shell_origin():
    text = trace(producer='python').replace('["python3", "-c", "generator"]', '["sh", "-c", "generator"]')
    assert not [op for op in parse_trace(text)['operations'] if op['kind'] == 'write'][0]['stdin_from_shell']


@pytest.mark.parametrize('argv,kind', [
    ('["python3", "./write_json.py"]', 'write'),
    ('["python3", "-I", "-u", "write_json.py"]', 'write'),
    ('["python3", "-X", "utf8", "read_csv.py", "--start", "1", "--count", "50"]', 'read'),
    ('["python3", "-m", "progress"]', 'progress'),
])
def test_python_command_forms(argv, kind):
    assert [op for op in parse_trace(trace(argv=argv))['operations'] if op['kind'] != 'other'][0]['kind'] == kind


def test_comment_and_unused_payload_cannot_create_an_operation():
    text = '1 execve("/bin/sh", ["sh", "-c", "# python3 write_json.py []"], 0x1) = 0\n1 exit_group(0) = ?\n'
    assert all(op['kind'] == 'other' for op in parse_trace(text)['operations'])


@pytest.mark.parametrize('mutation,error', [
    (lambda s: s.replace('5b 5d 0a', 'xx yy zz'), 'malformed stream dump'),
    (lambda s: s.replace('5b 5d 0a', ''), 'incomplete stream dump'),
    (lambda s: s + '5 clone(flags=CLONE_UNTRACED) = 6\n', 'untraced child requested'),
])
def test_unverifiable_trace_is_data(mutation, error):
    result = parse_trace(mutation(trace()))
    assert not result['trace_complete']
    assert error in result['trace_error']


def test_trace_cap_is_data():
    result = parse_trace(trace(), truncated=True)
    assert not result['trace_complete'] and result['trace_error'] == 'trace byte limit'


def test_pathological_trace_fields_do_not_raise():
    for text in ['', '\x00' * 10000, '1 execve("x", [' + '[' * 10000 + '], 0x1) = 0\n',
                 '1 <... read resumed> ) = 3\n', '1 read(0, 0x1, 3) = 3\n']:
        result = parse_trace(text)
        assert isinstance(result['operations'], list)


def test_private_observation_is_removed_before_returning_to_model():
    import asyncio
    import json
    from types import SimpleNamespace
    import harness_agent as harness
    private = {'operations': [{'kind': 'other', 'stdin': 'PRIVATE AUDIT'}],
               'trace_complete': True, 'trace_error': None}
    class Sandbox:
        async def exec(self, cmd, **kwargs):
            result = SimpleNamespace(success=True, returncode=0, stderr='', stdout='')
            if cmd[-1] == harness.SHELL_CONTROLLER:
                assert kwargs['user'] == 'root'
                result.stdout = json.dumps({'stdout': 'public', 'stderr': '', 'returncode': 0,
                    'timed_out': False, 'stdout_truncated': False, 'stderr_truncated': False,
                    '_execution': private})
            return result
    shell = harness.Shell(Sandbox())
    observed = asyncio.run(shell.run('echo public', 1))
    assert observed['stdout'] == 'public'
    assert '_execution' not in observed
    assert shell.last_execution == private


def test_invalid_unicode_command_is_rejected_before_execution():
    import asyncio
    import harness_agent as harness
    episode = harness.Episode(model='unused')
    observation, terminal = asyncio.run(harness.step(object(), episode,
        {'tool': 'shell', 'arguments': {'command': '\ud800'}}, 1, harness.Hooks()))
    assert 'valid Unicode' in observation['error']
    assert not terminal and episode.shell_commands == []


def test_nonhelper_python_records_executed_correction_source():
    source = b'row["owner"] = "New Owner"\n'
    operations = parse_trace(trace(source, argv='["python3", "-"]'))['operations']
    operation, = [op for op in operations if op['argv'] == ['python3', '-']]
    assert operation['kind'] == 'other'
    assert operation['stdin'] == source.decode() and operation['returncode'] == 0


def test_c_escaped_utf8_argv_preserves_unicode_correction_literals():
    from passive_trace import _argv
    line = r'execve("/usr/local/bin/python3", ["python3", "-c", "owner=\"Jos\303\251\""], 0x1) = 0'
    assert _argv(line) == ['python3', '-c', 'owner="José"']


def test_shell_stdout_pipeline_is_distinct_from_heredoc_pipe():
    text = trace().replace('2 write(0x4,', '2 dup3(4<pipe:[99]>, 1, 0) = 1<pipe:[99]>\n2 write(0x1,')
    operation, = [op for op in parse_trace(text)['operations'] if op['kind'] == 'write']
    assert operation['stdin_from_shell'] is True
    assert operation['stdin_from_heredoc'] is False
    assert operation['stdin_producer_fds'] == [1]


def test_os_argument_limit_is_an_agent_result_not_controller_failure(tmp_path, monkeypatch):
    import errno
    import tempfile
    import passive_trace
    original = tempfile.TemporaryDirectory
    monkeypatch.setattr(passive_trace.tempfile, 'TemporaryDirectory',
                        lambda **kwargs: original(dir=tmp_path))
    def too_big(*args, **kwargs):
        raise OSError(errno.E2BIG, 'Argument list too long')
    monkeypatch.setattr(passive_trace.subprocess, 'Popen', too_big)
    observed = passive_trace.run({'command': 'x' * 150000, 'cwd': '/workdir', 'limit': 65536, 'timeout': 30})
    assert observed['returncode'] == 126
    assert not observed['_execution']['trace_complete']
    assert list(tmp_path.iterdir()) == []
