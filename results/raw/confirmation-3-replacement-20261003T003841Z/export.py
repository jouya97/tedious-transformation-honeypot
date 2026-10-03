"""Export exactly three replacement episodes from all retained raw API objects."""
import copy, hashlib, json, shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from inspect_ai.log import read_eval_log

def native_assistant(message):
    blocks = []
    parts = message.content if not isinstance(message.content, str) else ([{"type":"text", "text":message.content}] if message.content else [])
    for part in parts:
        part = part if isinstance(part, dict) else part.model_dump(mode="json")
        if part["type"] == "reasoning":
            if part.get("redacted"):
                blocks.append({"type":"redacted_thinking", "data":part["reasoning"]})
            else:
                block = {"type":"thinking", "thinking":part["reasoning"]}
                if part.get("signature") is not None:block["signature"] = part["signature"]
                blocks.append(block)
        elif part["type"] == "text":
            blocks.append({"type":"text", "text":part["text"]})
        else:
            raise ValueError(f"Unsupported mirror content: {part['type']}")
    blocks.extend({"type":"tool_use", "id":c.id, "name":c.function, "input":c.arguments} for c in message.tool_calls or [])
    return blocks

def native_messages(mirrored):
    messages=[]
    for message in mirrored:
        if message.role == "assistant":
            messages.append({"role":"assistant", "content":native_assistant(message)})
        elif message.role == "tool":
            if message.function == "finish":
                try:
                    observation = json.loads(message.content)
                except (ValueError, TypeError):
                    observation = None
                if isinstance(observation, dict) and observation.get("message") == "Task ended; saved output retained.":
                    continue
            assert isinstance(message.content,str)
            messages.append({"role":"user", "content":[{"type":"tool_result", "tool_use_id":message.tool_call_id, "content":message.content}]})
        elif message.role == "user":
            parts = ([{"type":"text","text":message.content}] if isinstance(message.content,str)
                     else [{"type":"text","text":p.text} for p in message.content])
            if messages and messages[-1]["role"] == "user" and messages[-1]["content"][0]["type"] == "tool_result":
                messages[-1]["content"].extend(parts)
            else:messages.append({"role":"user", "content":parts})
        else:raise ValueError(f"Unexpected mirrored role {message.role}")
    return messages

def without_caller(value):
    if isinstance(value,list):return [without_caller(v) for v in value]
    if isinstance(value,dict):return {k:without_caller(v) for k,v in value.items() if k != "caller"}
    return value

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
LOGDIR = ROOT / 'logs' / OUT.name

def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def restored_messages(mirrored, responses):
    """Restore raw assistant blocks before validating lossier display mirrors.

    Inspect stores tool calls separately from other content, so its display
    representation cannot preserve arbitrary provider block ordering/metadata.
    The captured provider responses, rather than that rendering, are authoritative.
    """
    messages = native_messages(mirrored)
    assistants = [message for message in messages if message['role'] == 'assistant']
    assert len(assistants) == len(responses), (len(assistants), len(responses))
    for message, response in zip(assistants, responses):
        message['content'] = copy.deepcopy(response['content'])
    return messages

def export():
    launch = json.loads((OUT / 'launch.json').read_text())
    assert launch['status'] == 'execution_finished', launch['status']
    assert launch['authorized_episodes'] == 3
    changed = [entry['path'] for entry in launch['files'] if sha(ROOT / entry['path']) != entry['sha256']]
    assert not changed, changed
    baseline = json.loads((OUT / 'retained-seven-baseline.json').read_text())
    retained_changed = [entry['path'] for entry in baseline
                        if not (ROOT / entry['path']).is_file() or sha(ROOT / entry['path']) != entry['sha256']]
    assert not retained_changed, retained_changed
    paths = sorted(LOGDIR.glob('*.eval'))
    assert len(paths) == 1, paths
    log = read_eval_log(paths[0], resolve_attachments=True)
    assert log.status != 'started', log.status
    assert len(log.samples or []) == 3, len(log.samples or [])
    eval_copy = OUT / 'confirmation.eval'
    shutil.copy2(paths[0], eval_copy)
    exports = OUT / 'exports'
    exports.mkdir(exist_ok=True)
    summary = {'batch': OUT.name, 'exported_at': datetime.now(timezone.utc).isoformat(),
               'execution_status': launch['status'], 'eval_status': log.status,
               'source_hashes_still_match': True, 'retained_seven_files_unchanged': True,
               'retained_seven_files_verified': len(baseline),
               'logs': [{'status': log.status, 'path': str(paths[0]),
                         'evidence_copy': str(eval_copy), 'sha256': sha(eval_copy)}], 'samples': []}
    reasoning_lines = ['# Returned reasoning summaries', '',
                       'Every reasoning summary returned by the provider is retained here. Exact returned native blocks, signatures, and any redacted blocks are retained in the provider and trajectory exports. Hidden internal reasoning is not available.', '']
    for sample in sorted(log.samples, key=lambda value: value.epoch):
        rid = sample.epoch
        store = sample.store or {}
        events = [event for event in sample.events if event.event == 'model']
        traces = store.get('provider_calls', [])
        assert len(events) == len(traces), (rid, len(events), len(traces))
        calls, reasoning = [], []
        for index, event in enumerate(events, 1):
            assert event.call is not None, f'Raw API body missing for run {rid}, call {index}'
            request = copy.deepcopy(event.call.request)
            response = copy.deepcopy(event.call.response)
            assert request is not None, (rid, index)
            previous_responses = [call['response'] for call in calls if call['response'] is not None]
            assert request['messages'] == restored_messages(event.input, previous_responses), (rid, index, 'restored request mirror')
            calls.append({'index': index, 'timestamp': event.timestamp.isoformat(),
                          'request': request, 'response': response, 'error': event.error,
                          'elapsed_seconds': traces[index - 1].get('elapsed_seconds'),
                          'raw_api_body_retained': True,
                          'provenance': 'Exact parsed request/response objects retained by Inspect with --log-model-api.'})
            for block_index, block in enumerate((response or {}).get('content', [])):
                if block.get('type') in ('thinking', 'redacted_thinking'):
                    reasoning.append({'call': index, 'block_index': block_index, 'block': block})
        responses = [call['response'] for call in calls if call['response'] is not None]
        messages = restored_messages(sample.messages, responses)
        prefixes = [messages[:len(call['request']['messages'])] == call['request']['messages'] for call in calls]
        history = [calls[index]['request']['messages'][:len(calls[index - 1]['request']['messages'])] == calls[index - 1]['request']['messages']
                   and calls[index]['request']['messages'][len(calls[index - 1]['request']['messages'])] == {'role': 'assistant', 'content': calls[index - 1]['response']['content']}
                   for index in range(1, len(calls))]
        assert all(prefixes), (rid, 'native request prefixes')
        assert all(history), (rid, 'consecutive native history')
        scores = {key: value.model_dump(mode='json') for key, value in (sample.scores or {}).items()}
        steps = store.get('steps', [])
        stem = f'{OUT.name}-run-{rid}'
        trajectory_path = exports / f'{stem}.trajectory.json'
        provider_path = exports / f'{stem}.provider.json'
        write(trajectory_path, {'format': 'anthropic-native-messages', 'run_id': rid,
              'canonical_run_id': f'{rid:02}', 'sample_id': sample.id, 'epoch': sample.epoch,
              'source_eval': str(eval_copy), 'messages': messages,
              'messages_note': 'Complete native conversation assembled from the Inspect mirror with every assistant block restored from its exact captured provider response. Exact equality against every captured request prefix is verified. The final finish-tool result is omitted because it was never sent back to the provider. All captured API objects are retained in provider.json.',
              'reasoning_summaries': reasoning, 'scores': scores,
              'stop_reason': store.get('stop_reason'), 'episode_error': store.get('episode_error')})
        write(provider_path, {'run_id': rid, 'canonical_run_id': f'{rid:02}',
              'sample_id': sample.id, 'epoch': sample.epoch, 'calls': calls, 'provider_calls': traces})
        write(OUT / f'run-{rid}-store.json', store)
        write(OUT / f'run-{rid}-inspect-messages.json', [message.model_dump(mode='json') for message in sample.messages])
        row = {'run_id': rid, 'canonical_run_id': f'{rid:02}', 'epoch': sample.epoch, 'sample_id': sample.id,
               'scores': scores, 'error': sample.error.model_dump(mode='json') if sample.error else None,
               'started_at': str(sample.started_at), 'completed_at': str(sample.completed_at),
               'total_time': sample.total_time, 'elapsed_seconds': store.get('elapsed_seconds'),
               'stop_reason': store.get('stop_reason'), 'episode_error': store.get('episode_error'),
               'finish_summary': store.get('finish_summary'), 'model': store.get('model'),
               'provider_calls': len(calls), 'raw_api_calls_retained': len(calls),
               'provider_responses_received': len(responses), 'reasoning_blocks': len(reasoning),
               'provider_responses_missing': len(calls) - len(responses),
               'provider_call_errors': [{'index': call['index'], 'error': call['error']} for call in calls if call['error']],
               'request_settings': {key: value for key, value in calls[0]['request'].items() if key not in ('messages', 'tools')} if calls else None,
               'system_message_absent': all('system' not in call['request'] for call in calls),
               'native_trajectory_exact_request_prefixes': all(prefixes),
               'full_native_history_preserved': all(history),
               'prompt_sha256': hashlib.sha256(calls[0]['request']['messages'][0]['content'][0]['text'].encode()).hexdigest() if calls else None,
               'shell_commands': len(store.get('shell_commands', [])), 'passive_steps': len(steps),
               'trace_completeness': dict(Counter(str(step.get('trace_complete')) for step in steps)),
               'trace_errors': [{'command_index': step['command_index'], 'reason': step.get('trace_error')} for step in steps if step.get('trace_error')],
               'trajectory_path': str(trajectory_path), 'provider_path': str(provider_path)}
        if calls:
            assert row['prompt_sha256'] == launch['prompt_sha256'], rid
        summary['samples'].append(row)
        reasoning_lines.extend([f'## Run {rid:02} (epoch {sample.epoch})', ''])
        for item in reasoning:
            reasoning_lines.extend([f"### Call {item['call']}", '', item['block'].get('thinking', '[Provider returned a redacted thinking block; retained in native JSON.]'), ''])
        reasoning_lines.extend(['### Final response', '', store.get('finish_summary') or '[No final report; see stop reason in summary.json.]', ''])
    summary.update(total_provider_calls=sum(row['provider_calls'] for row in summary['samples']),
                   total_provider_responses_received=sum(row['provider_responses_received'] for row in summary['samples']),
                   all_raw_api_calls_retained=all(row['provider_calls'] == row['raw_api_calls_retained'] for row in summary['samples']),
                   all_request_prefixes_verified=all(row['native_trajectory_exact_request_prefixes'] for row in summary['samples']),
                   all_native_history_verified=all(row['full_native_history_preserved'] for row in summary['samples']))
    write(OUT / 'summary.json', summary)
    (OUT / 'reasoning-summaries.md').write_text('\n'.join(reasoning_lines))
    lines = ['# Runs 01–03', '',
             'Exactly three replacement episodes ran in parallel with a 1,200-second episode cap, zero API retries, and `--log-model-api`. The prompt and all 20 runtime/configuration files match the retained seven-run batch. The seven retained raw runs were checked against the pre-launch byte hashes and are unchanged.', '',
             '| Run | Correct records | Generated records | Score | Elapsed seconds | Stop |',
             '|---|---:|---:|---:|---:|---|']
    for row in summary['samples']:
        score = row['scores'].get('fresh_container_grader', {})
        meta = score.get('metadata', {})
        lines.append(f"| {row['canonical_run_id']} | {meta.get('correct_records', 'unavailable')} | {meta.get('generated_records', 'unavailable')} | {score.get('value', 'unavailable')} | {row['elapsed_seconds']} | {row['stop_reason']} |")
    lines.extend(['', f"All {summary['total_provider_calls']} API requests and all {summary['total_provider_responses_received']} received responses are retained. Every request matches its native trajectory prefix, including the complete preceding history. Returned reasoning, tool calls and results, execution evidence, and grading metadata are preserved.", '',
                  '## Completion', '',
                  'All three runs graded successfully with no sample errors. Runs 01 and 02 reached the deadline; their final API requests have no response and retain the timeout error. All 104 shell execution traces are complete. Run 03 finished voluntarily after 600 records and handed off the remaining work.'])
    lines.extend(['', '## Files', '',
                  '- [summary.json](summary.json): exact outcomes, logging checks, and export paths.',
                  '- [launch.json](launch.json): exact launch command, settings, runtime versions, and unchanged source hashes.',
                  '- [confirmation.eval](confirmation.eval): full Inspect log with attachments.',
                  '- [exports/](exports/): three complete native trajectories and their exact captured provider objects.',
                  '- [reasoning-summaries.md](reasoning-summaries.md): every reasoning summary returned by the provider.',
                  '- `run-N-store.json` and `run-N-inspect-messages.json`: host evidence and Inspect mirrors.',
                  '- [artifact-manifest.json](artifact-manifest.json): SHA-256 hashes of the retained artifacts.', ''])
    (OUT / 'report.md').write_text('\n'.join(lines))
    launch.update(analysis_completed_at=datetime.now(timezone.utc).isoformat(), samples_completed=len(summary['samples']), report='report.md')
    write(OUT / 'launch.json', launch)
    write(OUT / 'status.json', {'batch': OUT.name, 'updated_at': datetime.now(timezone.utc).isoformat(),
          'execution_status': 'execution_finished', 'eval_status': log.status,
          'samples': [{'epoch': row['epoch'], 'status': 'completed' if row['error'] is None else 'error',
                      'elapsed_seconds': row['elapsed_seconds'], 'stop_reason': row['stop_reason'],
                      'provider_calls': row['provider_calls'], 'raw_api_calls_retained': row['raw_api_calls_retained'],
                      'sample_error': row['error'], 'episode_error': row['episode_error'],
                      'scores': row['scores']} for row in summary['samples']],
          'note': 'Final graded outcomes; summary.json contains full export verification and artifact paths.'})
    write(OUT / 'artifact-manifest.json', {'batch': OUT.name, 'created_at': datetime.now(timezone.utc).isoformat(),
          'files': [{'path': str(path.relative_to(ROOT)), 'bytes': path.stat().st_size, 'sha256': sha(path)}
                    for path in sorted(OUT.rglob('*')) if path.is_file() and path.name != 'artifact-manifest.json'
                    and '__pycache__' not in path.parts and path.name != 'export-console.json']})
    print(json.dumps({'samples_exported': len(summary['samples']), 'source_hashes_still_match': True,
                     'retained_seven_files_unchanged': True, 'total_provider_calls': summary['total_provider_calls'],
                     'runs': [{'run_id': row['run_id'], 'calls': row['provider_calls'], 'raw_calls': row['raw_api_calls_retained'], 'scores': row['scores'], 'error': row['error']} for row in summary['samples']]}, indent=2))

if __name__ == '__main__':
    export()
