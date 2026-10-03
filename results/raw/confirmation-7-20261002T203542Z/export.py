"""Export all seven new episodes from exact captured API objects, without API calls."""
import copy,hashlib,importlib.util,json,shutil
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from inspect_ai.log import read_eval_log
ROOT=Path(__file__).resolve().parents[3];OUT=Path(__file__).resolve().parent;LOGDIR=ROOT/'logs'/OUT.name
# The retained replacement exporter supplies reusable mirror normalization only.
spec=importlib.util.spec_from_file_location('retained_confirmation_export',ROOT/'results/raw/confirmation-3-replacement-20261003T003841Z/export.py')
prior=importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)
def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def export():
 launch=json.loads((OUT/'launch.json').read_text())
 summary={'batch':OUT.name,'exported_at':datetime.now(timezone.utc).isoformat(),'execution_status':launch['status'],'source_hashes_still_match':all(sha(ROOT/f['path'])==f['sha256'] for f in launch['files']),'logs':[],'samples':[]}
 reasoning_lines=['# Returned reasoning summaries', '', 'All summaries returned by the provider are retained here; hidden internal reasoning is not available. Exact native blocks, including signatures and any redacted blocks, are in the root trajectory/provider files.', '']
 for batch in (1,2):
  paths=sorted((LOGDIR/f'batch-{batch}').glob('*.eval'))
  if not paths:continue
  assert len(paths)==1,paths
  log=read_eval_log(paths[0],resolve_attachments=True)
  if log.status=='started':continue
  copy_path=OUT/f'batch-{batch}.eval';shutil.copy2(paths[0],copy_path)
  summary['logs'].append({'batch':batch,'status':log.status,'path':str(paths[0]),'evidence_copy':str(copy_path),'sha256':sha(paths[0])})
  for sample in sorted(log.samples or [],key=lambda s:s.epoch):
   rid=sample.epoch+(0 if batch==1 else 3);store=sample.store or {};model_events=[e for e in sample.events if e.event=='model'];provider_traces=store.get('provider_calls',[]);calls=[];reasoning=[];checks=[]
   for index,event in enumerate(model_events,1):
    assert event.call is not None,f'Raw API body missing for run {rid} call {index}'
    request=copy.deepcopy(event.call.request);response=copy.deepcopy(event.call.response);trace=provider_traces[index-1] if index<=len(provider_traces) else {}
    checks.append(prior.without_caller(request['messages'])==prior.without_caller(prior.native_messages(event.input)))
    if response is not None:
     assert prior.without_caller(prior.native_assistant(event.output.choices[0].message))==prior.without_caller(response['content']),f'Raw/mirror response differs run {rid} call {index}'
    calls.append({'index':index,'timestamp':event.timestamp.isoformat(),'request':request,'response':response,'error':event.error,'elapsed_seconds':trace.get('elapsed_seconds'),'raw_api_body_retained':True,'provenance':'Exact parsed request/response objects retained by Inspect with --log-model-api; not a byte-for-byte HTTP wire capture.'})
    for block_index,block in enumerate((response or {}).get('content',[])):
     if block.get('type') in ('thinking','redacted_thinking'):reasoning.append({'call':index,'block_index':block_index,'block':block})
   assert all(checks),f'Mirror request differs for run {rid}'
   messages=prior.native_messages(sample.messages)
   raw_responses=[c['response'] for c in calls if c['response'] is not None]
   assistant_messages=[m for m in messages if m['role']=='assistant']
   assert len(assistant_messages)==len(raw_responses),(rid,len(assistant_messages),len(raw_responses))
   for message,response in zip(assistant_messages,raw_responses):
    assert prior.without_caller(message['content'])==prior.without_caller(response['content'])
    message['content']=copy.deepcopy(response['content'])
   # Every request is an exact prefix of the exported native transcript.
   prefix_checks=[messages[:len(c['request']['messages'])]==c['request']['messages'] for c in calls]
   assert all(prefix_checks),f'Native trajectory differs from raw requests in run {rid}'
   scores={k:v.model_dump(mode='json') for k,v in (sample.scores or {}).items()};steps=store.get('steps',[])
   stem=f'{OUT.name}-run-{rid}';trajectory_path=ROOT/f'{stem}.trajectory.json';provider_path=ROOT/f'{stem}.provider.json'
   write(trajectory_path,{'format':'anthropic-native-messages','run_id':rid,'batch':batch,'sample_id':sample.id,'epoch':sample.epoch,'source_eval':str(paths[0]),'messages':messages,'messages_note':'Full native conversation assembled from the Inspect mirror with every assistant block restored from its exact captured provider response. Exact equality against every captured request prefix is verified. The final finish-tool result is omitted because it was never sent back to the provider. All raw API objects are present in provider.json.','reasoning_summaries':reasoning,'scores':scores,'stop_reason':store.get('stop_reason'),'episode_error':store.get('episode_error')})
   write(provider_path,{'run_id':rid,'batch':batch,'sample_id':sample.id,'epoch':sample.epoch,'calls':calls,'provider_calls':provider_traces})
   write(OUT/f'run-{rid}-store.json',store)
   write(OUT/f'run-{rid}-inspect-messages.json',[m.model_dump(mode='json') for m in sample.messages])
   row={'run_id':rid,'batch':batch,'epoch':sample.epoch,'sample_id':sample.id,'scores':scores,'error':sample.error.model_dump(mode='json') if sample.error else None,'started_at':str(sample.started_at),'completed_at':str(sample.completed_at),'total_time':sample.total_time,'elapsed_seconds':store.get('elapsed_seconds'),'stop_reason':store.get('stop_reason'),'episode_error':store.get('episode_error'),'finish_summary':store.get('finish_summary'),'model':store.get('model'),'provider_calls':len(calls),'raw_api_calls_retained':len(calls),'reasoning_blocks':len(reasoning),'request_settings':{k:v for k,v in calls[0]['request'].items() if k not in ('messages','tools')} if calls else None,'system_message_absent':all('system' not in c['request'] for c in calls),'native_trajectory_exact_request_prefixes':all(prefix_checks),'full_native_history_preserved':all(calls[i]['request']['messages'][:len(calls[i-1]['request']['messages'])]==calls[i-1]['request']['messages'] and calls[i]['request']['messages'][len(calls[i-1]['request']['messages'])]=={'role':'assistant','content':calls[i-1]['response']['content']} for i in range(1,len(calls))),'prompt_sha256':hashlib.sha256(calls[0]['request']['messages'][0]['content'][0]['text'].encode()).hexdigest() if calls else None,'shell_commands':len(store.get('shell_commands',[])),'passive_steps':len(steps),'trace_completeness':dict(Counter(str(s.get('trace_complete')) for s in steps)),'trace_errors':[{'command_index':s['command_index'],'reason':s.get('trace_error')} for s in steps if s.get('trace_error')],'trajectory_path':str(trajectory_path),'provider_path':str(provider_path)}
   summary['samples'].append(row)
   reasoning_lines.extend([f'## Run {rid} (batch {batch}, epoch {sample.epoch})',''])
   for item in reasoning:
    reasoning_lines.extend([f"### Call {item['call']}", '', item['block'].get('thinking','[Provider returned a redacted thinking block; retained in native JSON.]'),''])
   reasoning_lines.extend(['### Final response','',store.get('finish_summary') or '[No finish response; see stop reason in summary.json.]',''])
 write(OUT/'summary.json',summary);(OUT/'reasoning-summaries.md').write_text('\n'.join(reasoning_lines))
 print(json.dumps({'samples_exported':len(summary['samples']),'source_hashes_still_match':summary['source_hashes_still_match'],'runs':[{'run_id':r['run_id'],'calls':r['provider_calls'],'raw_calls':r['raw_api_calls_retained'],'scores':r['scores'],'error':r['error']} for r in summary['samples']]},indent=2))
if __name__=='__main__':export()
