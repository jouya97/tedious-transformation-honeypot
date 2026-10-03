"""Launch exactly three authorized replacement episodes once, without retries."""
import hashlib, json, os, subprocess
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
LAUNCH=OUT/'launch.json'
def now():return datetime.now(timezone.utc).isoformat()
def save(value):
    temporary=OUT/'launch.tmp'
    temporary.write_text(json.dumps(value,indent=2)+'\n')
    temporary.replace(LAUNCH)
def main():
    launch=json.loads(LAUNCH.read_text())
    assert launch['status']=='prepared','This launcher is one-shot; no retries or extra runs are authorized.'
    changed=[f['path'] for f in launch['files'] if hashlib.sha256((ROOT/f['path']).read_bytes()).hexdigest()!=f['sha256']]
    assert not changed,changed
    load_dotenv(ROOT/'.env',override=False)
    assert os.environ.get('ANTHROPIC_API_KEY'),'No Anthropic API key is available; no process was launched.'
    launch.update(status='starting',runner_pid=os.getpid(),launched_at=now());save(launch)
    with (OUT/'console.log').open('ab',buffering=0) as log:
        process=subprocess.Popen(launch['command'],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL)
        launch.update(status='running',pid=process.pid);save(launch)
        code=process.wait()
    launch.update(status='execution_finished',returncode=code,completed_at=now());save(launch)
    return code
if __name__=='__main__':raise SystemExit(main())
