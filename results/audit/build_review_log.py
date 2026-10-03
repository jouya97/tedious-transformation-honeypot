"""Build and verify a derived ten-rollout Inspect review log; no evaluation is run.

    .venv/bin/python results/audit/build_review_log.py
    .venv/bin/inspect view start --log-dir results/view --host 127.0.0.1

The original .eval logs are read-only inputs. Review epochs identify rollouts 01–10;
source identifiers, headers, scheduling and times remain available in metadata.
"""
from __future__ import annotations

import copy
import hashlib
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

from inspect_ai.log import (
    EvalLog, EvalMetric, EvalResults, EvalScore, EvalStats, HeadlineMetric,
    list_eval_logs, read_eval_log, write_eval_log,
)
from inspect_ai.model import ModelUsage

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'results/view/combined.eval'
VALIDATION = ROOT / 'results/audit/combined-log-validation.json'
REVIEW_KEY = 'review_compilation'


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def usage_sum(samples, field):
    grouped = {}
    for sample in samples:
        for name, usage in getattr(sample, field).items():
            grouped.setdefault(name, []).append(usage.model_dump())
    return {name: ModelUsage.model_validate({
        key: sum(value[key] for value in values if value[key] is not None)
        if any(value[key] is not None for value in values) else None
        for key in values[0]
    }) for name, values in grouped.items()}


def main():
    index = json.loads((ROOT / 'results/rollouts.json').read_text())
    rows = sorted(index['rollouts'], key=lambda row: int(row['rollout_id']))
    assert [int(row['rollout_id']) for row in rows] == list(range(1, 11))
    source_paths = {row['artifacts'].get('original_eval', row['artifacts']['eval']) for row in rows}
    assert len(source_paths) == 3 and str(OUTPUT.relative_to(ROOT)) not in source_paths
    sources = {path: read_eval_log(ROOT / path, resolve_attachments=False) for path in sorted(source_paths)}
    hashes = {path: sha(ROOT / path) for path in sources}
    assert all(log.status == 'success' for log in sources.values())
    base = next(iter(sources.values()))
    identity_fields = {'eval_id', 'run_id', 'task_id', 'created', 'config'}
    for log in sources.values():
        for field in type(base.eval).model_fields:
            if field not in identity_fields:
                assert getattr(log.eval, field) == getattr(base.eval, field), ('different common setting', field)
        for field in type(base.eval.config).model_fields:
            if field not in {'epochs', 'max_samples'}:
                assert getattr(log.eval.config, field) == getattr(base.eval.config, field), ('different config', field)
        assert log.plan == base.plan
    samples, mappings, originals = [], [], []
    for row in rows:
        path = row['artifacts'].get('original_eval', row['artifacts']['eval'])
        original = row['original_identifiers']
        matches = [sample for sample in sources[path].samples
                   if sample.id == original['sample_id'] and sample.epoch == original['epoch']]
        assert len(matches) == 1, (path, original)
        source_sample = matches[0]
        assert REVIEW_KEY not in source_sample.metadata
        mapping = {'rollout_id': row['rollout_id'], 'review_epoch': int(row['rollout_id']),
                   'source_eval': path, 'source_sha256': hashes[path],
                   'original_identifiers': copy.deepcopy(original),
                   'original_sample_uuid': source_sample.uuid}
        sample = source_sample.model_copy(deep=True)
        sample.epoch = int(row['rollout_id'])
        sample.metadata[REVIEW_KEY] = mapping
        samples.append(sample)
        mappings.append(mapping)
        originals.append(source_sample)
    assert {sample.id for sample in samples} == {'crm-49819'}
    compilation_id = 'crm-review-' + hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:16]
    provenance = {
        'kind': 'derived_review_compilation',
        'description': 'Ten previously executed episodes combined for review. Epochs 1–10 are review labels, not a new ten-way launch. No model calls were made to build this log.',
        'source_logs': {path: {'sha256': hashes[path], 'header': log.model_dump(mode='json', exclude={'samples', 'reductions', 'location', 'etag'})}
                        for path, log in sources.items()},
        'sample_mapping': mappings,
        'common_condition': index['condition'],
        'aggregate_definition': 'Counts cover the ten recorded episodes; mean is the arithmetic mean of their original scores. No epoch reduction or inferred uncertainty statistic is applied.',
    }
    spec = base.eval.model_copy(deep=True)
    spec.eval_id = spec.run_id = spec.task_id = compilation_id
    spec.created = datetime.now(timezone.utc).isoformat()
    spec.task_display_name = 'CRM transcription — 10 rollouts'
    spec.config.epochs = 10
    spec.config.max_samples = None
    spec.dataset.sample_ids = ['crm-49819']
    spec.metadata = {REVIEW_KEY: {'kind': provenance['kind'], 'description': provenance['description']}}
    score_names = sorted({name for sample in samples for name in sample.scores or {}})
    aggregate_scores = []
    for name in score_names:
        values = [sample.scores[name].value for sample in samples if sample.scores and name in sample.scores]
        assert all(type(value) in (int, float) for value in values)
        aggregate_scores.append(EvalScore(name=name, scorer=name, scored_samples=len(values),
            unscored_samples=len(samples) - len(values),
            metrics={'mean': EvalMetric(name='mean', value=statistics.mean(values))},
            metadata={'aggregation': 'arithmetic mean of the original episode scores; no epoch reduction'}))
    results = EvalResults(total_samples=len(samples), completed_samples=sum(sample.error is None for sample in samples),
        logged_samples=len(samples), scores=aggregate_scores,
        headline=HeadlineMetric(scorer=score_names[0], score=score_names[0], metric='mean') if score_names else None,
        metadata={'derived_review': True})
    combined = EvalLog(status='success', eval=spec, plan=base.plan.model_copy(deep=True), results=results,
        stats=EvalStats(started_at='', completed_at='', model_usage=usage_sum(samples, 'model_usage'),
                        role_usage=usage_sum(samples, 'role_usage'), connection_limit_history=[]),
        tags=['derived-review'], metadata={REVIEW_KEY: provenance}, samples=samples)
    # The writer may pool long text/API objects into attachments. Validate the
    # fully resolved result, including every sample field, after it roundtrips.
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    write_eval_log(combined, OUTPUT)
    compact = read_eval_log(OUTPUT, resolve_attachments=False)
    restored = read_eval_log(OUTPUT, resolve_attachments='full')
    full_sources = {path: read_eval_log(ROOT / path, resolve_attachments='full') for path in sources}
    assert restored.eval.config.epochs == 10 and len(restored.samples) == 10
    assert restored.eval.config.max_samples is None
    assert restored.results.total_samples == restored.results.completed_samples == 10
    checks = []
    for source_sample, expected, actual, pooled, mapping in zip(originals, samples, restored.samples, compact.samples, mappings):
        assert actual.id == expected.id and actual.epoch == expected.epoch
        assert all(pooled.attachments.get(key) == value for key, value in source_sample.attachments.items())
        source_full = next(sample for sample in full_sources[mapping['source_eval']].samples
                           if sample.id == source_sample.id and sample.epoch == source_sample.epoch)
        want, got = source_full.model_dump(mode='json'), actual.model_dump(mode='json')
        want['epoch'] = expected.epoch
        want['metadata'][REVIEW_KEY] = mapping
        differences = [field for field in want if want[field] != got[field]]
        assert not differences, (expected.epoch, differences)
        events = [event for event in actual.events if event.event == 'model']
        source_events = [event for event in source_full.events if event.event == 'model']
        assert len(events) == len(source_events)
        assert all(event.call is not None and event.call == original_event.call for event, original_event in zip(events, source_events))
        checks.append({'rollout_id': f'{actual.epoch:02}', 'all_sample_fields_preserved': True,
                       'original_epoch': source_sample.epoch, 'model_calls': len(events),
                       'received_responses': sum(event.call.response is not None for event in events),
                       'original_attachments_preserved': len(source_sample.attachments)})
    assert all(sha(ROOT / path) == digest for path, digest in hashes.items())
    visible = list_eval_logs(str(OUTPUT.parent), formats=['eval'], recursive=True)
    assert len(visible) == 1 and Path(visible[0].name).name == 'combined.eval'
    validation = {'artifact': str(OUTPUT.relative_to(ROOT)), 'sha256': sha(OUTPUT), 'bytes': OUTPUT.stat().st_size,
        'derived_review_compilation': True, 'samples': len(restored.samples), 'review_epochs': list(range(1, 11)),
        'only_sample_changes': ['epoch', 'metadata.review_compilation'],
        'source_logs_unchanged': hashes, 'viewer_entries': len(visible),
        'aggregate_scores': {score.name: score.metrics['mean'].value for score in aggregate_scores},
        'checks': checks, 'model_calls': sum(check['model_calls'] for check in checks),
        'received_responses': sum(check['received_responses'] for check in checks),
        'original_attachments_preserved': sum(check['original_attachments_preserved'] for check in checks),
        'viewer_command': '.venv/bin/inspect view start --log-dir results/view --host 127.0.0.1'}
    VALIDATION.write_text(json.dumps(validation, indent=2) + '\n')
    print(json.dumps(validation, indent=2))


if __name__ == '__main__':
    main()
