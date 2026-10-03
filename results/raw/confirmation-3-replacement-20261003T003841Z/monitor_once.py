"""One read-only local progress snapshot; never makes provider calls."""
import concurrent.futures, json, subprocess
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent

def cli(args):
    result=subprocess.run([str(ROOT/'.venv/bin/inspect'),'ctl',*args,'--json'],cwd=ROOT,capture_output=True,text=True,check=True,timeout=50)
    return json.loads(result.stdout)

def describe(sample):
    args=['sample','store',sample['task_id'],str(sample['sample_id']),str(sample['epoch'])]
    for key in ['shell_commands','steps','provider_calls','stop_reason','episode_error','elapsed_seconds']:
        args.extend(['--key',key])
    args.append('--full')
    store=cli(args).get('store',{})
    commands=store.get('shell_commands',[])
    steps=store.get('steps',[])
    saved=set()
    for step in steps:
        saved.difference_update(tuple(key) for key in step.get('removed',[]))
        saved.update(tuple(key) for key in step.get('added',[]))
    return {'epoch':sample['epoch'],'status':sample['status'],'elapsed_seconds':round(sample.get('total_time') or 0,1),'provider_calls':len(store.get('provider_calls',[])),'shell_commands':len(commands),'literal_writer_commands':sum('<<' in cmd and 'write_json.py' in cmd for cmd in commands),'script_indicator_commands':sum(any(token in cmd for token in ('csv.DictReader','json.dump','subprocess','.drive.py')) for cmd in commands),'saved_records_observed':len(saved),'sample_error':sample.get('error'),'episode_error':store.get('episode_error'),'trace_steps':len(steps),'trace_incomplete':sum(step.get('trace_complete') is not True for step in steps)}

def main():
    launch=json.loads((OUT/'launch.json').read_text())
    if launch['status']=='execution_finished':
        print(json.dumps({'execution_status':'execution_finished','returncode':launch.get('returncode')}))
        return
    ids={path.stem.rsplit('_',1)[-1] for path in (ROOT/'logs'/OUT.name).glob('*.eval')}
    live=cli(['sample'])
    samples=[sample for sample in live['samples'] if sample['task_id'] in ids]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        rows=sorted(pool.map(describe,samples),key=lambda row:row['epoch'])
    result={'batch':OUT.name,'updated_at':datetime.now(timezone.utc).isoformat(),'execution_status':launch['status'],'samples':rows,'note':'Live saved counts are observed output deltas, not final correctness claims. Script indicators include read-only checks and are not behavior classifications.'}
    (OUT/'status.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))

if __name__=='__main__':main()
