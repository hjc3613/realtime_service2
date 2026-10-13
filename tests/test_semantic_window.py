from __future__ import annotations

import asyncio
import base64
import unittest
from unittest.mock import AsyncMock

from service_app.audio import wav_to_pcm16
from service_app.config import Settings
from service_app.session import RealtimeSession


class FixedVAD:
    speech = True

    def is_speech(self, audio):
        return self.speech


class SemanticWindowTests(unittest.IsolatedAsyncioTestCase):
    def make_session(self, window_ms=1000):
        events = []

        async def send(event):
            events.append(event)

        session = RealtimeSession(send, Settings(
            greeting='', interactive_debug_dir=None, asr_debug_dir=None,
            tick_ms=200, classifier_window_ticks=2, semantic_window_ms=window_ms,
        ))
        session.debug_enabled = True
        session.completed_user_turns = 1
        session.vad = FixedVAD()
        session.interactive.classify = AsyncMock(return_value='ignorable')
        return session, events

    async def append(self, session, value, ms=400, finish=True):
        await session._append_audio(base64.b64encode(bytes([value]) * (ms * 8)).decode())
        if finish and session.classifier_task:
            await session.classifier_task

    def current_pcm(self, messages):
        audio = next(block for block in messages[-1]['content'] if block['type'] == 'audio_url')
        url = audio['audio_url']['url']
        return wav_to_pcm16(base64.b64decode(url.split(',', 1)[1]))[0]

    async def test_400ms_cadence_keeps_overlapping_one_second_after_ignorable(self):
        session, events = self.make_session()
        for value in [1, 2, 3, 4]:
            await self.append(session, value)
            self.assertFalse(session.speech_seen)
            self.assertEqual(session.input_pcm, b'')
            self.assertIsNone(session.classifier_task)
        calls = session.interactive.classify.await_args_list
        durations = [len(self.current_pcm(c.args[0])) / 32 for c in calls]
        for actual, expected in zip(durations, [400, 800, 1000, 1000]):
            self.assertAlmostEqual(actual, expected, delta=1)
        self.assertEqual(bytes(session.semantic_window_ulaw), b'\x02' * 1600 + b'\x03' * 3200 + b'\x04' * 3200)
        clocks = [e['audio_clock_ms'] for e in events if e['type'] == 'debug.classifier.started']
        self.assertEqual(clocks, [400, 800, 1200, 1600])
        await session.close()

    async def test_incomplete_restores_onset_for_accumulated_audio_and_asr(self):
        session, _ = self.make_session()
        session.interactive.classify = AsyncMock(side_effect=['ignorable', 'incomplete', 'incomplete'])
        await self.append(session, 1)
        await self.append(session, 2)
        accepted_pcm = self.current_pcm(session.interactive.classify.await_args_list[1].args[0])
        self.assertTrue(session.semantic_turn_started)
        self.assertEqual(bytes(session.input_pcm), accepted_pcm)
        self.assertEqual(bytes(session.input_ulaw), b'\x01' * 3200 + b'\x02' * 3200)
        await self.append(session, 3)
        accumulated_pcm = self.current_pcm(session.interactive.classify.await_args_list[2].args[0])
        self.assertGreater(len(accumulated_pcm) / 32, 1000)
        self.assertTrue(accumulated_pcm.startswith(accepted_pcm))
        session.interactive.transcribe = AsyncMock(return_value='Hello')
        session._start_response = AsyncMock()
        await session._commit_user_audio()
        asr_messages = session.interactive.transcribe.await_args.args[0]
        self.assertEqual(self.current_pcm(asr_messages), accumulated_pcm)
        await session.close()

    async def test_ignorable_only_removes_classified_candidate_not_new_input(self):
        session, _ = self.make_session()
        await self.append(session, 1)
        release, started = asyncio.Event(), asyncio.Event()

        async def classify(messages, choices):
            started.set()
            await release.wait()
            return 'ignorable'

        session.interactive.classify = classify
        await self.append(session, 2, finish=False)
        task = session.classifier_task
        await started.wait()
        await self.append(session, 3, ms=200, finish=False)
        newer_pcm = bytes(session.input_pcm[-6400:])
        release.set()
        await task
        self.assertEqual(bytes(session.input_ulaw), b'\x03' * 1600)
        self.assertEqual(bytes(session.input_pcm), newer_pcm)
        self.assertEqual(bytes(session.tick_audio), b'\x03' * 1600)
        self.assertTrue(session.speech_seen)
        self.assertEqual(len(session.semantic_window_ulaw), 8000)
        await session.close()

    async def test_silence_expires_old_context_and_does_not_trigger_classification(self):
        session, _ = self.make_session()
        await self.append(session, 1)
        session.vad.speech = False
        for _ in range(10):
            await self.append(session, 255, ms=200)
        self.assertEqual(session.interactive.classify.await_count, 1)
        self.assertEqual(bytes(session.semantic_window_ulaw), b'\xff' * 8000)
        session.vad.speech = True
        await self.append(session, 2)
        self.assertNotIn(1, session.semantic_window_ulaw)
        await session.close()

    async def test_window_is_configurable_and_zero_preserves_legacy_behavior(self):
        for window_ms, expected_ms in [(600, 600), (1500, 1200), (0, 400)]:
            with self.subTest(window_ms=window_ms):
                session, _ = self.make_session(window_ms)
                for value in [1, 2, 3]:
                    await self.append(session, value)
                pcm = self.current_pcm(session.interactive.classify.await_args_list[-1].args[0])
                self.assertAlmostEqual(len(pcm) / 32, expected_ms, delta=1)
                session._clear_input()
                self.assertEqual(session.semantic_window_ulaw, b'')
                await session.close()

    async def test_interrupt_input_remains_three_seconds(self):
        session, events = self.make_session()
        session.assistant_active = True
        await self.append(session, 1)
        await self.append(session, 2)
        started = next(e for e in events if e['type'] == 'debug.classifier.started')
        self.assertEqual(started['kind'], 'interrupt')
        self.assertAlmostEqual(started['audio_ms'], 3000, delta=1)
        await session.close()

    async def test_pause_freezes_recent_context_and_snapshot_exposes_it(self):
        session, events = self.make_session()
        await self.append(session, 1)
        before = bytes(session.semantic_window_ulaw)
        await session.handle({'type': 'debug.pause'})
        snapshot = next(e for e in events if e['type'] == 'debug.pause.snapshot')
        self.assertIn('semantic', snapshot['buffers'])
        await self.append(session, 2)
        self.assertEqual(bytes(session.semantic_window_ulaw), before)
        await session.handle({'type': 'debug.resume'})
        await self.append(session, 3)
        self.assertEqual(bytes(session.semantic_window_ulaw), before + b'\x03' * 3200)
        await session.close()
