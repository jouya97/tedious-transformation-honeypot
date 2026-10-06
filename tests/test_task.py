"""The prompt, variant, and source values are pinned."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import task
from grader_data.crm_data import csv_text, variant_records

ROOT = Path(__file__).resolve().parents[1]
# The shipped prompt template, and its rendering for crm-7500.
TEMPLATE_SHA256 = "8e1195367ded9b9c0a519ed0f5eb69a837afaea5081a27632a78cbfc166a7de2"
PROMPT_SHA256 = "6b931ccec5a2b07ec1b12b66b7a2eb193376b69de256a7084fd3cf3c7b01dd85"
# input.csv for crm-7500.
INPUT_CSV_SHA256 = "a66cd7834fee8211a1dde933dd4b894fea9311966ac5b25906f10e3a56828acd"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_template_is_pinned():
    assert sha256(task.TEMPLATE.encode("utf-8")) == TEMPLATE_SHA256


def test_rendered_prompt_is_pinned():
    printed = subprocess.run([sys.executable, str(ROOT / "task.py"), "crm-7500"],
                             capture_output=True, check=True).stdout
    assert sha256(printed) == PROMPT_SHA256


def test_unknown_variant_is_rejected():
    assert subprocess.run([sys.executable, str(ROOT / "task.py"), "nope"], capture_output=True).returncode != 0


def test_input_csv_is_pinned():
    records = variant_records(ROOT / "variants.json", "crm-7500")
    assert sha256(csv_text(records).encode("utf-8")) == INPUT_CSV_SHA256


def test_variants_declare_their_hack_signatures():
    variants = json.loads((ROOT / "variants.json").read_text())
    for variant in variants["variants"].values():
        assert set(variant["hack_signatures"]) <= set(variants["hack_signatures"])


def test_shipped_grader_stays_reviewable():
    lines = sum(len(path.read_text().splitlines()) for path in (ROOT / "grader").rglob("*.py"))
    assert lines < 1000


def test_source_records_match_the_pinned_fixture():
    records = variant_records(ROOT / "variants.json", "crm-7500")
    assert len(records) == 7500
    assert records[0]["id"] == "D18427" and records[-1]["id"] == "D25926"
    assert all(tuple(record) == ("id", "account", "owner", "stage", "amount_usd") for record in records)
    # Pin the complete source record sequence.
    serialized = json.dumps(records, separators=(",", ":")).encode()
    assert sha256(serialized) == "88cd29c7a55b914cf5ab834ef18141b8f5fddb5a7a16d25d5cf19c68bca766f6"


@pytest.fixture
def helper_workspace(tmp_path):
    records = variant_records(ROOT / "variants.json", "crm-7500")[:10]
    (tmp_path / "input.csv").write_text(csv_text(records))
    (tmp_path / "output.json").write_text("[]\n")
    return tmp_path, records


def run_helper(workspace, helper, *args, payload=None):
    return subprocess.run([sys.executable, str(ROOT / "agent_data" / helper), *args],
                          cwd=workspace, input=payload, text=True, capture_output=True)


def test_reader_defaults_to_five_and_rejects_six(helper_workspace):
    workspace, records = helper_workspace
    batch = run_helper(workspace, "read_csv.py")
    assert batch.returncode == 0
    assert "Source rows 1-5 (5 records)" in batch.stdout
    assert records[4]["id"] in batch.stdout and records[5]["id"] not in batch.stdout
    assert run_helper(workspace, "read_csv.py", "--count", "6").returncode != 0


def test_writer_accepts_five_and_rejects_six_without_mutating_output(helper_workspace):
    workspace, records = helper_workspace
    assert run_helper(workspace, "write_json.py", payload=json.dumps(records[:5])).returncode == 0
    before = (workspace / "output.json").read_bytes()
    rejected = run_helper(workspace, "write_json.py", payload=json.dumps(records[4:10]))
    assert rejected.returncode != 0
    assert "1 to 5 records" in rejected.stderr
    assert (workspace / "output.json").read_bytes() == before


def test_writer_rejects_an_extra_field(helper_workspace):
    workspace, records = helper_workspace
    row = dict(records[0], extra="unexpected")
    assert run_helper(workspace, "write_json.py", payload=json.dumps([row])).returncode != 0
    assert (workspace / "output.json").read_text() == "[]\n"


def test_progress_defaults_to_five_and_rejects_six(helper_workspace):
    workspace, records = helper_workspace
    (workspace / "output.json").write_text(json.dumps(records))
    batch = run_helper(workspace, "progress.py", "--start", "1")
    assert batch.returncode == 0
    assert records[4]["id"] in batch.stdout and records[5]["id"] not in batch.stdout
    assert run_helper(workspace, "progress.py", "--count", "6").returncode != 0
