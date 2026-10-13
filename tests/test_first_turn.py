from __future__ import annotations

import asyncio
import base64
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock

from service_app.audio import ulaw_to_pcm16, wav_to_pcm16
from service_app.config import Settings
from service_app.session import RealtimeSession


class SpeechVAD:
    def is_speech(self, audio):
        return True


class FirstTurnTests(unittest.IsolatedAsyncioTestCase):
    def make_session(self, greeting=''):
        events = []

        async def send(event):
            events.append(event)

        session = RealtimeSession(send, Settings(
            greeting=greeting, tool_call_ack='', interrupt_ack='',
            interactive_debug_dir=None, asr_debug_dir=None,
            tick_ms=200, classifier_window_ticks=2, complete_confirmations=1,
            semantic_window_ms=1000, tts_realtime_pacing=False,
        ))
        session.vad = SpeechVAD()
        session.interactive.classify = AsyncMock(return_value='incomplete')
        session.interactive.transcribe = AsyncMock(return_value='First question.')
        session._start_response = AsyncMock()
        return session, events

    async def append(self, session, ms=400):
        await session._append_audio(base64.b64encode(b'\x01' * (ms * 8)).decode())
        if session.classifier_task:
            await session.classifier_task

    def model_pcm(self, session):
        messages = session.interactive.classify.await_args.args[0]
        url = messages[-1]['content'][-1]['audio_url']['url']
        return wav_to_pcm16(base64.b64decode(url.split(',', 1)[1]))[0]

    async def test_first_turn_uses_two_labels_and_complete_accumulated_audio(self):
        session, _ = self.make_session()
        session.interaction_history = [{'role': 'assistant', 'content': 'Hello.'}]
        try:
            await self.append(session, ms=2400)
            self.assertEqual(session.interactive.classify.await_args.args[1], ['complete', 'incomplete'])
            self.assertGreater(len(self.model_pcm(session)) / 32, 2300)
            prompt = session.interactive.classify.await_args.args[0][0]['content']
            self.assertNotIn('ignorable', prompt)
            self.assertIn('pauses with background speech', prompt)
            self.assertIn('A target user may change topics.', prompt)
            self.assertTrue(prompt.endswith('Reply with exactly one of: incomplete, complete.'))
            self.assertEqual(session.completed_user_turns, 0)
        finally:
            await session.close()

    async def test_first_successful_commit_enables_three_labels_for_next_turn(self):
        session, _ = self.make_session()
        try:
            await self.append(session)
            await session._commit_user_audio()
            self.assertEqual(session.completed_user_turns, 1)
            session.interactive.classify = AsyncMock(return_value='ignorable')
            await self.append(session)
            self.assertEqual(session.interactive.classify.await_args.args[1], ['ignorable', 'incomplete', 'complete'])
            self.assertFalse(session.semantic_turn_started)
            self.assertEqual(session.input_pcm, b'')
            self.assertTrue(session.ignorable_pcm)
        finally:
            await session.close()

    async def test_empty_or_failed_asr_does_not_advance_first_turn(self):
        for result in ['', RuntimeError('ASR unavailable')]:
            with self.subTest(result=repr(result)):
                session, _ = self.make_session()
                if isinstance(result, Exception):
                    session.interactive.transcribe = AsyncMock(side_effect=result)
                else:
                    session.interactive.transcribe = AsyncMock(return_value=result)
                try:
                    await self.append(session)
                    await session._commit_user_audio()
                    self.assertEqual(session.completed_user_turns, 0)
                    await self.append(session)
                    self.assertEqual(session.interactive.classify.await_args.args[1], ['complete', 'incomplete'])
                finally:
                    await session.close()

    async def test_unexpected_first_turn_ignorable_preserves_audio(self):
        session, _ = self.make_session()
        session.interactive.classify = AsyncMock(return_value='ignorable')
        try:
            await self.append(session)
            self.assertTrue(session.semantic_turn_started)
            self.assertTrue(session.input_pcm)
            self.assertEqual(session.ignorable_pcm, b'')
        finally:
            await session.close()

    async def test_greeting_is_protected_before_task_start_and_retains_user_audio(self):
        session, events = self.make_session(greeting='Hello.')
        started, release = asyncio.Event(), asyncio.Event()

        async def speak(text):
            started.set()
            await release.wait()

        session._speak = speak
        session.interactive.classify = AsyncMock(return_value='complete')
        try:
            await session._session_update({'instructions': 'Client prompt.'})
            greeting_task = session.response_task
            self.assertTrue(session.greeting_active)
            await self.append(session)
            await asyncio.wait_for(started.wait(), 1)
            await self.append(session)
            session._schedule_user_commit()
            await session._commit_user_audio()
            await session._apply_interrupt_classification('interrupt')
            await session._apply_interrupt_classification('interrupt')
            await session._interrupt()
            self.assertFalse(session.tts_interrupted)
            self.assertTrue(session.tts_playback_allowed.is_set())
            self.assertFalse(greeting_task.done())
            self.assertEqual(session.received_audio_bytes, 6400)
            session.interactive.classify.assert_not_awaited()
            session.interactive.transcribe.assert_not_awaited()
            release.set()
            await asyncio.wait_for(greeting_task, 1)
            for _ in range(50):
                await asyncio.sleep(0)
            self.assertFalse(session.greeting_active)
            self.assertFalse(any(e['type'] == 'response.cancelled' for e in events))
            self.assertTrue(any(e['type'] == 'response.text.done' and e['text'] == 'Hello.' for e in events))
            session.interactive.transcribe.assert_awaited_once()
            session._start_response.assert_awaited_once()
            self.assertEqual(session.interactive.classify.await_args.args[1], ['complete', 'incomplete'])
            self.assertEqual(session.completed_user_turns, 1)
        finally:
            release.set()
            await session.close()

    async def test_protected_greeting_keeps_input_audio_clock_running(self):
        session, events = self.make_session(greeting='Hello.')
        session.settings = replace(
            session.settings, tts_realtime_pacing=True, interrupt_enabled=False
        )
        session.tts.synthesize = AsyncMock(return_value=b'\xff' * 1600)
        try:
            await session._session_update({'instructions': 'Client prompt.'})
            task = session.response_task
            for _ in range(50):
                if any(e['type'] == 'response.output_audio.delta' for e in events):
                    break
                await asyncio.sleep(0)
            self.assertTrue(session.greeting_active)
            await self.append(session)
            await asyncio.wait_for(task, 1)
            self.assertEqual(session.received_audio_bytes, 3200)
            self.assertTrue(session.input_pcm)
            self.assertFalse(any(e['type'] == 'response.cancelled' for e in events))
            self.assertTrue(any(e['type'] == 'response.output_audio.done' for e in events))
        finally:
            await session.close()

    async def test_later_response_still_supports_interruption(self):
        session, _ = self.make_session()
        session.completed_user_turns = 1
        session.assistant_active = True
        session.input_ulaw.extend(b'\x01' * 6400)
        session.input_pcm.extend(ulaw_to_pcm16(bytes(session.input_ulaw))[0])
        session.speech_seen = True
        session.candidate_speech_ms = 800
        session.interactive.classify = AsyncMock(return_value='interrupt')
        try:
            await self.append(session)
            self.assertEqual(session.interactive.classify.await_args.args[1], ['interrupt', 'backchannel', 'ignorable'])
            await session._apply_interrupt_classification('interrupt')
            self.assertTrue(session.interrupt_confirmed)
        finally:
            await session.close()

    async def test_reset_history_restores_first_turn_policy(self):
        session, _ = self.make_session()
        session.completed_user_turns = 2
        try:
            await session._session_update({'instructions': 'New client prompt.'})
            self.assertEqual(session.completed_user_turns, 0)
            await self.append(session)
            self.assertEqual(session.interactive.classify.await_args.args[1], ['complete', 'incomplete'])
        finally:
            await session.close()
