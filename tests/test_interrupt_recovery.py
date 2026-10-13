from __future__ import annotations

import asyncio
import base64
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock

from service_app.audio import ulaw_to_pcm16
from service_app.config import Settings
from service_app.session import RealtimeSession


class SilentVAD:
    def is_speech(self, audio):
        return False


class DrainingTextBackend:
    def __init__(self, late_tool=False):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self.late_tool = late_tool
        self.fail_after_release = False

    async def stream(self, messages, tools):
        self.calls += 1
        if self.calls > 1:
            yield {'content': 'New reply.'}
            return
        yield {'content': 'Original reply.'}
        self.started.set()
        await self.release.wait()
        if self.fail_after_release:
            raise RuntimeError('Old generation failed after interruption')
        if self.late_tool:
            yield {'tool_calls': [{'index': 0, 'id': 'stale_call',
                                  'function': {'name': 'lookup', 'arguments': '{}'}}]}


class InterruptRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def make_session(self, late_tool=False):
        events = []

        async def send(event):
            events.append(event)

        session = RealtimeSession(send, Settings(
            greeting='', tool_call_ack='', interrupt_ack='',
            interactive_debug_dir=None, asr_debug_dir=None,
            tts_realtime_pacing=False, complete_confirmations=1,
            tick_ms=200, classifier_window_ticks=2, semantic_window_ms=1000,
        ))
        session.text = DrainingTextBackend(late_tool)
        session.tts.synthesize = AsyncMock(return_value=b'\xff' * 1600)
        session.interactive.classify = AsyncMock(return_value='complete')
        session.interactive.transcribe = AsyncMock(return_value='Please explain that again.')
        session.vad = SilentVAD()
        session.history = [{'role': 'system', 'content': 'Client instructions.'}]
        session.completed_user_turns = 1
        return session, events

    def seed_user_audio(self, session):
        audio = b'\x01' * 6400
        pcm, session.input_resample_state = ulaw_to_pcm16(audio)
        session.input_ulaw.extend(audio)
        session.input_pcm.extend(pcm)
        session.speech_seen = True
        session.speech_start_ms = 0
        session.candidate_speech_ms = 800
        session.received_audio_bytes = 6400
        session._append_semantic_timeline(audio)

    async def tick_silence(self, session, ms=400):
        await session._append_audio(base64.b64encode(b'\xff' * (ms * 8)).decode())
        for _ in range(50):
            await asyncio.sleep(0)

    async def settle_reply(self, session, old_task):
        session.text.release.set()
        await asyncio.wait_for(old_task, 1)
        for _ in range(50):
            await asyncio.sleep(0)
        if session.response_task is not old_task:
            await asyncio.wait_for(session.response_task, 1)

    async def test_completeness_and_asr_run_while_old_llm_drains(self):
        session, events = self.make_session()
        session.response_task = old_task = asyncio.create_task(session._run_response())
        try:
            await session.text.started.wait()
            self.seed_user_audio(session)
            await session._interrupt()
            await self.tick_silence(session)
            self.assertFalse(old_task.done())
            session.interactive.classify.assert_awaited_once()
            self.assertEqual(session.interactive.classify.await_args.args[1], ['complete', 'incomplete'])
            session.interactive.transcribe.assert_awaited_once()
            self.assertEqual(len(session.pending_user_turns), 1)
            await self.settle_reply(session, old_task)
            self.assertEqual(session.text.calls, 2)
            self.assertTrue(any(e['type'] == 'response.text.done' and e['text'] == 'New reply.' for e in events))
        finally:
            session.text.release.set()
            await session.close()

    async def test_confirmed_interrupt_preserves_utterance_beyond_recent_window(self):
        session, _ = self.make_session()
        self.seed_user_audio(session)
        await session._interrupt()
        # A late transition to semantic classification must still see the
        # speech, even after more than one second of silence has arrived.
        await session._append_audio(base64.b64encode(b'\xff' * 9600).decode())
        pcm, _ = session._semantic_audio_snapshot()
        try:
            self.assertTrue(session.semantic_turn_started)
            self.assertGreater(len(pcm) / 32, 1000)
            self.assertTrue(any(pcm[:25600]))
        finally:
            await session.close()

    async def test_silence_fallback_commits_without_another_user_question(self):
        session, _ = self.make_session()
        session.interactive.classify = AsyncMock(return_value='incomplete')
        session.response_task = old_task = asyncio.create_task(session._run_response())
        try:
            await session.text.started.wait()
            self.seed_user_audio(session)
            await session._interrupt()
            await self.tick_silence(session, ms=3000)
            session.interactive.transcribe.assert_awaited_once()
            await self.settle_reply(session, old_task)
            self.assertEqual(session.text.calls, 2)
        finally:
            session.text.release.set()
            await session.close()

    async def test_late_unissued_tool_does_not_block_replacement_reply(self):
        session, events = self.make_session(late_tool=True)
        session.response_task = old_task = asyncio.create_task(session._run_response())
        try:
            await session.text.started.wait()
            self.seed_user_audio(session)
            await session._interrupt()
            await self.tick_silence(session)
            await self.settle_reply(session, old_task)
            self.assertEqual(session.pending_tool_calls, {})
            self.assertFalse(any(e['type'] == 'response.function_call_arguments.done' for e in events))
            self.assertEqual(session.text.calls, 2)
        finally:
            session.text.release.set()
            await session.close()

    async def test_commit_reset_does_not_restore_interrupt_mode_during_drain(self):
        session, _ = self.make_session()
        session.interactive.classify = AsyncMock(return_value='incomplete')
        session.assistant_active = True
        session.tts_interrupted = True
        session.interrupt_confirmed = False  # Reset when the preceding turn committed.
        self.seed_user_audio(session)
        session.tick_audio.extend(b'\x01' * 3200)
        try:
            session._schedule_classification()
            await session.classifier_task
            self.assertEqual(session.interactive.classify.await_args.args[1], ['ignorable', 'incomplete', 'complete'])
            self.assertTrue(session.semantic_turn_started)
        finally:
            await session.close()

    async def test_old_generation_error_does_not_strand_committed_user_turn(self):
        session, events = self.make_session()
        session.text.fail_after_release = True
        session.response_task = old_task = asyncio.create_task(session._run_response())
        try:
            await session.text.started.wait()
            self.seed_user_audio(session)
            await session._interrupt()
            await self.tick_silence(session)
            await self.settle_reply(session, old_task)
            self.assertTrue(any(e['type'] == 'error' for e in events))
            self.assertEqual(session.text.calls, 2)
        finally:
            session.text.release.set()
            await session.close()

    async def test_already_emitted_tool_is_preserved_until_result_arrives(self):
        session, _ = self.make_session(late_tool=True)
        session.settings = replace(session.settings, tts_realtime_pacing=True)
        session.text.release.set()
        session.response_task = old_task = asyncio.create_task(session._run_response())
        try:
            for _ in range(100):
                if session.pending_tool_calls:
                    break
                await asyncio.sleep(0)
            self.assertIn('stale_call', session.pending_tool_calls)
            self.seed_user_audio(session)
            await session._interrupt()
            await asyncio.gather(old_task, return_exceptions=True)
            session.settings = replace(session.settings, tts_realtime_pacing=False)
            await self.tick_silence(session)
            self.assertIn('stale_call', session.pending_tool_calls)
            self.assertEqual(session.text.calls, 1)
            self.assertEqual(len(session.pending_user_turns), 1)
            await session.handle({'type': 'conversation.item.create', 'item': {
                'type': 'function_call_output', 'call_id': 'stale_call', 'output': '{}',
            }})
            await session.handle({'type': 'response.create'})
            await session.response_task
            self.assertEqual(session.pending_tool_calls, {})
            self.assertEqual(session.text.calls, 2)
        finally:
            session.text.release.set()
            await session.close()
