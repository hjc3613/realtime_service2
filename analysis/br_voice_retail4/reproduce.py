"""Run selected CURRENT source methods offline; no backends or project imports.

AST extraction keeps production method bodies intact. Only transport, clocks,
and unrelated dependencies are replaced. This is a bounded behavioral probe,
not a full integration test or evidence of which version produced the run.
"""
import ast
import asyncio
import base64
import json
import logging
import pathlib
import re
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[2]

def extract(path, names):
    tree = ast.parse(path.read_text())
    nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    return compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes], type_ignores=[])), str(path), 'exec')

env = dict(asyncio=asyncio, base64=base64, logger=logging.getLogger('probe'),
           _event=lambda kind, **kw: dict(type=kind, **kw), ResponseInterrupted=RuntimeError)
exec(extract(ROOT/'service_app/session.py', ['_play_clause_audio', '_queue_pending_user_turn']), env)
b = {'CLAUSE_END_RE': re.compile(r'[;；:：.!?。！？\n]+')}
exec(extract(ROOT/'service_app/backends.py', ['streaming_clause_chunks']), b)

async def playback(cancel_at=None):
    records=[]; at_wait=[]
    state=SimpleNamespace(tts_interrupted=False,tts_playback_allowed=asyncio.Event(),
        settings=SimpleNamespace(tick_ms=200,tts_realtime_pacing=True),received_audio_bytes=0,
        session_id='offline-probe',current_item_id='probe-item',spoken_assistant_text='')
    state.tts_playback_allowed.set()
    async def send(event):records.append(event)
    async def wait(target):
        state.received_audio_bytes=target
        at_wait.append(dict(seconds=target/8000,transcript_events=sum('transcript.delta' in e['type'] for e in records)))
        if cancel_at is not None and target>=cancel_at:
            raise asyncio.CancelledError()
    state.send=send;state._wait_for_input_audio_clock=wait
    try:await env['_play_clause_audio'](state,'Please confirm both items before I submit the exchange.',b'\xff'*48000)
    except asyncio.CancelledError:pass
    return dict(audio_seconds_emitted=sum(len(base64.b64decode(e['delta'])) for e in records if e['type']=='response.output_audio.delta')/8000,
                transcript_events_during_audio=max(x['transcript_events'] for x in at_wait),
                transcript_events_total=sum('transcript.delta' in e['type'] for e in records))

async def main():
    complete=await playback(); interrupted=await playback(16000)
    assert complete==dict(audio_seconds_emitted=6.0,transcript_events_during_audio=0,transcript_events_total=1)
    assert interrupted==dict(audio_seconds_emitted=2.0,transcript_events_during_audio=0,transcript_events_total=0)
    state=SimpleNamespace(pending_user_turns=[],session_id='offline-probe')
    for text in ['Change the color to blue.', 'And make the size eight.']:
        env['_queue_pending_user_turn'](state, {'role':'user','content':text},{},'')
    assert len(state.pending_user_turns)==1
    chunks,tail=b['streaming_clause_chunks']('The price is $54.85. Your email is a.b@example.com.')
    assert 'The price is $54.' in chunks
    output={'full_clause_playback':complete,'interrupt_after_two_seconds':interrupted,
            'queued_user_text_remaining':[x[0]['content'] for x in state.pending_user_turns],
            'tts_chunks':chunks,'tts_tail':tail,'scope':'current source selected-method probe; no live inference'}
    print(json.dumps(output,indent=2));(pathlib.Path(__file__).parent/'reproduction.json').write_text(json.dumps(output,indent=2))

asyncio.run(main())
