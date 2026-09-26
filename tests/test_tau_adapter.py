"""Optional offline integration with the adjacent tau2-bench checkout.

No provider connection or inference: feed real service events through BR's
inherited event parser and tick implementation. Run with tau2 on PYTHONPATH.
"""
from __future__ import annotations

import asyncio
import importlib.util
import unittest

from service_app.config import Settings
from service_app.session import RealtimeSession

TAU_AVAILABLE = importlib.util.find_spec("tau2") is not None
if TAU_AVAILABLE:
    from tau2.voice.audio_native.br.discrete_time_adapter import DiscreteTimeBRRealtimeAdapter
    from tau2.voice.audio_native.openai.events import parse_realtime_event

    class ReplayAdapter(DiscreteTimeBRRealtimeAdapter):
        """Keep the real buffer/transcript algorithm; replace network IO only."""
        def __init__(self):
            super().__init__(tick_duration_ms=200)
            self.batch = []

        async def _execute_tick(self, user_audio, tick_number, result, tick_start):
            for raw in self.batch:
                await self._process_event(result, parse_realtime_event(raw))
            self.batch = []


@unittest.skipUnless(TAU_AVAILABLE, "put the tau2-bench src directory on PYTHONPATH")
class TauAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_playback_partial_cancel_and_final_packet_reach_real_adapter(self):
        for cancel_after in (None, 3200):
            adapter = ReplayAdapter()
            delivered = []
            text = "The camera is the most expensive item."

            async def send(event):
                adapter.batch.append(event)

            session = RealtimeSession(send, Settings(greeting="", interactive_debug_dir=None))
            session.current_item_id = "replay_item"

            async def clock(target):
                result = await adapter._async_run_tick(b"\xff" * 1600, len(delivered))
                delivered.append(result.proportional_transcript)
                self.assertEqual("".join(delivered), text[:len(text) * target // 8000])
                if target == cancel_after:
                    raise asyncio.CancelledError()

            session._wait_for_input_audio_clock = clock
            try:
                await session._play_clause_audio(text, b"\xff" * 8000)
            except asyncio.CancelledError:
                self.assertIsNotNone(cancel_after)
            expected = text if cancel_after is None else text[:len(text) * cancel_after // 8000]
            self.assertEqual("".join(delivered), expected)
            self.assertTrue(all(delivered))

    async def test_batched_audio_drains_all_text_including_short_final_packet(self):
        adapter = ReplayAdapter()
        text = "Refund $54.85 to your original payment method."

        async def send(event):
            adapter.batch.append(event)

        session = RealtimeSession(send, Settings(greeting="", interactive_debug_dir=None,
                                                 tts_realtime_pacing=False))
        session.current_item_id = "buffered_item"
        await session._play_clause_audio(text, b"\xff" * 9500)
        delivered = []
        for tick in range(6):
            result = await adapter._async_run_tick(b"\xff" * 1600, tick)
            delivered.append(result.proportional_transcript)
        self.assertEqual("".join(delivered), text)
        self.assertFalse(adapter._buffered_agent_audio)
