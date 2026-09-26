from __future__ import annotations

import asyncio
import base64
import unittest
from unittest.mock import AsyncMock
from dataclasses import replace

from service_app.audio import ulaw_to_pcm16, wav_to_pcm16
from service_app.config import Settings
from service_app.session import RealtimeSession


class _ToolCallingTextBackend:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = False

    async def stream(self, messages, tools):
        yield {"content": "I am checking that now.", "tool_calls": []}
        self.started.set()
        await self.release.wait()
        self.finished = True
        yield {
            "content": "",
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_latest",
                    "function": {"name": "lookup", "arguments": "{}"},
                }
            ],
        }


class _MultiToolCallingTextBackend:
    async def stream(self, messages, tools):
        yield {
            "content": "",
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_1",
                    "function": {"name": "lookup", "arguments": '{"id": 1}'},
                },
                {
                    "index": 1,
                    "id": "call_2",
                    "function": {"name": "lookup", "arguments": '{"id": 2}'},
                },
            ],
        }


class _ReasoningToolCallingTextBackend:
    async def stream(self, messages, tools):
        yield {"reasoning_content": "I need to look "}
        yield {
            "reasoning_content": "this up.",
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_reasoning",
                    "function": {"name": "lookup", "arguments": "{}"},
                }
            ],
        }


class _NeverSpeechVAD:
    def is_speech(self, audio: bytes) -> bool:
        return False


class _AlwaysSpeechVAD:
    def is_speech(self, audio: bytes) -> bool:
        return True


class _BlockingInteractive:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def classify(self, messages, labels):
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await self.release.wait()
        return "incomplete"


class _CompletingInteractive:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self.audio_lengths: list[int] = []

    async def classify(self, messages, labels):
        audio_url = messages[-1]["content"][1]["audio_url"]["url"]
        wav = base64.b64decode(audio_url.split(",", 1)[1])
        self.audio_lengths.append(len(wav_to_pcm16(wav)))
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await self.release.wait()
        return "complete"

    async def transcribe(self, messages):
        return ""


class _TTSBackend:
    async def synthesize(self, text: str) -> bytes:
        return b"\xff" * 1600


class RealtimeSessionTests(unittest.IsolatedAsyncioTestCase):
    def make_session(self) -> tuple[RealtimeSession, list[dict]]:
        events: list[dict] = []

        async def send(event: dict) -> None:
            events.append(event)

        settings = Settings(
            greeting="",
            interactive_debug_dir=None,
            tts_realtime_pacing=False,
            tool_call_ack="",
        )
        return RealtimeSession(send, settings), events

    async def test_session_appends_server_instruction_suffix_once(self) -> None:
        events: list[dict] = []

        async def send(event: dict) -> None:
            events.append(event)

        session = RealtimeSession(
            send,
            Settings(
                greeting="",
                interactive_debug_dir=None,
                server_instruction_suffix="Local tool discipline.",
            ),
        )

        await session._session_update({"instructions": "Tau retail policy."})
        self.assertEqual(
            session.history[0]["content"],
            "Tau retail policy.\n\nLocal tool discipline.",
        )

        await session._session_update({})
        self.assertEqual(
            session.history[0]["content"].count("Local tool discipline."), 1
        )

    async def test_pending_fragments_are_retained_in_order(self) -> None:
        session, _ = self.make_session()
        session._queue_pending_user_turn(
            {"role": "user", "content": "old request"},
            {"role": "user", "content": "old request"},
            "",
        )
        session._queue_pending_user_turn(
            {"role": "user", "content": "latest request"},
            {"role": "user", "content": "latest request"},
            "",
        )

        self.assertEqual(len(session.pending_user_turns), 2)
        session._run_response = AsyncMock()
        session._start_deferred_response()
        await session.response_task
        self.assertEqual(
            [m["content"] for m in session.history], ["old request", "latest request"]
        )
        session._run_response.assert_awaited_once()

    async def test_pending_turn_waits_until_user_releases_floor(self) -> None:
        session, _ = self.make_session()
        session._run_response = AsyncMock()
        session._queue_pending_user_turn(
            {"role": "user", "content": "latest request"},
            {"role": "user", "content": "latest request"},
            "",
        )
        session.speech_seen = True

        session._start_deferred_response()
        self.assertIsNone(session.response_task)

        session.speech_seen = False
        session._start_deferred_response()
        await asyncio.sleep(0)

        self.assertEqual(session.history[-1]["content"], "latest request")
        session._run_response.assert_awaited_once()

    async def test_latest_turn_wins_over_tool_continuation(self) -> None:
        session, _ = self.make_session()
        session._run_response = AsyncMock()
        session.history = [
            {"role": "system", "content": "test"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_old",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{}"},
                    }
                ],
            },
        ]
        session.pending_tool_calls["call_old"] = {
            "id": "call_old",
            "name": "lookup",
            "arguments": "{}",
        }
        session._queue_pending_user_turn(
            {"role": "user", "content": "latest request"},
            {"role": "user", "content": "latest request"},
            "",
        )

        await session.handle(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": "call_old",
                    "output": "{}",
                },
            }
        )
        await session.handle({"type": "response.create"})
        await asyncio.sleep(0)

        self.assertEqual(session.history[-1]["content"], "latest request")
        self.assertFalse(session.tool_continuation_pending)
        session._run_response.assert_awaited_once()

    async def test_interrupt_cancels_tts_but_not_response_task(self) -> None:
        session, _ = self.make_session()
        response_release = asyncio.Event()
        tts_release = asyncio.Event()
        session.response_task = asyncio.create_task(response_release.wait())
        session.tts_task = asyncio.create_task(tts_release.wait())

        await session._interrupt()
        await asyncio.sleep(0)

        self.assertFalse(session.response_task.done())
        self.assertTrue(session.tts_task.cancelled())

        session.response_task.cancel()
        await asyncio.gather(session.response_task, return_exceptions=True)

    async def test_interrupted_response_drains_llm_without_executing_stale_tool(self) -> None:
        session, events = self.make_session()
        text = _ToolCallingTextBackend()
        session.text = text
        session.tts = _TTSBackend()
        session.history = [{"role": "system", "content": "test"}]
        session.tools = [
            {
                "name": "lookup",
                "description": "",
                "parameters": {"type": "object", "properties": {}},
            }
        ]

        session.response_task = asyncio.create_task(session._run_response())
        await text.started.wait()
        await session._interrupt()
        text.release.set()
        await session.response_task

        self.assertTrue(text.finished)
        self.assertNotIn("call_latest", session.pending_tool_calls)
        event_types = [event["type"] for event in events]
        self.assertNotIn("response.function_call_arguments.done", event_types)
        self.assertIn("response.cancelled", event_types)
        self.assertNotIn("response.text.done", event_types)

    async def test_parallel_completion_executes_only_first_tool_and_records_only_that_tool(self) -> None:
        session, events = self.make_session()
        session.text = _MultiToolCallingTextBackend()
        session.tts = _TTSBackend()
        session.history = [{"role": "system", "content": "test"}]
        session.tools = [
            {
                "name": "lookup",
                "description": "",
                "parameters": {"type": "object", "properties": {}},
            }
        ]

        await session._run_response()

        self.assertEqual(set(session.pending_tool_calls), {"call_1"})
        argument_events = [
            event
            for event in events
            if event["type"] == "response.function_call_arguments.done"
        ]
        self.assertEqual(
            [(event["call_id"], event["arguments"]) for event in argument_events],
            [("call_1", '{"id": 1}')],
        )
        self.assertEqual(len(session.history[-1]["tool_calls"]), 1)

    async def test_response_preserves_reasoning_content_for_tool_continuation(
        self,
    ) -> None:
        session, _ = self.make_session()
        session.text = _ReasoningToolCallingTextBackend()
        session.tts = _TTSBackend()
        session.history = [{"role": "system", "content": "test"}]
        session.tools = [
            {
                "name": "lookup",
                "description": "",
                "parameters": {"type": "object", "properties": {}},
            }
        ]

        await session._run_response()

        self.assertEqual(
            session.history[-1]["reasoning_content"], "I need to look this up."
        )
        self.assertEqual(session.history[-1]["tool_calls"][0]["id"], "call_reasoning")

    async def test_tool_continuation_waits_for_all_results(self) -> None:
        session, _ = self.make_session()
        session._run_response = AsyncMock()
        session.history = [
            {"role": "system", "content": "test"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{}"},
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{}"},
                    },
                ],
            },
        ]
        session.pending_tool_calls = {
            "call_1": {"id": "call_1", "name": "lookup", "arguments": "{}"},
            "call_2": {"id": "call_2", "name": "lookup", "arguments": "{}"},
        }

        for call_id in ("call_1", "call_2"):
            await session.handle(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": "{}",
                    },
                }
            )
            await session.handle({"type": "response.create"})
            await asyncio.sleep(0)
            if call_id == "call_1":
                session._run_response.assert_not_awaited()

        session._run_response.assert_awaited_once()

    async def test_audio_ingest_continues_while_classifier_is_in_flight(self) -> None:
        session, _ = self.make_session()
        interactive = _BlockingInteractive()
        session.interactive = interactive
        session.vad = _AlwaysSpeechVAD()
        packet = base64.b64encode(bytes([255]) * 1600).decode()

        await session._append_audio(packet)
        await session._append_audio(packet)
        await interactive.started.wait()
        self.assertIsNotNone(session.classifier_task)
        self.assertFalse(session.classifier_task.done())

        await asyncio.wait_for(session._append_audio(packet), timeout=0.1)
        await asyncio.wait_for(session._append_audio(packet), timeout=0.1)
        self.assertEqual(session.received_audio_bytes, 6400)

        interactive.release.set()
        for _ in range(20):
            if interactive.calls >= 2:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(interactive.calls, 2)
        await session.close()

    async def test_interrupt_waits_for_minimum_speech_before_classifying(self) -> None:
        session, _ = self.make_session()
        session.assistant_active = True
        session.speech_seen = True
        session.candidate_speech_ms = session.settings.interrupt_min_speech_ms - 1
        session.tick_audio.extend(bytes([1]) * session._classifier_window_bytes())
        session.input_ulaw.extend(bytes([1]) * session._classifier_window_bytes())
        pcm, _ = ulaw_to_pcm16(bytes(session.input_ulaw))
        session.input_pcm.extend(pcm)
        session._reset_interrupt_timeline()
        session.interactive.classify = AsyncMock(return_value="ignorable")

        session._schedule_classification()
        self.assertIsNone(session.classifier_task)

        session.candidate_speech_ms += 1
        session._schedule_classification()
        self.assertIsNotNone(session.classifier_task)
        await session.classifier_task
        session.interactive.classify.assert_awaited_once()

    async def test_interrupt_timeline_slides_and_survives_candidate_reset(self) -> None:
        session, _ = self.make_session()
        session.assistant_active = True
        session.vad = _NeverSpeechVAD()
        session._reset_interrupt_timeline()
        window_bytes = session._interrupt_window_bytes()
        packet_bytes = 1600
        packet = base64.b64encode(bytes([1]) * packet_bytes).decode()

        await session._append_audio(packet)

        self.assertEqual(len(session.interrupt_window_ulaw), window_bytes)
        self.assertEqual(
            bytes(session.interrupt_window_ulaw[-packet_bytes:]),
            bytes([1]) * packet_bytes,
        )
        self.assertEqual(
            bytes(session.interrupt_window_ulaw[: window_bytes - packet_bytes]),
            bytes([255]) * (window_bytes - packet_bytes),
        )

        timeline = bytes(session.interrupt_window_ulaw)
        session.speech_seen = True
        session.candidate_speech_ms = session.settings.interrupt_window_ms
        session.input_ulaw.extend(bytes([2]) * packet_bytes)
        await session._apply_interrupt_classification("ignorable")
        await session._apply_interrupt_classification("ignorable")

        self.assertFalse(session.speech_seen)
        self.assertEqual(bytes(session.interrupt_window_ulaw), timeline)

    async def test_interrupt_uses_two_positive_votes_in_last_three(self) -> None:
        session, _ = self.make_session()
        session.assistant_active = True
        session._interrupt = AsyncMock()

        await session._apply_interrupt_classification("interrupt")
        self.assertFalse(session.tts_playback_allowed.is_set())
        await session._apply_interrupt_classification("ignorable")
        self.assertFalse(session.tts_playback_allowed.is_set())
        await session._apply_interrupt_classification("interrupt")

        session._interrupt.assert_awaited_once()

    async def test_completed_turn_does_not_schedule_empty_classification(self) -> None:
        session, _ = self.make_session()
        interactive = _CompletingInteractive()
        session.interactive = interactive
        session.vad = _AlwaysSpeechVAD()
        packet = base64.b64encode(bytes([255]) * 1600).decode()

        await session._append_audio(packet)
        await session._append_audio(packet)
        await interactive.started.wait()
        await session._append_audio(packet)
        await session._append_audio(packet)

        interactive.release.set()
        for _ in range(100):
            if (
                interactive.calls == 2
                and session.classifier_task is None
                and session.user_commit_task is None
            ):
                break
            await asyncio.sleep(0.01)

        self.assertEqual(interactive.calls, 2)
        self.assertEqual(len(interactive.audio_lengths), 2)
        self.assertTrue(all(length > 0 for length in interactive.audio_lengths))
        await session.close()

    async def test_tool_continuation_ignores_noisy_speech_candidate(self) -> None:
        session, _ = self.make_session()
        session._run_response = AsyncMock()
        session.history = [
            {"role": "system", "content": "test"},
            {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
        ]
        session.tool_continuation_pending = True
        session.speech_seen = True
        session.classification_running = True

        session._start_deferred_response()
        await asyncio.sleep(0)

        self.assertIsNotNone(session.response_task)
        self.assertFalse(session.tool_continuation_pending)
        session._run_response.assert_awaited_once()

    async def test_transcript_is_available_each_audio_tick_and_survives_interrupt(self):
        session, events = self.make_session()
        session.settings = replace(session.settings, tts_realtime_pacing=True)
        session.current_item_id = "item_test"
        text = "Please confirm both items before submitting the exchange."
        waits = []

        async def clock(target):
            waits.append(target)
            prefix = "".join(e["delta"] for e in events
                             if e["type"] == "response.output_audio_transcript.delta")
            expected = text[:len(text) * target // 48000]
            self.assertEqual(prefix, expected)
            if target == 16000:
                raise asyncio.CancelledError()

        session._wait_for_input_audio_clock = clock
        with self.assertRaises(asyncio.CancelledError):
            await session._play_clause_audio(text, b"\xff" * 48000)
        self.assertEqual(len(waits), 10)
        self.assertEqual(session._heard_assistant_text(), text[:len(text) // 3].rstrip())
        self.assertFalse(any(e["type"] == "response.output_audio_transcript.done"
                             for e in events))

    async def test_last_audio_packet_contains_final_text_without_next_clause(self):
        session, events = self.make_session()
        session.current_item_id = "item_test"
        session.settings = replace(session.settings, tts_realtime_pacing=True)
        session._wait_for_input_audio_clock = AsyncMock()
        await session._play_clause_audio("The price is $54.85.", b"\xff" * 9500)
        self.assertEqual(events[-1]["type"], "response.output_audio.delta")
        self.assertEqual("".join(e["delta"] for e in events
                                if e["type"] == "response.output_audio_transcript.delta"),
                         "The price is $54.85.")
        await session._play_clause_audio("Please confirm.", b"\xff" * 1600)
        self.assertEqual("".join(e["delta"] for e in events
                                if e["type"] == "response.output_audio_transcript.delta"),
                         "The price is $54.85. Please confirm.")

    async def test_asr_commits_preserve_existing_pending_fragment(self):
        session, _ = self.make_session()
        session._run_response = AsyncMock()
        session._queue_pending_user_turn(
            {"role": "user", "content": "Make it blue."}, {}, ""
        )
        session.speech_seen = True
        session.input_pcm.extend(b"\x00" * 32000)
        session.interactive.transcribe = AsyncMock(return_value="And size eight.")
        await session._commit_user_audio()
        await session.response_task
        self.assertEqual([m["content"] for m in session.history],
                         ["Make it blue.", "And size eight."])

    async def test_history_user_audio_is_limited_to_five_seconds(self) -> None:
        session, _ = self.make_session()
        ten_seconds_pcm = b"\x00\x00" * 16000 * 10

        trimmed = session._history_user_pcm(ten_seconds_pcm)

        self.assertEqual(len(trimmed), 32000 * 5)


if __name__ == "__main__":
    unittest.main()
