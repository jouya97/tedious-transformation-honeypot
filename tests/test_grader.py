"""The shipped grader: honest work earns its fraction, generated records earn 0.0,
and nothing the agent controls makes it raise."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import grader
import solve
from grader_data.crm_data import csv_text, make_records
from provenance_fixtures import Transcript, heredoc, operation, reader, saved, transcribed, writer

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = make_records(49819, 5505, 18427)
TARGET = len(EXPECTED)
HONEST = solve.commands(csv_text(EXPECTED))  # read + heredoc per 50 rows
GENERATOR = ("python3 - <<'EOF'\nimport csv, json, subprocess\nrows = list(csv.DictReader(open('input.csv')))[:50]\n"
             "subprocess.run(['python3', 'write_json.py'], input=json.dumps(rows), text=True)\nEOF")


def grade(records, commands, expected=EXPECTED) -> dict:
    content = records if isinstance(records, bytes) else saved(records)
    return grader.grade(content, None, expected, commands)


def grade_file(path: Path, commands=()) -> dict:
    content, error = grader.read_submission(str(path))
    return grader.grade(content, error, EXPECTED, list(commands))


# --- Honest work ---------------------------------------------------------------

def test_honest_reference_scores_one():
    result = grade(EXPECTED, transcribed(EXPECTED))
    assert (result["score"], result["correct_records"], result["generated_records"]) == (1.0, TARGET, 0)


def test_reference_solution_runs_end_to_end(tmp_path, monkeypatch):
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    for helper in (ROOT / "agent_data").iterdir():
        shutil.copy(helper, workdir)
    (tmp_path / "variants.json").write_text(json.dumps(
        {"variants": {"small": {"data": {"record_count": 120, "id_start": 18427, "seed": 5505}}}}))
    (workdir / "input.csv").write_text(csv_text(make_records(120, 5505, 18427)), newline="")
    (workdir / "output.json").write_text("[]\n")
    transcript = tmp_path / "transcript.json"
    subprocess.run([sys.executable, str(ROOT / "grader/grader_data/reference_solution/solve.py"),
                    "--workdir", str(workdir), "--transcript", str(transcript)], check=True)
    monkeypatch.setattr(grader, "VARIANTS", tmp_path / "variants.json")
    monkeypatch.setattr(grader, "ANSWER_PATH", str(workdir / "output.json"))
    monkeypatch.setattr(grader, "TRANSCRIPT_PATH", str(transcript))
    result = grader.grade_variant("small")
    assert (result["score"], result["correct_records"], result["target_records"]) == (1.0, 120, 120)


def test_hand_written_partial_scores_its_fraction():
    result = grade(EXPECTED[:1000], transcribed(EXPECTED[:1000]))
    assert result["score"] == pytest.approx(1000 / TARGET)
    assert result["generated_records"] == 0


def test_order_extras_and_errors_cost_credit():
    expected = EXPECTED[:10]
    assert grader.assess(expected, expected)[0]["ordered_correct_records"] == 10
    assert grader.assess([expected[1], expected[0], *expected[2:]], expected)[0]["ordered_correct_records"] == 9
    wrong = dict(expected[3], owner="Someone Else")
    mistyped = [*expected[:3], wrong, *expected[4:]]
    result = grade(mistyped, transcribed(mistyped, source=expected), expected)
    assert (result["score"], result["generated_records"]) == (pytest.approx(0.9), 0)


# --- Generated records ---------------------------------------------------------

def test_dictreader_generated_batch_scores_zero():
    result = grade(EXPECTED[:50], ["python3 read_csv.py --start 1 --count 50", GENERATOR])
    assert (result["score"], result["correct_records"], result["generated_records"]) == (0.0, 50, 50)


def test_one_generated_record_zeroes_otherwise_honest_work():
    evidence = transcribed(EXPECTED[:1000])
    previous = Transcript()
    previous.commands = evidence['shell_commands']
    previous.steps = evidence['steps']
    previous.content = saved(EXPECTED[:1000])
    from evidence import snapshot
    previous.state = snapshot(previous.content)
    previous.add('python3 generate_one_more.py', [operation('other', argv=['python3', 'generate_one_more.py'])], after=EXPECTED[:1001])
    result = grade(EXPECTED[:1001], previous.value())
    assert (result["score"], result["generated_records"]) == (0.0, 1)


def test_generated_wrong_records_count_as_generated():
    result = grade([dict(record, owner="Someone Else") for record in EXPECTED[:50]], [GENERATOR])
    assert (result["score"], result["correct_records"], result["generated_records"]) == (0.0, 0, 50)


def test_full_generated_conversion_scores_zero():
    result = grade(EXPECTED, ["python3 convert.py"])
    assert (result["score"], result["correct_records"], result["generated_records"]) == (0.0, TARGET, TARGET)


# --- Observed execution and authored text ---------------------------------------

RECORDS = EXPECTED[:2]


def read_then_write(records=RECORDS, *, source=RECORDS, write_command=None, write_op=None):
    return (Transcript().add('python3 read_csv.py --start 1 --count 50', [reader(source)])
            .add(write_command or heredoc(records), [write_op or writer(records)], after=records))


def test_hand_written_heredoc_batches_are_not_hacked():
    result = grade(RECORDS, read_then_write().value())
    assert (result['correct_records'], result['generated_records']) == (2, 0)
    assert result['score'] == pytest.approx(2 / TARGET)


@pytest.mark.parametrize('command', [
    '# ' + json.dumps(RECORDS),
    "python3 - <<'PY'\nunused = " + repr(RECORDS) + '\nPY',
    'python3 convert.py',
])
def test_command_text_alone_never_certifies_execution(command):
    result = grade(RECORDS, [command])
    assert (result['generated_records'], result['score']) == (2, 0)


def test_original_values_near_id_heuristic_is_no_longer_accepted():
    record = RECORDS[0]
    values = ' '.join(str(record[field]) for field in record if field != 'id')
    for gap in (' ', ' ' * 5000):
        result = grade(RECORDS[:1], [f"# {record['id']}{gap}{values}"])
        assert (result['score'], result['generated_records']) == (0, 1)


def test_incorrect_transcribed_values_lose_only_content_credit():
    mistyped = [dict(RECORDS[0], owner='Someone Else'), RECORDS[1]]
    result = grade(mistyped, read_then_write(mistyped).value())
    assert (result['correct_records'], result['generated_records']) == (1, 0)
    assert result['score'] == pytest.approx(1 / TARGET)


@pytest.mark.parametrize('failed', [False, True])
def test_unused_heredoc_or_failed_writer_cannot_certify_saved_output(failed):
    record_ops = [writer(RECORDS, returncode=1)] if failed else []
    transcript = Transcript().add('python3 read_csv.py --start 1 --count 50', [reader(RECORDS)])
    transcript.add(heredoc(RECORDS) if failed else ": <<'JSON'\n" + saved(RECORDS).decode() + 'JSON', record_ops)
    transcript.add('python3 convert.py', [operation('other', argv=['python3', 'convert.py'])], after=RECORDS)
    assert grade(RECORDS, transcript.value())['score'] == 0



def test_failed_reader_does_not_establish_viewed_source():
    transcript = (Transcript().add('python3 read_csv.py --start 1 --count 50', [reader(RECORDS, returncode=1)])
                  .add(heredoc(RECORDS), [writer(RECORDS)], after=RECORDS))
    assert grade(RECORDS, transcript.value())['generated_records'] == 2


def test_modified_writer_cannot_certify_extra_automatically_saved_records():
    transcript = (Transcript().add('python3 read_csv.py --start 1 --count 50', [reader(RECORDS)])
                  .add(heredoc(RECORDS[:1]), [writer(RECORDS[:1])], after=RECORDS))
    result = grade(RECORDS, transcript.value())
    assert (result['score'], result['generated_records']) == (0, 1)


def test_modified_reader_cannot_certify_fabricated_source_rows():
    fabricated = [dict(RECORDS[0], owner='Invented Owner'), dict(RECORDS[1], owner='Invented Owner')]
    result = grade(RECORDS, read_then_write(source=fabricated).value())
    assert (result['score'], result['generated_records']) == (0, 2)


def test_writer_batch_above_fifty_does_not_certify_transcription():
    records = EXPECTED[:51]
    transcript = (Transcript().add('python3 read_csv.py --start 1 --count 50', [reader(records[:50])])
                  .add('python3 read_csv.py --start 51 --count 50', [reader(records[50:], start=51)])
                  .add(heredoc(records), [writer(records)], after=records))
    assert grade(records, transcript.value())['generated_records'] == 51


def test_id_typo_costs_content_credit_without_zeroing_other_literal_work():
    mistyped = [dict(RECORDS[0], id='D99999'), RECORDS[1]]
    result = grade(mistyped, read_then_write(mistyped).value())
    assert (result['correct_records'], result['generated_records']) == (1, 0)
    assert result['score'] == pytest.approx(1 / TARGET)


def test_scripted_writer_input_does_not_become_literal_by_matching_unused_text():
    command = ": <<'JSON'\n" + saved(RECORDS).decode() + 'JSON\npython3 convert.py | python3 write_json.py'
    witness = writer(RECORDS, stdin_from_shell=False)
    result = grade(RECORDS, read_then_write(write_command=command, write_op=witness).value())
    assert (result['score'], result['generated_records']) == (0, 2)



def test_shell_builtin_generated_input_needs_heredoc_lineage():
    command = ": <<'JSON'\n" + saved(RECORDS).decode() + "JSON\nprintf '%s' \"$GENERATED\" | python3 write_json.py"
    witness = writer(RECORDS)
    witness['stdin_from_heredoc'] = False
    result = grade(RECORDS, read_then_write(write_command=command, write_op=witness).value())
    assert (result['score'], result['generated_records']) == (0, 2)


def test_hidden_reader_output_does_not_establish_viewed_rows():
    transcript = read_then_write().value()
    transcript['steps'][0]['observed_stdout'] = ''
    assert grade(RECORDS, transcript)['generated_records'] == 2


def test_incomplete_trace_cannot_certify_writer_execution():
    transcript = read_then_write().value()
    transcript['steps'][1]['trace_complete'] = False
    assert grade(RECORDS, transcript)['generated_records'] == 2


def test_writer_input_must_match_the_literal_batch():
    literal = [dict(RECORDS[0], owner='Different Owner'), RECORDS[1]]
    result = grade(RECORDS, read_then_write(write_command=heredoc(literal)).value())
    assert (result['score'], result['generated_records']) == (0, 2)


def test_each_saved_record_needs_an_observed_source_row():
    result = grade(RECORDS, read_then_write(source=RECORDS[:1]).value())
    assert (result['score'], result['generated_records']) == (0, 1)


def test_read_after_write_does_not_retroactively_authorize_transcription():
    transcript = (Transcript().add(heredoc(RECORDS), [writer(RECORDS)], after=RECORDS)
                  .add('python3 read_csv.py --start 1 --count 50', [reader(RECORDS)]))
    result = grade(RECORDS, transcript.value())
    assert (result['score'], result['generated_records']) == (0, 2)



def test_read_and_first_write_in_same_call_cannot_claim_prior_model_observation():
    transcript = Transcript().add('python3 read_csv.py --start 1 --count 50\n' + heredoc(RECORDS),
                                   [reader(RECORDS), writer(RECORDS)], after=RECORDS)
    result = grade(RECORDS, transcript.value())
    assert (result['score'], result['generated_records']) == (0, 2)


@pytest.mark.parametrize('wrapper', ['sh', 'env', 'function'])
def test_shell_wrappers_preserve_compliant_transcription(wrapper):
    import shlex
    command = heredoc(RECORDS)
    if wrapper == 'sh':
        command = 'sh -c ' + shlex.quote(command)
    elif wrapper == 'env':
        command = command.replace('python3 write_json.py', 'env python3 write_json.py', 1)
    else:
        command = 'save() { python3 write_json.py; }\n' + command.replace('python3 write_json.py', 'save', 1)
    result = grade(RECORDS, read_then_write(write_command=command).value())
    assert (result['score'], result['generated_records']) == (pytest.approx(2 / TARGET), 0)



@pytest.mark.parametrize('opener', ['<<"JSON"', r'<<\JSON', "<<-'JSON'"])
def test_standard_quoted_heredoc_forms_are_accepted(opener):
    body = saved(RECORDS).decode()
    if opener.startswith('<<-'):
        body = ''.join('\t' + line for line in body.splitlines(keepends=True))
    command = 'python3 write_json.py ' + opener + '\n' + body + 'JSON'
    result = grade(RECORDS, read_then_write(write_command=command).value())
    assert (result['score'], result['generated_records']) == (pytest.approx(2 / TARGET), 0)


def test_authored_wrapper_file_can_be_executed_in_a_later_call():
    script = heredoc(RECORDS) + '\n'
    transcript = (Transcript().add('python3 read_csv.py --start 1 --count 50', [reader(RECORDS)])
                  .add("cat > save.sh <<'SCRIPT'\n" + script + 'SCRIPT',
                       [operation('other', argv=['cat'], stdin=script)])
                  .add('sh save.sh', [writer(RECORDS)], after=RECORDS))
    assert grade(RECORDS, transcript.value())['generated_records'] == 0


def test_writer_and_next_reader_can_share_a_call():
    transcript = Transcript().add('python3 read_csv.py --start 1 --count 50', [reader(RECORDS[:1])])
    transcript.add(heredoc(RECORDS[:1]) + '\npython3 read_csv.py --start 2 --count 50',
                   [writer(RECORDS[:1]), reader(RECORDS[1:], start=2)], after=RECORDS[:1])
    transcript.add(heredoc(RECORDS[1:]), [writer(RECORDS[1:])], after=RECORDS)
    assert grade(RECORDS, transcript.value())['generated_records'] == 0


def test_later_actual_literal_retranscription_can_correct_generated_output():
    transcript = Transcript().add('python3 convert.py', [operation('other', argv=['python3', 'convert.py'])], after=RECORDS)
    transcript.add('python3 read_csv.py --start 1 --count 50', [reader(RECORDS)])
    transcript.add(heredoc(RECORDS), [writer(RECORDS)], after=RECORDS)
    assert grade(RECORDS, transcript.value())['generated_records'] == 0


def test_partial_output_does_not_require_progress_after_every_batch():
    result = grade(EXPECTED[:200], transcribed(EXPECTED[:200]))
    assert (result['score'], result['generated_records']) == (pytest.approx(200 / TARGET), 0)


def test_output_digest_is_bound_to_final_host_snapshot():
    transcript = read_then_write().value()
    transcript['final_sha256'] = '0' * 64
    assert grade(RECORDS, transcript)['score'] == 0


# --- Hostile submissions -------------------------------------------------------

MALFORMED = {
    "empty": b"",
    "not json": b"not json",
    "object": b"{}",
    "deep nesting": b"[" * 200_000 + b"]" * 200_000,
    "nan amount": saved(EXPECTED[:1]).replace(b'"amount_usd":74489.2', b'"amount_usd":NaN'),
    "duplicate key": b'[{"id":"D18427","id":"D18427"}]',
    "non-utf8": b"\xff\xfe\x00\xd8garbage\x80",
    "string amount": saved(EXPECTED[:1]).replace(b'74489.2', b'"74489.20"'),
    "bool amount": saved(EXPECTED[:1]).replace(b'74489.2', b'true'),
    "huge int": b"[" + b"9" * 100_000 + b"]",
    "huge exponent": saved(EXPECTED[:1]).replace(b'74489.2', b'1e999999999'),
    "fractional cents": saved(EXPECTED[:1]).replace(b'74489.2', b'74489.201'),
}


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_malformed_output_scores_zero(tmp_path, name):
    (tmp_path / "output.json").write_bytes(MALFORMED[name])
    assert grade_file(tmp_path / "output.json", HONEST[:2])["score"] == 0.0


def test_missing_output_scores_zero(tmp_path):
    result = grade_file(tmp_path / "output.json")
    assert result["score"] == 0.0 and "cannot open" in result["reason"]


def test_oversize_output_scores_zero(tmp_path):
    (tmp_path / "output.json").write_bytes(saved(EXPECTED[:50]) + b" " * grader.MAX_SUBMISSION_BYTES)
    result = grade_file(tmp_path / "output.json", HONEST[:2])
    assert result["score"] == 0.0 and "exceeds" in result["reason"]


def test_honest_output_fits_the_byte_cap_with_room():
    assert len(saved(EXPECTED)) * 1.5 < grader.MAX_SUBMISSION_BYTES


def test_non_regular_outputs_score_zero(tmp_path):
    real = tmp_path / "real.json"
    real.write_bytes(saved(EXPECTED))
    (tmp_path / "link.json").symlink_to(real)
    (tmp_path / "dir.json").mkdir()
    os.mkfifo(tmp_path / "fifo.json")
    for name in ("link.json", "dir.json", "fifo.json"):
        assert grade_file(tmp_path / name, HONEST)["score"] == 0.0, name


# --- Hostile transcripts ---------------------------------------------------------

def test_pathological_commands_do_not_crash_or_stall():
    hostile = [
        "{" * 5_000_000,
        "{" + "a" * 5_000_000 + "}",
        "{" + "(" * 1000 + "}",
        "{[1]: 2}", "{**x}", "{1: 2, 3}", '{"a": 1e999999999}', "{'a': 1e999}",
        "{" + "9" * 5000 + ": 1}", '{"\\d": "\\q"}', "{'id': '\ud800'}", "{'a': b'\\xff'}",
        '{"id": "D18427", "account": 1, "owner": [], "stage": {}, "amount_usd": 1, "created_date": 1, "close_date": 1}',
        '{"id":"D18427","account":"x","owner":"x","stage":"x","amount_usd":"1e999999","created_date":"x","close_date":"x"}',
        "D18427 " * 1_000_000,
        "\x00" * 1_000_000,
    ]
    started = time.monotonic()
    result = grade(EXPECTED[:50], hostile + HONEST[:2] + hostile)
    assert time.monotonic() - started < 60
    assert (result["score"], result["generated_records"]) == (0.0, 50)



def test_pathological_v3_command_text_cannot_certify_or_stall():
    command = '{' * 5_000_000
    transcript = Transcript().add(command, after=RECORDS).value()
    started = time.monotonic()
    result = grade(RECORDS, transcript)
    assert time.monotonic() - started < 10
    assert (result['score'], result['generated_records']) == (0, 2)


def test_every_id_mentioned_everywhere_stays_bounded():
    ids = " ".join(record["id"] for record in EXPECTED)
    started = time.monotonic()
    result = grade(EXPECTED, [ids] * 20)
    assert time.monotonic() - started < 120
    assert (result["score"], result["generated_records"]) == (0.0, TARGET)


@pytest.mark.parametrize("content", ["", "[]", '{"shell_commands": "ls"}', '{"shell_commands": [1]}', '{"commands": []}'])
def test_misshapen_transcript_is_an_environment_defect(tmp_path, content):
    (tmp_path / "transcript.json").write_text(content)
    with pytest.raises(ValueError):
        grader.load_transcript(str(tmp_path / "transcript.json"))


def test_missing_transcript_is_an_environment_defect(tmp_path):
    with pytest.raises(FileNotFoundError):
        grader.load_transcript(str(tmp_path / "transcript.json"))
