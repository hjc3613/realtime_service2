from __future__ import annotations

import asyncio
import base64
import unittest
from unittest.mock import AsyncMock

from service_app.audio import wav_to_pcm16
from service_app.config import Settings
from service_app.session import RealtimeSession


class DebugSessionTests(unittest.IsolatedAsyncioTestCase):
    def make_session(self, debug=True):
        events = []

        async def send(event):
            events.append(event)

        session = RealtimeSession(
            send, Settings(greeting="", interactive_debug_dir=None)
        )
        session.debug_enabled = debug
        return session, events

    async def test_pause_rejects_input_without_advancing_clock_or_clearing_turn(self):
        session, events = self.make_session()
        session.input_pcm.extend(b"\x01\x00" * 160)
        before = bytes(session.input_pcm)
        await session.handle({"type": "debug.pause"})

        await session.handle({
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(b"\xff" * 1600).decode(),
        })
        await session.handle({"type": "input_audio_buffer.clear"})

        self.assertEqual(session.received_audio_bytes, 0)
        self.assertEqual(bytes(session.input_pcm), before)
        self.assertTrue(next(e for e in events if e["type"] == "debug.pause.updated")["paused"])
        await session.handle({"type": "debug.resume"})
        await session._append_audio(base64.b64encode(b"\xff" * 1600).decode())
        self.assertEqual(session.received_audio_bytes, 1600)
        await session.close()

    async def test_inflight_prediction_is_inspectable_but_applies_only_after_resume(self):
        session, events = self.make_session()
        started, release = asyncio.Event(), asyncio.Event()

        async def classify(messages, choices):
            started.set()
            await release.wait()
            return "incomplete"

        session.interactive.classify = classify
        pcm = b"\x01\x00" * 160
        session.input_pcm.extend(pcm)
        session.speech_seen = True
        messages = session._classifier_messages(pcm, interrupt=False)
        session.classifier_task = asyncio.create_task(
            session._classify_window(0, 0, False, pcm, messages, 10, 10)
        )
        task = session.classifier_task
        await started.wait()
        await session.handle({"type": "debug.pause"})
        release.set()
        for _ in range(20):
            if any(e["type"] == "debug.classifier.predicted" for e in events):
                break
            await asyncio.sleep(0)

        request = next(e for e in events if e["type"] == "debug.classifier.started")
        prediction = next(e for e in events if e["type"] == "debug.classifier.predicted")
        self.assertEqual(request["messages"], messages)
        url = request["messages"][-1]["content"][-1]["audio_url"]["url"]
        self.assertEqual(wav_to_pcm16(base64.b64decode(url.split(",", 1)[1]))[0], pcm)
        self.assertTrue(prediction["awaiting_resume"])
        self.assertFalse(session.semantic_turn_started)
        self.assertFalse(task.done())

        await session.handle({"type": "debug.resume"})
        await task
        self.assertTrue(session.semantic_turn_started)
        self.assertEqual(events[-1]["status"], "applied")
        await session.close()

    async def test_paused_session_answers_heartbeat_and_sends_buffers_only_on_pause(self):
        session, events = self.make_session()
        session.ignorable_pcm.extend(b"\x01\x00" * 16000 * 15)
        session.input_pcm.extend(b"\x02\x00" * 160)
        session.speech_seen = True
        session.interactive.classify = AsyncMock(return_value="incomplete")
        messages = session._classifier_messages(bytes(session.input_pcm), False)
        await session._classify_window(0, 0, False, bytes(session.input_pcm), messages, 10, 10)
        started = next(e for e in events if e["type"] == "debug.classifier.started")
        self.assertNotIn("buffers", started)
        self.assertEqual(started["buffer_audio_ms"]["ignorable"], 15000)

        await session.handle({"type": "debug.pause"})
        await session.handle({"type": "debug.ping"})

        self.assertEqual(events[-1]["type"], "debug.pong")
        snapshot = next(e for e in events if e["type"] == "debug.pause.snapshot")
        url = snapshot["buffers"]["ignorable"]["audio_url"]["url"]
        self.assertEqual(len(wav_to_pcm16(base64.b64decode(url.split(",", 1)[1]))[0]), 32000 * 15)
        self.assertTrue(session.debug_paused)
        await session.close()

    async def test_pause_holds_normal_output_and_close_releases_tasks(self):
        session, events = self.make_session()
        await session.handle({"type": "debug.pause"})
        output = asyncio.create_task(session.send({"type": "response.text.delta", "delta": "hello"}))
        await asyncio.sleep(0)
        self.assertFalse(output.done())
        self.assertFalse(any(e["type"] == "response.text.delta" for e in events))
        await session.handle({"type": "debug.resume"})
        await output
        self.assertEqual(events[-1]["delta"], "hello")
        await session.close()

    async def test_debug_is_opt_in(self):
        session, events = self.make_session(debug=False)
        await session.handle({"type": "debug.pause"})
        self.assertFalse(session.debug_paused)
        session.interactive.classify = AsyncMock(return_value="incomplete")
        session.speech_seen = True
        await session._classify_window(0, 0, False, b"\x00\x00", [], 0, 0)
        self.assertEqual(events, [])
        await session.close()

    async def test_on_demand_input_is_exact_and_inspectable_while_paused(self):
        session, events = self.make_session()
        session.debug_audio_on_demand = True
        session.interactive.classify = AsyncMock(return_value="incomplete")
        session.speech_seen = True
        pcm = b"\x01\x00" * 16000 * 10
        messages = session._classifier_messages(pcm, False)
        await session._classify_window(0, 0, False, pcm, messages, 10000, 10000)
        started = next(e for e in events if e["type"] == "debug.classifier.started")
        self.assertIsNone(started["messages"])
        self.assertLess(len(str(started)), 2000)
        await session.handle({"type": "debug.pause"})
        await session.handle({"type": "debug.classifier.inspect", "request_id": started["request_id"]})
        self.assertEqual(events[-1]["messages"], messages)
        await session.handle({"type": "debug.ping", "client_time_ms": 1234})
        self.assertEqual(events[-1]["client_time_ms"], 1234)
        await session.close()
        self.assertEqual(session.debug_classifier_inputs, {})

    async def test_on_demand_inputs_are_bounded_and_report_expiration(self):
        session, events = self.make_session()
        session.debug_audio_on_demand = True
        session.interactive.classify = AsyncMock(return_value="incomplete")
        session.speech_seen = True
        for _ in range(21):
            await session._classify_window(0, 0, False, b"\x01\x00", [], 0, 0)
        self.assertEqual(len(session.debug_classifier_inputs), 20)
        first = next(e for e in events if e["type"] == "debug.classifier.started")
        await session.handle({"type": "debug.classifier.inspect", "request_id": first["request_id"]})
        self.assertIsNone(events[-1]["messages"])
        self.assertIn("expired", events[-1]["error"])
        await session.close()
