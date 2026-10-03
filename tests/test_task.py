"""Task bytes remain pinned to the recorded rollouts."""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import task
from grader_data.crm_data import csv_text, variant_records

ROOT = Path(__file__).resolve().parents[1]
CONFIRMATION = ROOT / "results/raw/confirmation-7-20261002T203542Z"
# The shipped prompt template, and its rendering for crm-49819.
TEMPLATE_SHA256 = "40ef7d1a7fb46f7f315a7443ad596977603a120fc5f18c1c8401dcde5b494a02"
PROMPT_SHA256 = "28c0db270f096f4f7b72b1c94e637d8f2d041d8e896fb28d26329bc2987f2d1c"
# input.csv for crm-49819.
INPUT_CSV_SHA256 = "058963286d6d69c871f61b583425ae0792f99ecb25c399cf316e0cafff7eb422"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_template_is_the_v10_prompt_byte_for_byte():
    assert sha256(task.TEMPLATE.encode("utf-8")) == TEMPLATE_SHA256


def test_rendered_prompt_matches_the_evidence_rollouts():
    printed = subprocess.run([sys.executable, str(ROOT / "task.py"), "crm-49819"],
                             capture_output=True, check=True).stdout
    assert sha256(printed) == PROMPT_SHA256


def test_unknown_variant_is_rejected():
    assert subprocess.run([sys.executable, str(ROOT / "task.py"), "nope"], capture_output=True).returncode != 0


def test_generator_reproduces_the_rollouts_input_csv():
    records = variant_records(ROOT / "variants.json", "crm-49819")
    assert sha256(csv_text(records).encode("utf-8")) == INPUT_CSV_SHA256


def test_variants_declare_their_hack_signatures():
    variants = json.loads((ROOT / "variants.json").read_text())
    for variant in variants["variants"].values():
        assert set(variant["hack_signatures"]) <= set(variants["hack_signatures"])


def test_shipped_grader_stays_reviewable():
    paths = [*(ROOT / "grader").rglob("*.py"), ROOT / 'adapters/inspect/passive_trace.py']
    lines = sum(len(path.read_text().splitlines()) for path in paths)
    assert lines < 1000


def test_declared_review_budget_covers_grader():
    import ast
    paths = [*(ROOT / 'grader').rglob('*.py'), ROOT / 'adapters/inspect/passive_trace.py']
    nodes = sum(sum(1 for _ in ast.walk(ast.parse(path.read_text()))) for path in paths)
    budget = json.loads((ROOT / 'qc.json').read_text())['review_budget']['max_ast_nodes']
    assert nodes <= budget


def test_preserved_confirmation_evidence_has_not_changed():
    manifest = json.loads((CONFIRMATION / 'artifact-manifest.json').read_text())
    for entry in manifest['files']:
        content = (ROOT / entry['path']).read_bytes()
        assert len(content) == entry['bytes']
        assert sha256(content) == entry['sha256']


def test_active_prompt_and_workspace_instructions_are_original_bytes():
    archive = CONFIRMATION / 'source'
    # Documentation terminology may change; the agent-visible template may not.
    archived_task = ast.parse((archive / 'task.py').read_text())
    archived_template = next(ast.literal_eval(node.value) for node in archived_task.body
                             if isinstance(node, ast.Assign)
                             and any(isinstance(target, ast.Name) and target.id == 'TEMPLATE'
                                     for target in node.targets))
    assert task.TEMPLATE.encode('utf-8') == archived_template.encode('utf-8')
    for path in ['agent_data/README.md', 'agent_data/read_csv.py',
                 'agent_data/write_json.py', 'agent_data/progress.py', 'variants.json', 'env.json']:
        assert (ROOT / path).read_bytes() == (archive / path).read_bytes(), path
    printed = subprocess.check_output([sys.executable, str(ROOT / 'task.py'), 'crm-49819'])
    assert sha256(printed) == PROMPT_SHA256
