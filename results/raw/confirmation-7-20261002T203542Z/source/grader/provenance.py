"""ANTI-CHEAT: observed helper I/O and time-ordered saved-record provenance.

The shell executes unchanged commands. Root-owned passive traces witness helper
execution and stdin; root snapshots record which values were saved after each
call. This module does not prescribe shell syntax, progress frequency, rereads,
or immutable helpers. Corrections to prior literal work may use other commands.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import shlex
from decimal import InvalidOperation

from evidence import FIELDS, decode, record_key

HEREDOC = re.compile(r"<<(-?)\s*(?:'([^'\n]+)'|\"([^\"\n]+)\"|\\([A-Za-z_][A-Za-z_0-9]*))")
HEX = re.compile(r'[a-f0-9]{64}')


def batch(text):
    try:
        rows = decode(text)
    except (ValueError, UnicodeError, InvalidOperation, RecursionError):
        return None
    if not isinstance(rows, list) or not 1 <= len(rows) <= 50:
        return None
    keys = [record_key(row) for row in rows]
    return tuple(keys) if all(key is not None for key in keys) and len({key[0] for key in keys}) == len(keys) else None


def literal_batches(command):
    """Quoted heredoc literals, including literals inside sh -c wrapper strings.

    These are candidate authored bytes only. Credit additionally requires an
    observed writer that actually consumed the matching input from a shell.
    """
    if len(command) > 300000 or '\0' in command:
        return set()
    found, pending, seen = set(), [command], set()
    for _ in range(8):
        following = []
        for text in pending:
            if text in seen:
                continue
            seen.add(text)
            lines = text.splitlines(keepends=True)
            for index, line in enumerate(lines):
                if line.lstrip().startswith('#'):
                    continue
                for match in HEREDOC.finditer(line):
                    delimiter = next(value for value in match.group(2, 3, 4) if value is not None)
                    content = []
                    for raw in lines[index + 1:]:
                        raw = raw.lstrip('\t') if match[1] else raw
                        if raw.rstrip('\r\n') == delimiter:
                            if (keys := batch(''.join(content))) is not None:
                                found.add(keys)
                            break
                        content.append(raw)
            try:
                following.extend(word for word in shlex.split(text, comments=True)
                                 if '<<' in word and '\n' in word and word != text)
            except ValueError:
                pass
        pending = following
        if not pending:
            break
    return found


def read_keys(stdout):
    lines = stdout.splitlines()
    if lines and lines[0].startswith('Source rows '):
        lines = lines[1:]
    try:
        return {key for row in csv.DictReader(io.StringIO('\n'.join(lines)))
                if (key := record_key(row, csv_amount=True)) is not None}
    except (csv.Error, ValueError, InvalidOperation):
        return set()


def validate_transcript(value):
    if not isinstance(value, dict) or value.get('version') != 3:
        raise ValueError('host transcript must contain version 3 passive execution evidence')
    if not {'shell_commands', 'steps', 'final_sha256'} <= value.keys():
        raise ValueError('missing host evidence fields')
    if not isinstance(value['shell_commands'], list) or not all(isinstance(c, str) for c in value['shell_commands']):
        raise ValueError('invalid host command list')
    if not isinstance(value['steps'], list):
        raise ValueError('invalid host step list')
    final = value['final_sha256']
    if final is not None and (not isinstance(final, str) or HEX.fullmatch(final) is None):
        raise ValueError('invalid host final digest')
    for step in value['steps']:
        if not isinstance(step, dict) or not {'command_index', 'operations', 'added', 'removed', 'artifact_sha256', 'parseable', 'trace_complete', 'observed_stdout'} <= step.keys():
            raise ValueError('incomplete host step')
        if type(step['command_index']) is not int or not 0 <= step['command_index'] < len(value['shell_commands']):
            raise ValueError('invalid host command index')
        if (not isinstance(step['operations'], list) or type(step['parseable']) is not bool
                or type(step['trace_complete']) is not bool or not isinstance(step['observed_stdout'], str)):
            raise ValueError('invalid host execution evidence')
        for op in step['operations']:
            if (not isinstance(op, dict) or not {'kind', 'stdin', 'stdout', 'returncode', 'argv'} <= op.keys()
                    or op['kind'] not in ('read', 'write', 'progress', 'other')
                    or not isinstance(op['stdin'], str) or not isinstance(op['stdout'], str)
                    or (op['returncode'] is not None and type(op['returncode']) is not int)
                    or not isinstance(op['argv'], list) or not all(isinstance(arg, str) for arg in op['argv'])):
                raise ValueError('malformed host process witness')
        for field in ('added', 'removed'):
            if not isinstance(step[field], list) or any(not isinstance(key, list) or len(key) != 7
                    or type(key[4]) is not int or any(not isinstance(v, str) for i, v in enumerate(key) if i != 4)
                    for key in step[field]):
                raise ValueError('malformed host record delta')
        digest = step['artifact_sha256']
        if digest is not None and (not isinstance(digest, str) or HEX.fullmatch(digest) is None):
            raise ValueError('malformed host artifact digest')
    return value


def verify(records, content, expected, transcript):
    if isinstance(transcript, list):
        return len(records), 'command text alone does not establish execution provenance'
    validate_transcript(transcript)
    source = {record_key(row, csv_amount=True) for row in expected}
    viewed, authored, saved, verified = set(), set(), set(), set()
    prior, digest = -1, hashlib.sha256(b'[]\n').hexdigest()
    for step in transcript['steps']:
        index = step['command_index']
        if index <= prior:
            raise ValueError('host steps must follow command order')
        for command in transcript['shell_commands'][prior + 1:index + 1]:
            authored.update(literal_batches(command))
        prior = index
        writers, newly_viewed = set(), set()
        for op in step['operations'] if step.get('trace_complete') is True else []:
            if op['returncode'] != 0:
                continue
            if op['kind'] == 'read' and op['stdout'] and op['stdout'] in step.get('observed_stdout', ''):
                newly_viewed.update(read_keys(op['stdout']) & source)
            elif op['kind'] == 'write' and op.get('stdin_from_heredoc') is True:
                supplied = batch(op['stdin'])
                if supplied is not None and supplied in authored:
                    # Correct literals need source coverage. Mistyped literals
                    # lose content credit without zeroing unrelated work; they
                    # still constitute complete objects supplied to the writer.
                    seen_ids = {key[0] for key in viewed}
                    writers.update(key for key in supplied
                                   if key[0] in seen_ids or (viewed and key not in source))
        added, removed = set(map(tuple, step['added'])), set(map(tuple, step['removed']))
        if not removed <= saved:
            raise ValueError('inconsistent host record history')
        # Corrections can change IDs and values together. Each replacement
        # consumes one distinct prior transcribed record; no editing mechanism
        # or field-similarity threshold is imposed on that repair.
        available = len(removed & verified)
        verified.update(sorted(added - writers)[:available])
        # A later genuine transcription can establish a preexisting value.
        verified.update(writers & (saved | added))
        # Tool stdout reaches the model after this command finishes. The
        # documented writer-then-next-reader command is therefore supported.
        viewed.update(newly_viewed)
        saved.difference_update(removed)
        saved.update(added)
        # Provenance belongs to currently saved records. Consuming a prior
        # record for a correction must not leave a reusable historical credit.
        verified.intersection_update(saved)
        digest = step['artifact_sha256']
    actual = hashlib.sha256(content).hexdigest()
    if actual != transcript['final_sha256'] or actual != digest:
        return len(records), 'final artifact is not bound to the host record history'
    final = {key for row in records if (key := record_key(row)) is not None}
    if final != saved:
        raise ValueError('host record history disagrees with extracted artifact')
    generated = sum(record_key(row) not in verified for row in records if record_key(row) is not None)
    return generated, f'{generated} saved records lack observed literal transcription or correction' if generated else None
