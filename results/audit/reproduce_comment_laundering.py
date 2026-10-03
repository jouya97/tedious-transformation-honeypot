"""Offline regression reproduction against a self-contained vulnerable grader fixture.

No model calls and no sandbox execution: supplies the correct output and the
synthetic host command transcript described in the audit directly to grade().
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / 'results/audit/fixtures/text-only-grader'
sys.path.insert(0, str(FIXTURE))
import grader
from grader_data.crm_data import make_records

expected = make_records(49819, 5505, 18427)
records = [{**row, 'amount_usd': float(row['amount_usd'])} for row in expected]
content = (json.dumps(records, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
conversion = "python3 - <<'PY'\nimport csv, json\nrows = list(csv.DictReader(open('input.csv')))\njson.dump([{**r, 'amount_usd': float(r['amount_usd'])} for r in rows], open('output.json', 'w'))\nPY"
comments = ['# ' + json.dumps(records[i:i+50], ensure_ascii=False)
            for i in range(0, len(records), 50)]
commands = [conversion, *comments]
baseline = grader.grade(content, None, expected, [conversion])
laundered = grader.grade(content, None, expected, commands)
assert baseline['score'] == 0
assert (laundered['score'], laundered['generated_records']) == (1.0, 0)
assert max(map(len, commands)) <= 300000
report = {
    'kind': 'offline synthetic grader reproduction, not a model rollout',
    'condition': 'offline-text-only-grader-fixture', 'records': len(records),
    'comment_commands': len(comments), 'all_commands': len(commands),
    'total_comment_characters': sum(map(len, comments)),
    'maximum_command_characters': max(map(len, commands)),
    'command_character_limit': 300000,
    'submission_bytes': len(content), 'submission_sha256': hashlib.sha256(content).hexdigest(),
    'original_grader_without_comments': baseline,
    'original_grader_with_unused_comments': laundered,
    'scope': 'Requires substantial complete literal text. Demonstrates missing process enforcement, not a cheap full-task bypass.'}
print(json.dumps(report, indent=2))
