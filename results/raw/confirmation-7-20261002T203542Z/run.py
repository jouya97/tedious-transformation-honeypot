"""Exactly seven authorized paid episodes: parallel batches of three then four."""
import hashlib,json,os,subprocess,time
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
LAUNCH=OUT/'launch.json'
def now():return datetime.now(timezone.utc).isoformat()
def save(value):
 p=OUT/'launch.tmp';p.write_text(json.dumps(value,indent=2)+'\n');p.replace(LAUNCH)
launch=json.loads(LAUNCH.read_text());launch.update(status='running',runner_pid=os.getpid(),launched_at=now());save(launch)
for entry in launch['batches']:
 changed=[f['path'] for f in launch['files'] if hashlib.sha256((ROOT/f['path']).read_bytes()).hexdigest()!=f['sha256']]
 if changed:
  launch.update(status='blocked_source_changed',blocked_files=changed,completed_at=now());save(launch);break
 entry.update(status='starting',started_at=now());save(launch)
 with (OUT/f"batch-{entry['batch']}-console.log").open('ab',buffering=0) as log:
  process=subprocess.Popen(entry['command'],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL)
  entry.update(status='running',pid=process.pid);save(launch)
  code=process.wait()
 entry.update(status='process_finished',returncode=code,completed_at=now());save(launch)
else:
 launch.update(status='execution_finished',completed_at=now());save(launch)
