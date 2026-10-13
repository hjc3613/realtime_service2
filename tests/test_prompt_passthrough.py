from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from service_app.config import Settings
from service_app.session import (
    HOT_WORDS_BY_DOMAIN,
    RealtimeSession,
    default_asr_context,
)


class PromptPassthroughTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_prompt_is_verbatim_and_asr_keeps_domain_hotwords(self):
        for domain in ['retail', 'airline', 'telecom']:
            with self.subTest(domain=domain), patch.dict('os.environ', {
                'SERVER_INSTRUCTION_SUFFIX': 'Legacy tool discipline.',
                'TELECOM_TEXT_PROMPT_MODE': 'full',
            }):
                session = RealtimeSession(lambda event: asyncio.sleep(0), Settings(
                    greeting='', tool_call_ack='', interactive_debug_dir=None,
                    asr_debug_dir=None, asr_prompt_mode='qwen3_omni',
                    asr_context=None,
                ))
                prompt = f'# {domain.title()} agent policy\n\nClient instructions only. '
                await session._session_update({'instructions': prompt})
                self.assertEqual(session.instructions, prompt)
                self.assertEqual(session.history[0]['content'], prompt)
                self.assertEqual(session.hotword_domain, domain)
                await session._session_update({})
                self.assertEqual(session.history[0]['content'], prompt)
                session.input_pcm.extend(b'\x01\x00' * 1600)
                session.speech_seen = True
                session.interactive.transcribe = AsyncMock(return_value='Hello.')
                session._start_response = AsyncMock()
                try:
                    await session._commit_user_audio()
                    asr_messages = session.interactive.transcribe.await_args.args[0]
                    self.assertEqual(asr_messages[0]['content'], default_asr_context('qwen3_omni', domain))
                    for hotword in HOT_WORDS_BY_DOMAIN[domain]:
                        self.assertIn(hotword, asr_messages[0]['content'])
                    self.assertEqual(session.history[0]['content'], prompt)
                finally:
                    await session.close()

    async def test_explicit_asr_context_remains_independent_of_text_prompt(self):
        session = RealtimeSession(lambda event: asyncio.sleep(0), Settings(
            greeting='', interactive_debug_dir=None, asr_debug_dir=None,
            asr_context='ASR spelling hints: custom product.',
            asr_prompt_mode='qwen3_asr',
        ))
        session.interactive.transcribe = AsyncMock(return_value='Hello.')
        session._start_response = AsyncMock()
        try:
            await session._session_update({'instructions': 'Client prompt.'})
            session.input_pcm.extend(b'\x01\x00' * 1600)
            session.speech_seen = True
            await session._commit_user_audio()
            asr_messages = session.interactive.transcribe.await_args.args[0]
            self.assertEqual(asr_messages[0]['content'], 'ASR spelling hints: custom product.')
            self.assertEqual(session.history[0]['content'], 'Client prompt.')
        finally:
            await session.close()
