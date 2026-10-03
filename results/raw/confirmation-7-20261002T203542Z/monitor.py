"""Read-only monitoring; no API calls or rollout mutation."""
import json,subprocess,time
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent
INSPECT=str(ROOT/'.venv/bin/inspect')
def cli(args):
 p=subprocess.run([INSPECT,'ctl',*args,'--json'],cwd=ROOT,text=True,capture_output=True,timeout=50)
 if p.returncode:raise RuntimeError(p.stderr[-1000:])
 return json.loads(p.stdout)
def snapshot():
 launch=json.loads((OUT/'launch.json').read_text());previous=json.loads((OUT/'status.json').read_text());rows={r['run_id']:r for r in previous.get('samples',[])}
 live=cli(['sample']);seen=[]
 for entry in launch['batches']:
  logdir=ROOT/'logs'/OUT.name/f"batch-{entry['batch']}"
  ids={p.stem.rsplit('_',1)[-1] for p in logdir.glob('*.eval')}
  samples=[s for s in live.get('samples',[]) if s.get('task_id') in ids];is_live=bool(samples)
  if not is_live and any(logdir.glob('*.eval')):
   samples=cli(['sample','--log-dir',str(logdir)]).get('samples',[])
  for s in samples:
   rid=s['epoch']+(0 if entry['batch']==1 else 3)
   row={k:s.get(k) for k in ('task_id','sample_id','epoch','status','started_at','completed_at','total_time','total_tokens','error','scores')};row.update(run_id=rid,batch=entry['batch'])
   if s['status'] not in ('pending','queued'):
    args=['sample','store',s['task_id'],str(s['sample_id']),str(s['epoch'])]
    if not is_live:args+=['--log-dir',str(logdir)]
    for key in ['shell_commands','steps','stop_reason','episode_error','elapsed_seconds','finish_summary']:args+=['--key',key]
    args+=['--full']
    try:
     store=cli(args).get('store',{});commands=store.get('shell_commands',[]);steps=store.get('steps',[]);saved=set()
     for step in steps:
      saved.difference_update(tuple(k) for k in step.get('removed',[]));saved.update(tuple(k) for k in step.get('added',[]))
     script_commands=[{'index':i,'prefix':c[:700]} for i,c in enumerate(commands) if ('csv.DictReader' in c or 'json.dump' in c or 'subprocess' in c or '.drive.py' in c)]
     row.update(shell_commands=len(commands),saved_records_observed=len(saved),literal_writer_commands=sum('<<' in c and 'write_json.py' in c for c in commands),last_command_prefix=commands[-1][:500] if commands else None,script_indicators=script_commands[:5],trace_complete_steps=sum(x.get('trace_complete') is True for x in steps),trace_steps=len(steps))
     row.update({k:store.get(k) for k in ('stop_reason','episode_error','elapsed_seconds','finish_summary')})
    except Exception as exc:row['store_error']=str(exc)
   rows[rid]=row
 result={'batch':OUT.name,'updated_at':datetime.now(timezone.utc).isoformat(),'execution_status':launch['status'],'runner_pid':launch.get('runner_pid'),'batches':[{k:e.get(k) for k in ('batch','status','pid','started_at','completed_at','returncode')} for e in launch['batches']],'samples':[rows[i] for i in sorted(rows)],'note':'Saved counts are observed output deltas, not correctness claims. Script indicators are heuristic pointers for human review. Final grader metrics supersede live data.'}
 temp=OUT/'status.tmp';temp.write_text(json.dumps(result,indent=2)+'\n');temp.replace(OUT/'status.json')
 return launch['status'] in ('execution_finished','blocked_source_changed') and all(r['status'] in ('completed','error','cancelled') for r in rows.values())
while True:
 try:
  if snapshot():break
 except Exception as exc:
  with (OUT/'monitor-errors.log').open('a') as f:f.write(f'{datetime.now(timezone.utc).isoformat()} {exc}\n')
 time.sleep(60)
