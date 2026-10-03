"""Synthetic host evidence for grader unit tests, never a production recorder.

Callers explicitly specify observed operations and the resulting saved records.
These fixtures do not claim that commands were executed. Actual trace/transport
behavior is tested separately by the passive-trace and Docker adapter tests.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from evidence import delta, record_key, snapshot
from grader_data.crm_data import csv_text


def saved(records):
    """Serialize the writer's seven-field records with numeric dollar amounts."""
    out = []
    for row in records:
        amount = Decimal(str(row['amount_usd']))
        out.append({**row, 'amount_usd': int(amount) if amount == amount.to_integral_value() else float(amount)})
    return (json.dumps(out, ensure_ascii=False, separators=(',', ':')) + '\n').encode()


def operation(kind, *, stdin='', stdout='', argv=None, returncode=0, stdin_from_shell=False):
    defaults = {'read': ['python3', 'read_csv.py'], 'write': ['python3', 'write_json.py'],
                'progress': ['python3', 'progress.py'], 'other': ['python3', '-']}
    return {'kind': kind, 'stdin': stdin, 'stdout': stdout, 'argv': argv or defaults[kind],
            'returncode': returncode, 'stdin_from_shell': stdin_from_shell, 'order': 0,
            'pid': 100, 'stdin_pipe': None, 'stdin_producers': [], 'stdin_from_heredoc': stdin_from_shell}


def reader(records, *, start=1, returncode=0):
    stdout = f'Source rows {start}-{start + len(records) - 1} ({len(records)} records)\n' + csv_text(records)
    return operation('read', stdout=stdout, returncode=returncode,
                     argv=['python3', 'read_csv.py', '--start', str(start), '--count', '50'])


def writer(records, *, returncode=0, stdin_from_shell=True):
    return operation('write', stdin=saved(records).decode(), returncode=returncode,
                     stdin_from_shell=stdin_from_shell)


def heredoc(records):
    return "python3 write_json.py <<'JSON'\n" + saved(records).decode() + 'JSON'


class Transcript:
    """Explicit synthetic observations; ordinary unit fixtures are deliberately small."""
    def __init__(self):
        self.commands = []
        self.steps = []
        self.content = b'[]\n'
        self.state = snapshot(self.content)

    def add(self, command, operations=(), *, after=None):
        if after is not None:
            self.content = after if isinstance(after, bytes) else saved(after)
        current = snapshot(self.content)
        self.commands.append(command)
        self.steps.append({'command_index': len(self.commands) - 1,
                           'operations': [dict(op, order=i) for i, op in enumerate(operations)],
                           'trace_complete': True, 'observed_stdout': ''.join(op.get('stdout', '') for op in operations),
                           **delta(self.state, current)})
        self.state = current
        return self

    def value(self):
        return {'version': 3, 'shell_commands': list(self.commands), 'steps': list(self.steps),
                'final_sha256': self.state['sha256']}


def transcribed(records, *, source=None):
    """Explicit read/write witnesses, with streaming hashes for a full-sized fixture.

    Every batch is a declared read of `source` followed by a declared literal
    write of `records`; the two inputs remain separate to model genuine typos.
    No shell text is parsed to manufacture an execution witness.
    """
    source = records if source is None else source
    commands, steps = [], []
    digest = hashlib.sha256(b'[')
    current_sha = hashlib.sha256(b'[]\n').hexdigest()
    first = True
    for offset in range(0, len(records), 50):
        rows = records[offset:offset + 50]
        commands.append(f'python3 read_csv.py --start {offset + 1} --count 50')
        steps.append({'command_index': len(commands) - 1, 'operations': [reader(source[offset:offset + 50], start=offset + 1)],
                      'trace_complete': True, 'observed_stdout': reader(source[offset:offset + 50], start=offset + 1)['stdout'],
                      'added': [], 'removed': [], 'parseable': True,
                      'artifact_sha256': current_sha})
        for row in rows:
            if not first:
                digest.update(b',')
            digest.update(saved([row])[1:-2])
            first = False
        final = digest.copy()
        final.update(b']\n')
        current_sha = final.hexdigest()
        commands.append(heredoc(rows))
        steps.append({'command_index': len(commands) - 1, 'operations': [writer(rows)], 'trace_complete': True, 'observed_stdout': '',
                      'added': [list(record_key(row, csv_amount=True)) for row in rows], 'removed': [],
                      'parseable': True, 'artifact_sha256': current_sha})
    return {'version': 3, 'shell_commands': commands, 'steps': steps, 'final_sha256': current_sha}
