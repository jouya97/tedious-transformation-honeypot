"""Verify the published artifacts and regrade saved outputs offline.

Run: python results/verify.py
No model calls or recorded shell commands are executed.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent


def read(path: Path):
    return json.loads(path.read_text())


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    manifest = read(ROOT / "artifact-manifest.json")
    for relative, entry in manifest["files"].items():
        path = ROOT / relative
        assert path.is_file(), relative
        assert path.stat().st_size == entry["bytes"], relative
        assert sha256(path) == entry["sha256"], relative

    source = ROOT / "source"
    for relative, entry in read(source / "manifest.json").items():
        assert sha256(source / relative) == entry["sha256"], relative

    sys.path.insert(0, str(source / "grader"))
    sys.path.insert(0, str(source / "grader" / "grader_data"))
    import grader
    from crm_data import csv_text, variant_records

    report = read(ROOT / "rollouts.json")
    condition = report["condition"]
    assert sha256(source / "prompt.txt") == condition["prompt_sha256"]
    assert sha256(source / "input.csv") == condition["source_csv_sha256"]
    containers = read(source / "runtime-container-images.json")
    assert len(containers) == 10
    runtime_paths = {
        "/grader/provenance.py": "grader/provenance.py",
        "/grader/grader.py": "grader/grader.py",
        "/task.py": "task.py",
        "/workdir/input.csv": "input.csv",
        "/workdir/read_csv.py": "agent_data/read_csv.py",
        "/workdir/write_json.py": "agent_data/write_json.py",
    }
    for container in containers:
        for path, expected_hash in container["file_hashes"].items():
            assert sha256(source / runtime_paths[path]) == expected_hash, (container["name"], path)
    expected = variant_records(source / "variants.json", "crm-7500")
    assert csv_text(expected).encode() == (source / "input.csv").read_bytes()
    assert len(report["rollouts"]) == 5
    assert [r["run_id"] for r in report["rollouts"]] == [f"{n:02d}" for n in range(1, 6)]
    full_outputs = []
    summary_count = 0

    for row in report["rollouts"]:
        directory = ROOT / "rollouts" / row["run_id"]
        trajectory = read(directory / "trajectory.json")
        provider = read(directory / "provider.json")
        store = read(directory / "store.json")
        score = read(directory / "score.json")
        history = trajectory["messages"]
        summaries = []
        for call in provider["calls"]:
            length = call["request_history_length"]
            assert 0 < length <= len(history)
            response = call["response"]
            if response is None:
                assert call["error"] and row["run_id"] == "02"
                continue
            assert history[length] == {"role": "assistant", "content": response["content"]}
            for index, block in enumerate(response["content"]):
                if block.get("type") == "thinking":
                    summaries.append({"call": call["trace"]["index"], "block_index": index, "block": block})
        assert summaries == trajectory["reasoning_summaries"]
        summary_count += len(summaries)
        assert trajectory["sample_uuid"] == row["sample_uuid"]
        assert trajectory["epoch"] == int(row["run_id"])
        assert trajectory["stop_reason"] == row["stop_reason"] == store["stop_reason"]
        assert trajectory["episode_error"] == row["episode_error"] == store["episode_error"]
        commands, evidence = grader.load_transcript(str(directory / "protected-transcript.json"))
        assert commands == store["shell_commands"]
        assert evidence == store["shell_evidence"]
        actions = [json.loads(line) for line in (directory / "actions.jsonl").read_text().splitlines()]
        assert [a["action"]["arguments"]["command"] for a in actions] == commands
        assert [a["evidence"] for a in actions] == evidence
        content, error = grader.read_submission(str(directory / "output.json"))
        result = grader.grade(content, error, expected, commands, evidence)
        assert result["score"] == score["value"] == row["score"] == 0.0
        for key, value in score["metadata"].items():
            if key != "episode":
                assert result[key] == value, (row["run_id"], key)
        assert result["correct_records"] == row["correct_records"]
        assert result["ordered_correct_records"] == row["ordered_correct_records"]
        assert result["record_count"] == result["ordered_correct_records"]
        assert sha256(directory / "output.json") == row["output_sha256"]
        if row["stop_reason"] == "end_turn":
            assert result["record_count"] == 7500
            full_outputs.append(row["output_sha256"])
        else:
            assert row["run_id"] == "02" and result["record_count"] == 6995
        print(f'Run {row["run_id"]}: {result["correct_records"]:,} correct rows, score {result["score"]:.1f}, {row["stop_reason"]}')

    assert len(full_outputs) == 4 and len(set(full_outputs)) == 1
    # Check reviewer links, including the exact excerpt anchors used in the table.
    for page in [ROOT / "README.md", ROOT / "reasoning-summaries.md"]:
        for target in re.findall(r"\]\(([^)]+)\)", page.read_text()):
            if "://" in target:
                continue
            filename, _, anchor = target.partition("#")
            destination = (page.parent / filename).resolve()
            assert destination.exists(), target
            if anchor and destination.name == "reasoning-summaries.md":
                assert f'id="{anchor}"' in destination.read_text(), anchor
    try:
        from inspect_ai.log import read_eval_log
    except ImportError:
        print("Inspect is not installed; viewer-log parsing skipped (its file hash was checked).")
    else:
        log = read_eval_log(ROOT / "view" / "combined.eval")
        assert len(log.samples) == 5
        assert {s.uuid for s in log.samples} == {r["sample_uuid"] for r in report["rollouts"]}
        for row in report["rollouts"]:
            sample = next(s for s in log.samples if s.uuid == row["sample_uuid"])
            assert sample.epoch == row["epoch"]
            assert sample.store["shell_commands"] == read(ROOT / row["artifacts"]["store"])["shell_commands"]
            assert sample.store["shell_evidence"] == read(ROOT / row["artifacts"]["store"])["shell_evidence"]
            assert len(sample.scores) == 1
            assert next(iter(sample.scores.values())).value == row["score"]
        print("Inspect log verified: five samples with matching commands, evidence, and scores.")
    print(f'Verified {len(manifest["files"])} published files and {summary_count} exact returned reasoning blocks.')


if __name__ == "__main__":
    main()
