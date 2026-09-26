"""Offline, read-only extraction of tau-voice evaluation evidence."""
import json, pathlib, collections, statistics
ROOT=pathlib.Path('/Users/admin/Desktop/换机/git_root/agent_eval/tau2-bench/data/simulations/br_voice_retail4')
OUT=pathlib.Path(__file__).resolve().parent
results=json.loads((ROOT/'results.json').read_text()); tasks={t['id']:t for t in results['tasks']}
rows=[]
for f in sorted((ROOT/'simulations').glob('*.json')):
 s=json.loads(f.read_text()); tid=s['task_id']; art=ROOT/'artifacts'/f'task_{tid}'/f"sim_{s['id']}"; events=[]
 for role,filename in [('USER','user_labels.txt'),('ASSISTANT','assistant_labels.txt')]:
  for lineno,line in enumerate((art/'audio'/filename).read_text().splitlines(),1):
   a,b,txt=line.split('\t',2)
   events.append((float(a), f'[{float(a):.1f}–{float(b):.1f}s] {role}: {txt or "[audio without transcript]"}'))
 calls=[];errors=[];interrupts=0; audio_ticks=blank_ticks=0
 for tick in s['ticks']:
  t=tick['tick_id']*.2; ac=tick.get('agent_chunk') or {};raw=ac.get('raw_data') or {}
  interrupts+='speech_started' in (raw.get('vad_events') or [])
  if ac.get('contains_speech'):
   audio_ticks+=1;blank_ticks+=not bool(ac.get('content'))
  for c in tick.get('agent_tool_calls') or []:
   calls.append(c);events.append((t,f'[{t:.1f}s] TOOL CALL: '+json.dumps(c,ensure_ascii=False)))
  for r in tick.get('agent_tool_results') or []:
   content=r.get('content',''); events.append((t+.001,f'[{t:.1f}s] TOOL RESULT: '+json.dumps(r,ensure_ascii=False)))
   if r.get('error') or 'Error:' in str(content):errors.append(r)
 text=[f"# Task {tid} / reward {s['reward_info']['reward']}",f"Simulation: {f}",f"Session: {s.get('provider_session_id')}",f"Termination: {s['termination_reason']}",'## Task specification',json.dumps(tasks[tid],ensure_ascii=False,indent=2),'## Reward',json.dumps(s['reward_info'],ensure_ascii=False,indent=2),'## Timeline (labels are evaluator-visible text, NOT service ASR)',*[v for _,v in sorted(events,key=lambda e:e[0])]]
 groups={}
 for tick in s['ticks']:
  for key in ['agent_chunk','user_chunk']:
   chunk=tick.get(key) or {}; content=chunk.get('content') or ''
   if not content:continue
   ids=tuple(chunk.get('utterance_ids') or [f"tick_{tick['tick_id']}"]);gid=(key,ids)
   g=groups.setdefault(gid,{'start':tick['tick_id']*.2,'end':tick['tick_id']*.2,'role':key,'parts':[]})
   g['parts'].append(content);g['end']=tick['tick_id']*.2
 text.extend(['## Full tick text grouped by utterance (no label length limit)', 'Timing below is text arrival time, not the audio onset. User text is simulator speech, NOT service ASR.'])
 for g in sorted(groups.values(),key=lambda g:g['start']):
  text.append(f"[{g['start']:.1f}–{g['end']:.1f}s] {g['role']}: "+''.join(g['parts']))
 (OUT/f'task_{int(tid):03}.md').write_text('\n\n'.join(text))
 rows.append(dict(task=int(tid),reward=s['reward_info']['reward'],termination=s['termination_reason'],seconds=len(s['ticks'])*.2,wall_seconds=s['duration'],session=s.get('provider_session_id'),simulation=s['id'],interrupts=interrupts,audio_ticks=audio_ticks,blank_audio_ticks=blank_ticks,tool_calls=len(calls),tool_errors=len(errors),voice=s['speech_environment']['persona_name'],db=s['reward_info']['db_check']['db_match'],nl_failed=[n['nl_assertion'] for n in s['reward_info'].get('nl_assertions') or [] if not n['met']],info=s.get('info')))
rows.sort(key=lambda r:r['task']);(OUT/'metrics.json').write_text(json.dumps(rows,indent=2,ensure_ascii=False))
print('rows',len(rows),'success',sum(r['reward'] for r in rows))
for r in rows:print(r['task'],r['reward'],'interrupts',r['interrupts'],'tools',r['tool_calls'],'errors',r['tool_errors'],'blank',r['blank_audio_ticks'],'/',r['audio_ticks'])
