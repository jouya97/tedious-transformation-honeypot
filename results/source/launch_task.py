"""Archive-only wrapper around the unchanged current Inspect task and grader."""
from __future__ import annotations
import base64
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from inspect_ai import task
from inspect_ai.scorer import scorer, mean, stderr
from inspect_ai.util import sandbox

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'adapters' / 'inspect'))
import inspect_task as adapter
import harness_agent as harness


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + '\n')


def append(path, value):
    with path.open('a') as stream:
        stream.write(json.dumps(value, ensure_ascii=True) + '\n')


OriginalMirror = adapter.InspectMirror
class ArchiveMirror(OriginalMirror):
    def __init__(self, state):
        super().__init__(state)
        self.dest = BASE / ('preflight' if str(state.model).startswith('mockllm') else 'runs') / f'rollout-{state.epoch:02d}'
        self.dest.mkdir(parents=True, exist_ok=False)
        self.started_at = datetime.now(timezone.utc).isoformat()
        write(self.dest / 'started.json', {'started_at': self.started_at, 'epoch': state.epoch, 'uuid': state.uuid})
    def started(self, episode):
        super().started(episode)
        write(self.dest / 'initial-native-history.json', episode.messages)
    def model_call(self, episode, payload, response, trace, error):
        super().model_call(episode, payload, response, trace, error)
        # Full signed native blocks, exact request settings, and native history
        # boundaries are retained without duplicating every prior history.
        append(self.dest / 'provider-calls.jsonl', {
            'request': {k:v for k,v in payload.items() if k != 'messages'},
            'request_history_length': len(payload['messages']),
            'response': response, 'trace': trace, 'error': error})
        self.status(episode)
    def stepped(self, episode, action, observation, progress):
        super().stepped(episode, action, observation, progress)
        append(self.dest / 'actions.jsonl', {'action': action, 'observation': observation, 'progress': progress,
                                          'evidence': episode.shell_evidence[-1]})
        self.status(episode, progress)
    def status(self, episode, progress=None):
        write(self.dest / 'status.json', {'started_at': self.started_at, 'updated_at': datetime.now(timezone.utc).isoformat(),
            'provider_calls': len(episode.calls), 'tool_calls': episode.tool_calls, 'progress': progress,
            'stop_reason': episode.stop_reason, 'error': episode.error})
        if episode.stop_reason != 'running':
            write(self.dest / 'native-history.json', episode.messages)

adapter.InspectMirror = ArchiveMirror

@scorer(metrics=[mean(), stderr()])
def archived_current_grader():
    original = adapter.fresh_container_grader()
    async def score(state, target):
        result = await original(state, target)
        dest = BASE / ('preflight' if str(state.model).startswith('mockllm') else 'runs') / f'rollout-{state.epoch:02d}'
        entry = await adapter.extract(harness.OUTPUT_PATH, adapter.load_contract()[1])
        if 'b64' in entry:
            (dest / 'output.json').write_bytes(base64.b64decode(entry['b64']))
        else:
            write(dest / 'withheld-output.json', entry)
        write(dest / 'protected-transcript.json', {'shell_commands': state.store.get('shell_commands', []),
                                                   'shell_evidence': state.store.get('shell_evidence', [])})
        write(dest / 'score.json', result.model_dump(mode='json'))
        write(dest / 'episode.json', dict(state.store.items()))
        write(dest / 'finished.json', {'finished_at': datetime.now(timezone.utc).isoformat(),
                                      'epoch': state.epoch, 'uuid': state.uuid})
        return result
    return score

@task
def protected_rollouts(client: str | None = None):
    current = adapter.honeypot(client=client, max_retries=0)
    current.scorer = [archived_current_grader()]
    return current
