from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
import uuid
from typing import Any, Awaitable, Callable

from .asr_debug import ASRDebugRecorder
from .audio import VoiceDetector, ulaw_rms, ulaw_to_pcm16
from .backends import (
    InteractiveBackend,
    TextBackend,
    TTSBackend,
    audio_message,
    clause_chunks,
    streaming_clause_chunks,
)
from .classifier_debug import ClassifierDebugRecorder
from .config import Settings

Send = Callable[[dict[str, Any]], Awaitable[None]]
logger = logging.getLogger(__name__)
# Domain-specific ASR spelling hints. Keep each domain isolated so callers can
# select only the vocabulary relevant to the active evaluation scenario.
RETAIL_HOT_WORDS = [
    '101 Highway, New York, 10001',
    '1234 Elm St, Springfield, IL, 62701',
    '445 Maple Drive, Suite 394, Fort Worth, Texas, 76165',
    'Aarav Anderson',
    'Aarav Lee',
    'Aarav Santos',
    'aarav.santos8320@example.com',
    'aarav.santos8321@example.com',
    'aarav_santos_2259',
    'Amelia',
    'Amelia Gonzalez',
    'amelia.gonzalez4271@example.com',
    'amelia.silva7872@example.com',
    'Ava Nguyen',
    'Chen Johnson',
    'Chen Silva',
    'Chen Smith',
    'Daiki Johnson',
    'Daiki Sanchez',
    'Daiki Silva',
    'daiki.silva6295@example.com',
    'daiki_johnson_9523',
    'daikisanchez1479@example.com',
    'Emma Kovacs',
    'Emma Smith',
    'emma.kovacs2974@example.com',
    'emma_smith_8564',
    'Ethan Garcia',
    'Fatima Johnson',
    'Fatima Taylor',
    'Fatima Wilson',
    'fatima.wilson5721@example.com',
    'fatima_taylor_3452',
    'Isabella Johansson',
    'Isabella Lopez',
    'isabella.lopez3271@example.com',
    'Ivan Hernandez',
    'Ivan Johnson',
    'Ivan Khan',
    'ivan_hernandez_6923',
    'James Kim',
    'James Kovacs',
    'James Sanchez',
    'james.kim1995@example.com',
    'Lei Li',
    'Lei Wilson',
    'Liam Moore',
    'Liam Thomas',
    'liam.moore6985@example.com',
    'Lucas Brown',
    'Lucas Santos',
    'lucas.brown9344@example.com',
    'lucas_santos_6600',
    'Mei Ahmed',
    'Mei Davis',
    'Mei Kovacs',
    'Mei Patel',
    'mei_kovacs_8020',
    'mei_patel_7272',
    'Mia Garcia',
    'mia.garcia2723@example.com',
    'mia_garcia_4516',
    'Noah Ito',
    'Noah Patel',
    'noah_ito_3850',
    'NoNo',
    'Olivia Lopez',
    'Raj Santos',
    'silva7872@example.com',
    'Sofia Hernandez',
    'Sofia Li',
    'Sofia Thomas',
    'sofia.thomas3019@example.com',
    'sofia.thomas3069@example.com',
    'Sophia Martin',
    'sophia.martin4832@example.com',
    'Suite 394',
    'Suite 641',
    'Yara Ito',
    'Yara Muller',
    'Yara Silva',
    'yara.muller9246@example.com',
    'Yusuf Gonzalez',
    'Yusuf Hernandez',
    'Yusuf Li',
    'Yusuf Rossi',
    'Yusuf Taylor',
    'yusuf.hernandez8836@example.com',
    '#W2378156',
    '#W2702727',
    '#W4316152',
    '#W5056519',
    '#W5995614',
    '#W6908222',
    '#W7209932',
    '#W8268610',
    '#W8277957',
    '#W8665881',
    '#W8855135',
    '#W9300146',
    'Air Purifier',
    'Backpack',
    'Bicycle',
    'Bluetooth Speaker',
    'Bookshelf',
    'Desk Lamp',
    'Digital Camera',
    'E-Reader',
    'Fleece Jacket',
    'Garden Hose',
    'Grill',
    'Hiking Boots',
    'Jigsaw Puzzle',
    'Laptop',
    'Luggage Set',
    'Makeup Kit',
    'Mechanical Keyboard',
    'Office Chair',
    'Perfume',
    'Pet Bed',
    'Running Shoes',
    'Skateboard',
    'Smart Thermostat',
    'Smart Watch',
    'Sneakers',
    'T-Shirt',
    'Tablet',
    'Tea Kettle',
    'Vacuum Cleaner',
    'Water Bottle',
    'Wireless Earbuds',
]

AIRLINE_HOT_WORDS = [
    'Aarav Ahmed',
    'Aarav Garcia',
    'aarav_ahmed_6699',
    'aarav_garcia_1177',
    'Amelia Davis',
    'Amelia Rossi',
    'Amelia Sanchez',
    'amelia_davis_8890',
    'amelia_rossi_1297',
    'amelia_sanchez_4739',
    'Anya Garcia',
    'anya_garcia_5901',
    'ATL',
    'certificate_9984806',
    'Chen Lee',
    'chen_lee_6825',
    'CLT',
    'Daiki Lee',
    'Daiki Muller',
    'daiki_lee_6144',
    'daiki_muller_1116',
    'DEN',
    'DTW',
    'EHGLP3',
    'Emma Kim',
    'emma_kim_9957',
    'Ethan',
    'Ethan Martin',
    'ethan_martin_2396',
    'EUJUY6',
    'EWR',
    'GV1N64',
    'H8Q05L',
    'H9ZU1C',
    'HAT033',
    'HAT039',
    'HAT045',
    'HAT169',
    'HAT266',
    'HXDUBJ',
    'IAH',
    'IFOYYZ',
    'Ivan Muller',
    'Ivan Rossi',
    'ivan_muller_7015',
    'ivan_rossi_8555',
    'James Lee',
    'James Patel',
    'James Taylor',
    'james_lee_6136',
    'james_patel_9828',
    'james_taylor_7043',
    'JFK',
    'JMO1MG',
    'Kevin Smith',
    'LAS',
    'LAX',
    'LGA',
    'Liam',
    'Liam Khan',
    'liam_khan_2521',
    'Lucas Brown',
    'lucas_brown_4047',
    'M20IZO',
    'MCO',
    'Mei Brown',
    'Mei Lee',
    'mei_brown_7075',
    'Mia Kim',
    'Mia Li',
    'mia_kim_4397',
    'mia_li_3668',
    'Mohamed Hernandez',
    'Mohamed Silva',
    'mohamed_hernandez_5188',
    'mohamed_silva_9265',
    'Noah',
    'Noah Muller',
    'noah_muller_9847',
    'NQNU5R',
    'Olivia Gonzalez',
    'olivia_gonzalez_2305',
    'Omar Davis',
    'Omar Rossi',
    'omar_davis_3817',
    'omar_rossi_1241',
    'ORD',
    'PEP4E0',
    'PHL',
    'PHX',
    'Raj',
    'Raj Brown',
    'Raj Sanchez',
    'raj_brown_5782',
    'raj_sanchez_7340',
    'SEA',
    'SFO',
    'SI5UKW',
    'Sofia Kim',
    'sofia_kim_7287',
    'Sophia',
    'Sophia Martin',
    'Sophia Silva',
    'Sophia Taylor',
    'sophia_martin_4574',
    'sophia_silva_7557',
    'sophia_taylor_9065',
    'VA5SGQ',
    'XEHM4B',
    'XEWRD9',
    'Yara Garcia',
    'yara_garcia_1905',
    'YAX4DR',
    '1N99U6',
    '3RK2T9',
    '59XX6W',
    'Ivan Smith',
    'Mei Garcia',
]

TELECOM_HOT_WORDS = [
    '192.168.1.1',
    '555-123-2002',
    'B1234321',
    'broken',
    'http://mms.carrier.com/mms/wapenc',
    'John Smith',
]

HOT_WORDS_BY_DOMAIN = {
    "retail": RETAIL_HOT_WORDS,
    "airline": AIRLINE_HOT_WORDS,
    "telecom": TELECOM_HOT_WORDS,
}
ALL_HOT_WORDS = RETAIL_HOT_WORDS + AIRLINE_HOT_WORDS + TELECOM_HOT_WORDS

# These markers come from the domain policies embedded verbatim in tau2-bench's
# full-duplex system prompt. The role sentence is a fallback in case Markdown
# headings are stripped by a provider or a future prompt adapter.
_DOMAIN_PROMPT_MARKERS = {
    "retail": ("# retail agent policy", "as a retail agent"),
    "airline": ("# airline agent policy", "as an airline agent"),
    "telecom": ("# telecom agent policy", "as a telecom agent"),
}
QWEN3_ASR_SYSTEM_PROMPT = ""
QWEN3_OMNI_SYSTEM_PROMPT = "transcribe the given audio into text."
QWEN3_OMNI_USER_INSTRUCTION = (
    "transcribe the audio into text, only transcribe the target user content "
    "and ignore background content, Domain-specific terms are optional spelling "
    "hints, not a whitelist. Preserve the spoken identifier and digits; do not "
    "replace them with a different listed term."
)
_LLM_HOTWORD_CONTEXT_PREFIX = (
    "Voice transcription normalization for tool arguments:\n"
    "- The user's text was produced by speech recognition and may contain "
    "phonetic or spelling errors in identifiers and proper names.\n"
    "- Only when preparing a tool argument, compare a spoken name, ID, username, "
    "email, phone number, address, ZIP code, airport code, or product term with "
    "the canonical candidates below.\n"
    "- If exactly one candidate is a strong phonetic or orthographic match and "
    "fits the tool parameter, use its exact canonical spelling.\n"
    "- Never introduce a candidate the user did not attempt to say. Never replace "
    "ordinary conversational text. If the match is weak or ambiguous, ask the "
    "user to clarify instead of guessing or calling the tool, but do not tell "
    "the user about the canonical candidates.\n"
    "Canonical candidates for the {domain} domain: {candidates}"
)
_ASR_TEXT_MARKER = re.compile(r"<asr_text>", re.IGNORECASE)
_ASR_SPECIAL_TOKEN = re.compile(r"<\|(?:im_end|endoftext)\|>")


def parse_asr_transcript(raw: str) -> str:
    """Extract transcription text from Qwen3-ASR raw or text-only output."""
    value = raw.strip()
    marker = _ASR_TEXT_MARKER.search(value)
    if marker:
        value = value[marker.end() :]
    value = _ASR_SPECIAL_TOKEN.sub("", value)
    return value.strip()


def resolve_asr_prompt_mode(configured_mode: str, model: str) -> str:
    """Resolve the message shape used by Qwen3-ASR or Qwen3-Omni."""
    mode = configured_mode.strip().lower().replace("-", "_")
    if mode == "auto":
        normalized_model = model.strip().lower().replace("_", "-")
        return "qwen3_asr" if "qwen3-asr" in normalized_model else "qwen3_omni"
    if mode not in {"qwen3_asr", "qwen3_omni"}:
        raise ValueError("ASR_PROMPT_MODE must be auto, qwen3_asr, or qwen3_omni")
    return mode


def detect_hotword_domain(system_prompt: str) -> str | None:
    """Identify a Tau2 domain from its full-duplex agent system prompt."""
    normalized_prompt = " ".join(system_prompt.lower().split())
    matches = [
        domain
        for domain, markers in _DOMAIN_PROMPT_MARKERS.items()
        if any(marker in normalized_prompt for marker in markers)
    ]
    return matches[0] if len(matches) == 1 else None


def llm_hotword_context(domain: str | None) -> str:
    hotwords = HOT_WORDS_BY_DOMAIN.get(domain or "", ALL_HOT_WORDS)
    return _LLM_HOTWORD_CONTEXT_PREFIX.format(
        domain=domain or "all", candidates=", ".join(hotwords)
    )


def qwen3_omni_system_prompt(domain: str | None) -> str:
    hotwords = HOT_WORDS_BY_DOMAIN.get(domain or "", ALL_HOT_WORDS)
    return (
        f"{QWEN3_OMNI_SYSTEM_PROMPT} Here are some domain-specific terms "
        f"that might appear: {', '.join(hotwords)}"
    )


def default_asr_context(prompt_mode: str, domain: str | None = None) -> str:
    if prompt_mode == "qwen3_asr":
        return QWEN3_ASR_SYSTEM_PROMPT
    return qwen3_omni_system_prompt(domain)


def telecom_text_prompt_mode(domain: str | None, configured_mode: str) -> str:
    """Resolve the telecom text-LLM experiment without affecting other domains."""
    if domain != "telecom":
        return "full"
    mode = configured_mode.strip().lower()
    if mode not in {"full", "suffix_only", "none"}:
        raise ValueError("TELECOM_TEXT_PROMPT_MODE must be full, suffix_only, or none")
    return mode


def build_asr_messages(
    pcm: bytes, context: str, prompt_mode: str = "qwen3_asr"
) -> list[dict[str, Any]]:
    """Build model-specific ASR messages."""
    messages: list[dict[str, Any]] = []
    if context:
        messages.append({"role": "system", "content": context})
    user_content = [audio_message(pcm)]
    if prompt_mode == "qwen3_omni":
        user_content.append({"type": "text", "text": QWEN3_OMNI_USER_INSTRUCTION})
    messages.append({"role": "user", "content": user_content})
    return messages


# Keep these strings byte-for-byte aligned with the ms-swift training messages.
INTERRUPT_SYSTEM_PROMPT = (
    "You are a speaking-state interruption detector. The assistant is currently "
    "speaking. Classify only the current audio window using the preceding user "
    "audio as voice context. Ignore environmental noise, third-party conversations, "
    "and brief acknowledgements that do not request the floor. interrupt = an "
    "audible stop command or a target user taking the floor; ignorable = no target "
    "interaction or only a brief acknowledgement without requesting the floor. "
    "Reply with exactly one of: ignorable, interrupt."
)
INTERRUPT_QUERY = (
    "stage=speaking; classify the current audio window.\n"
)
COMPLETE_SYSTEM_PROMPT = (
    "You are an idle-state semantic completion detector. The assistant has "
    "finished speaking or has been interrupted and is already idle. Classify "
    "only the accumulated current audio: ignorable = no target user interaction "
    "yet; incomplete = the target user started but has not finished, including "
    "pauses with background speech; complete = a complete target user intent. "
    "A target user may change topics. Ignore environmental noise and non-target "
    "conversations. Reply with exactly one of: ignorable, incomplete, complete."
)
COMPLETE_QUERY = (
    "stage=idle; classify the current accumulated audio."
)
THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"


class ResponseInterrupted(Exception):
    """The current response lost the floor before all audio was played."""


def _event(event_type: str, **fields: Any) -> dict[str, Any]:
    return {"event_id": f"evt_{uuid.uuid4().hex}", "type": event_type, **fields}


def _error_event(code: str, message: str) -> dict[str, Any]:
    return _event(
        "error",
        error={"type": "server_error", "code": code, "message": message},
    )


def _visible_text(part: str, state: str, buffer: str) -> tuple[str, str, str]:
    """Hide a leading model reasoning block, including split stream chunks."""
    if state == "visible":
        return part, state, buffer

    buffer += part
    if state == "pending":
        if THINK_OPEN.startswith(buffer) and len(buffer) < len(THINK_OPEN):
            return "", state, buffer
        if buffer.startswith(THINK_OPEN):
            state = "thinking"
            buffer = buffer[len(THINK_OPEN) :]
        else:
            return buffer, "visible", ""

    close_index = buffer.find(THINK_CLOSE)
    if close_index < 0:
        return "", state, buffer
    visible = buffer[close_index + len(THINK_CLOSE) :].lstrip()
    return visible, "visible", ""


class RealtimeSession:
    def __init__(self, send: Send, settings: Settings):
        self._send = send
        self.debug_enabled = False
        self.debug_paused = False
        self.debug_audio_on_demand = False
        self.debug_classifier_inputs: dict[str, list[dict[str, Any]]] = {}
        self.last_client_event_at = time.perf_counter()
        self.last_audio_received_at: float | None = None
        self.debug_paused_at: float | None = None
        self.debug_resume = asyncio.Event()
        self.debug_resume.set()
        self.settings = settings
        self.interactive = InteractiveBackend(settings)
        self.text = TextBackend(settings)
        self.tts = TTSBackend(settings)
        self.vad = VoiceDetector()
        self.base_instructions = "You are a helpful assistant."
        self.instructions = self.base_instructions
        self.hotword_domain: str | None = None
        self.tools: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []
        self.interaction_history: list[dict[str, Any]] = []
        self.input_ulaw = bytearray()
        self.input_pcm = bytearray()
        self.ignorable_pcm = bytearray()
        self.tick_audio = bytearray()
        self.interrupt_window_ulaw = bytearray()
        self.pre_roll_ulaw = bytearray()
        self.input_resample_state = None
        self.speech_seen = False
        self.speech_start_ms: int | None = None
        self.input_generation = 0
        self.classification_running = False
        self.classifier_task: asyncio.Task[None] | None = None
        self.user_commit_task: asyncio.Task[None] | None = None
        self.consecutive_complete_count = 0
        self.semantic_turn_started = False
        self.consecutive_interrupt_count = 0
        self.interrupt_confirmed = False
        self.interrupt_negative_count = 0
        self.interrupt_votes: list[bool] = []
        self.interrupt_classification_attempts = 0
        self.candidate_speech_ms = 0
        self.candidate_silence_ms = 0
        self.assistant_active = False
        self.assistant_speaking = False
        self.candidate_started_during_response = False
        self.response_task: asyncio.Task[None] | None = None
        self.current_item_id: str | None = None
        self.current_clause_text = ""
        self.current_clause_total = 0
        self.current_clause_sent = 0
        self.current_assistant_text = ""
        self.spoken_assistant_text = ""
        self.current_assistant_remembered = False
        self.greeting_sent = False
        self.tts_interrupted = False
        self.interrupt_ack_pending = False
        self.next_response_ack = ""
        self.tool_ack_spoken = False
        self.tts_task: asyncio.Task[None] | None = None
        self.pending_user_turns: list[tuple[dict[str, Any], dict[str, Any], str]] = []
        self.closed = False
        self.user_turn_processing = False
        self.tool_continuation_pending = False
        self.pending_tool_calls: dict[str, dict[str, Any]] = {}
        self.session_id = f"sess_{uuid.uuid4().hex}"
        self.classifier_debug = ClassifierDebugRecorder(
            settings.interactive_debug_dir, self.session_id
        )
        self.asr_debug = ASRDebugRecorder(settings.asr_debug_dir, self.session_id)
        self.received_audio_bytes = 0
        self.input_audio_clock = asyncio.Condition()
        self.tts_playback_allowed = asyncio.Event()
        self.tts_playback_allowed.set()
        self.diagnostic_audio = bytearray()
        self.diagnostic_speech_packets = 0

    async def send(self, event: dict[str, Any]) -> None:
        # Keep debug reports available for inspection while ordinary output is
        # frozen. The browser opts in; other realtime clients receive no extras.
        if not event["type"].startswith("debug."):
            await self.debug_resume.wait()
        await self._send(event)

    async def start(self) -> None:
        await self.send(
            _event(
                "session.created",
                session={
                    "id": self.session_id,
                    "object": "realtime.session",
                },
            )
        )
        logger.info(
            "session=%s created interrupt_confirmations=%d "
            "interrupt_min_speech_ms=%d interrupt_window_ms=%d "
            "force_interrupt=disabled tts_pacing=%s classifier_window_ms=%d",
            self.session_id,
            self.settings.interrupt_confirmations,
            self.settings.interrupt_min_speech_ms,
            self.settings.interrupt_window_ms,
            ("input_audio_clock" if self.settings.tts_realtime_pacing else "disabled"),
            self._classifier_window_bytes() * 1000 // 8000,
        )

    async def close(self) -> None:
        self.closed = True
        self.debug_classifier_inputs.clear()
        self.debug_resume.set()
        self.tts_playback_allowed.set()
        async with self.input_audio_clock:
            self.input_audio_clock.notify_all()
        tasks = [
            task
            for task in (
                self.response_task,
                self.classifier_task,
                self.user_commit_task,
            )
            if task and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def handle(self, message: dict[str, Any]) -> None:
        self.last_client_event_at = time.perf_counter()
        event_type = message.get("type")
        if event_type == "debug.ping" and self.debug_enabled:
            await self.send(_event(
                "debug.pong", client_time_ms=message.get("client_time_ms"),
                audio_clock_ms=self.received_audio_bytes * 1000 // 8000,
            ))
            return
        if event_type == "debug.classifier.inspect" and self.debug_enabled:
            request_id = message.get("request_id")
            messages = self.debug_classifier_inputs.get(request_id)
            await self.send(_event(
                "debug.classifier.input", request_id=request_id,
                messages=messages, error=None if messages is not None else "Input expired; only the latest 20 requests are retained",
            ))
            return
        if event_type in {"debug.pause", "debug.resume"} and self.debug_enabled:
            self.debug_paused = event_type == "debug.pause"
            now = time.perf_counter()
            logger.info(
                "session=%s debug %s audio_clock_ms=%d pause_duration_ms=%.1f",
                self.session_id, "paused" if self.debug_paused else "resumed",
                self.received_audio_bytes * 1000 // 8000,
                (now - self.debug_paused_at) * 1000 if self.debug_paused_at is not None else 0,
            )
            self.debug_paused_at = now if self.debug_paused else None
            if self.debug_paused:
                self.debug_resume.clear()
            else:
                self.debug_resume.set()
            await self.send(_event(
                "debug.pause.updated", paused=self.debug_paused,
                audio_clock_ms=self.received_audio_bytes * 1000 // 8000,
            ))
            if self.debug_paused:
                # Large buffers are sent once on inspection, not on every vote.
                await self.send(_event(
                    "debug.pause.snapshot",
                    audio_clock_ms=self.received_audio_bytes * 1000 // 8000,
                    buffers={
                        "candidate": audio_message(bytes(self.input_pcm)),
                        "ignorable": audio_message(bytes(self.ignorable_pcm)),
                        "interrupt": audio_message(
                            ulaw_to_pcm16(self._interrupt_audio_window())[0]
                        ),
                    },
                ))
            if not self.debug_paused:
                self._schedule_classification()
                self._start_deferred_response()
            return
        if self.debug_paused:
            # Packets already queued in the socket must not advance the clock
            # or change the buffered turn during inspection.
            return
        if event_type == "session.update":
            await self._session_update(message.get("session", {}))
        elif event_type == "input_audio_buffer.append":
            await self._append_audio(message.get("audio", ""))
        elif event_type == "input_audio_buffer.commit":
            self._schedule_user_commit()
        elif event_type == "input_audio_buffer.clear":
            self._clear_input()
            await self.send(_event("input_audio_buffer.cleared"))
            self._start_deferred_response()
        elif event_type == "conversation.item.create":
            await self._conversation_item(message.get("item", {}))
        elif event_type == "conversation.item.truncate":
            self._truncate(int(message.get("audio_end_ms", 0)))
            await self.send(
                _event(
                    "conversation.item.truncated",
                    item_id=message.get("item_id"),
                    audio_end_ms=message.get("audio_end_ms", 0),
                )
            )
        elif event_type == "response.create":
            if self.pending_tool_calls:
                logger.info(
                    "session=%s deferring response.create pending_tools=%d",
                    self.session_id,
                    len(self.pending_tool_calls),
                )
            elif self.history and self.history[-1].get("role") == "tool":
                self.tool_continuation_pending = True
                self._start_deferred_response()
            else:
                self._schedule_user_commit()

    async def _session_update(self, session: dict[str, Any]) -> None:
        self.debug_enabled = session.get("debug", self.debug_enabled) is True
        self.debug_audio_on_demand = session.get("debug_audio_mode") == "on_demand"
        self.base_instructions = session.get("instructions") or self.base_instructions
        self.hotword_domain = detect_hotword_domain(self.base_instructions)
        if self.hotword_domain is None:
            logger.warning(
                "session=%s could not identify a unique hotword domain; "
                "using all hotwords=%d",
                self.session_id,
                len(ALL_HOT_WORDS),
            )
        text_prompt_mode = telecom_text_prompt_mode(
            self.hotword_domain, self.settings.telecom_text_prompt_mode
        )
        self.instructions = self.base_instructions
        supplements = []
        if text_prompt_mode == "full":
            supplements.append(llm_hotword_context(self.hotword_domain))
        if text_prompt_mode != "none":
            supplements.append(self.settings.server_instruction_suffix)
        for supplement in supplements:
            if supplement and supplement not in self.instructions:
                self.instructions = f"{self.instructions.rstrip()}\n\n{supplement}"
        play_greeting = not self.greeting_sent
        self.history = [{"role": "system", "content": self.instructions}]
        self.interaction_history.clear()
        self.ignorable_pcm.clear()
        if self.settings.greeting.strip():
            self.greeting_sent = True
        raw_tools = session.get("tools", [])
        self.tools = []
        for tool in raw_tools:
            if tool.get("type") != "function":
                continue
            function = tool.get("function", tool)
            self.tools.append(
                {
                    "name": function.get("name", ""),
                    "description": function.get("description", ""),
                    "parameters": function.get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                }
            )
        await self.send(
            _event(
                "session.updated", session={"id": "local", "object": "realtime.session"}
            )
        )
        if play_greeting and self.greeting_sent:
            self.response_task = asyncio.create_task(
                self._play_greeting(self.settings.greeting.strip())
            )
        logger.info(
            "session=%s configured tools=%d hotword_domain=%s hotwords=%d "
            "telecom_text_prompt_mode=%s",
            self.session_id,
            len(self.tools),
            self.hotword_domain or "none",
            (
                len(HOT_WORDS_BY_DOMAIN.get(self.hotword_domain or "", ALL_HOT_WORDS))
                if text_prompt_mode == "full"
                else 0
            ),
            text_prompt_mode,
        )

    async def _play_greeting(self, greeting: str) -> None:
        if not greeting:
            return
        item_id = f"item_{uuid.uuid4().hex}"
        self.assistant_active = True
        self._reset_interrupt_timeline()
        self.tts_interrupted = False
        self.current_item_id = item_id
        self.current_assistant_text = greeting
        self.spoken_assistant_text = ""
        self.current_clause_text = ""
        self.current_clause_total = 0
        self.current_clause_sent = 0
        self.current_assistant_remembered = False
        speech_task: asyncio.Task[None] | None = None
        response_finished = False
        try:
            await self.send(
                _event(
                    "response.created",
                    response={
                        "id": f"resp_{uuid.uuid4().hex}",
                        "status": "in_progress",
                    },
                )
            )
            await self.send(
                _event("response.text.delta", delta=greeting, item_id=item_id)
            )
            speech_task = asyncio.create_task(self._speak(greeting))
            self.tts_task = speech_task
            await self._wait_for_tts(speech_task)
            await self.send(_event("response.output_audio.done", item_id=item_id))
            await self.send(
                _event("response.text.done", text=greeting, item_id=item_id)
            )
            await self.send(
                _event(
                    "response.done",
                    response={
                        "id": f"resp_{uuid.uuid4().hex}",
                        "status": "completed",
                    },
                )
            )
            self.history.append({"role": "assistant", "content": greeting})
            self._finish_assistant_interaction_turn()
            response_finished = True
            logger.info("session=%s greeting playback completed", self.session_id)
        except ResponseInterrupted:
            if speech_task and not speech_task.done():
                speech_task.cancel()
                await asyncio.gather(speech_task, return_exceptions=True)
            self._remember_interrupted_assistant_turn()
            await self._send_response_cancelled()
            response_finished = True
        except asyncio.CancelledError:
            if speech_task and not speech_task.done():
                speech_task.cancel()
                await asyncio.gather(speech_task, return_exceptions=True)
            self._remember_interrupted_assistant_turn()
            await self._send_response_cancelled()
            response_finished = True
            raise
        except Exception as exc:
            logger.exception("session=%s greeting TTS failed", self.session_id)
            await self.send(_error_event("greeting_tts_error", str(exc)))
        finally:
            self.assistant_active = False
            self.assistant_speaking = False
            self.tts_interrupted = False
            self.tts_task = None
            self.current_item_id = None
            if response_finished and not self.closed:
                self._start_next_pending_user_turn()

    async def _append_audio(self, encoded: str) -> None:
        if self.debug_paused or not encoded:
            return
        try:
            audio = base64.b64decode(encoded)
        except (ValueError, TypeError) as exc:
            await self.send(_error_event("invalid_audio", str(exc)))
            return
        await self._advance_input_audio_clock(len(audio))
        self.last_audio_received_at = time.perf_counter()
        if self.received_audio_bytes == len(audio):
            logger.info(
                "session=%s received first audio packet bytes=%d",
                self.session_id,
                len(audio),
            )
        packet_bytes = len(audio)
        has_speech = self.vad.is_speech(audio)
        self._append_interrupt_timeline(audio)
        self.diagnostic_audio.extend(audio)
        if has_speech:
            self.diagnostic_speech_packets += 1
        diagnostic_bytes = 8000 * 5
        if len(self.diagnostic_audio) >= diagnostic_bytes:
            sample = bytes(self.diagnostic_audio)
            logger.info(
                "session=%s audio diagnostic seconds=%.1f rms=%d unique_bytes=%d "
                "vad_speech_packets=%d",
                self.session_id,
                len(sample) / 8000,
                ulaw_rms(sample),
                len(set(sample)),
                self.diagnostic_speech_packets,
            )
            self.diagnostic_audio.clear()
            self.diagnostic_speech_packets = 0
        # Do not let pre-turn silence fill the user buffer while the simulator
        # is waiting on its LLM or ElevenLabs. Keep only a small pre-roll so the
        # first phoneme is not lost at the VAD boundary.
        if not self.speech_seen and not has_speech:
            self._append_pre_roll(audio)
            return
        if self.assistant_speaking and not self.settings.interrupt_enabled:
            self._clear_input()
            return
        if has_speech:
            first_speech = not self.speech_seen
            self.speech_seen = True
            if first_speech:
                logger.info("session=%s detected speech", self.session_id)
                self.candidate_started_during_response = self.assistant_active
                self.speech_start_ms = (
                    (self.received_audio_bytes - packet_bytes) * 1000 // 8000
                )
                audio = bytes(self.pre_roll_ulaw) + audio
                self.pre_roll_ulaw.clear()
            self.candidate_speech_ms += packet_bytes * 1000 // 8000
            self.candidate_silence_ms = 0
        else:
            self.candidate_silence_ms += packet_bytes * 1000 // 8000
        max_bytes = self.settings.max_user_audio_s * 8000
        if len(self.input_ulaw) >= max_bytes:
            if not self.assistant_speaking:
                self._schedule_user_commit()
            return
        audio = audio[: max_bytes - len(self.input_ulaw)]
        self.input_ulaw.extend(audio)
        pcm, self.input_resample_state = ulaw_to_pcm16(audio, self.input_resample_state)
        self.input_pcm.extend(pcm)
        self.tick_audio.extend(audio)
        if len(self.tick_audio) >= self._classifier_window_bytes():
            self._schedule_classification()
        if (
            self.speech_seen
            and not self.assistant_speaking
            and self.semantic_turn_started
            and self.candidate_silence_ms >= self.settings.silence_ms
        ):
            self._schedule_user_commit()

    def _classifier_window_bytes(self) -> int:
        return max(
            1,
            int(
                8000
                * self.settings.tick_ms
                * self.settings.classifier_window_ticks
                / 1000
            ),
        )

    def _schedule_classification(self) -> None:
        interrupt = (
            self.assistant_active
            and self.settings.interrupt_enabled
            and not self.interrupt_confirmed
        )
        if (
            self.closed
            or self.debug_paused
            or self.user_turn_processing
            or (self.user_commit_task and not self.user_commit_task.done())
            or not self.speech_seen
            or (self.assistant_active and not interrupt)
            or (
                interrupt
                and self.candidate_speech_ms < self.settings.interrupt_min_speech_ms
            )
            or len(self.tick_audio) < self._classifier_window_bytes()
            or (self.classifier_task and not self.classifier_task.done())
        ):
            return
        if interrupt:
            window_ulaw = self._interrupt_audio_window()
            classifier_pcm, _ = ulaw_to_pcm16(window_ulaw)
        else:
            classifier_pcm = bytes(self.input_pcm)
        if not classifier_pcm:
            logger.warning(
                "session=%s skipped classification with empty audio snapshot",
                self.session_id,
            )
            return
        self.tick_audio.clear()
        self.classification_running = True
        self.classifier_task = asyncio.create_task(
            self._classify_window(
                self.input_generation,
                self.received_audio_bytes,
                interrupt,
                classifier_pcm,
                self._classifier_messages(classifier_pcm, interrupt=interrupt),
                len(self.input_ulaw) * 1000 // 8000,
                self.candidate_speech_ms,
            )
        )

    async def _classify_window(
        self,
        generation: int,
        scheduled_audio_clock: int,
        interrupt: bool,
        classifier_pcm: bytes,
        messages: list[dict[str, Any]],
        candidate_audio_ms: int,
        candidate_speech_ms: int,
    ) -> None:
        started_at = time.perf_counter()
        request_id = f"cls_{uuid.uuid4().hex}"
        kind = "interrupt" if interrupt else "semantic_complete"
        choices = (
            ["interrupt", "backchannel", "ignorable"]
            if interrupt
            else (
                ["complete", "incomplete"]
                if self.semantic_turn_started
                else ["ignorable", "incomplete", "complete"]
            )
        )
        status = "error"
        label = None
        error = None
        try:
            if self.debug_enabled:
                if self.debug_audio_on_demand:
                    self.debug_classifier_inputs[request_id] = messages
                    while len(self.debug_classifier_inputs) > 20:
                        del self.debug_classifier_inputs[next(iter(self.debug_classifier_inputs))]
                await self.send(_event(
                    "debug.classifier.started",
                    request_id=request_id,
                    kind=kind,
                    model=self.settings.interactive_model,
                    messages=None if self.debug_audio_on_demand else messages,
                    choices=choices,
                    audio_clock_ms=scheduled_audio_clock * 1000 // 8000,
                    audio_ms=len(classifier_pcm) * 1000 // 32000,
                    candidate_audio_ms=candidate_audio_ms,
                    candidate_speech_ms=candidate_speech_ms,
                    input_generation=generation,
                    buffer_audio_ms={
                        "candidate": len(self.input_pcm) * 1000 // 32000,
                        "ignorable": len(self.ignorable_pcm) * 1000 // 32000,
                    },
                ))
            if interrupt:
                logger.info(
                    "session=%s requesting interrupt classification "
                    "audio_ms=%d candidate_audio_ms=%d candidate_speech_ms=%d",
                    self.session_id,
                    len(classifier_pcm) * 1000 // 32000,
                    candidate_audio_ms,
                    candidate_speech_ms,
                )
                self.interrupt_classification_attempts += 1
                label = await self.interactive.classify(
                    messages,
                    choices,
                )
                logger.info(
                    "session=%s interrupt classification=%s latency_ms=%.1f "
                    "input_advanced_ms=%d",
                    self.session_id,
                    label,
                    (time.perf_counter() - started_at) * 1000,
                    (self.received_audio_bytes - scheduled_audio_clock) * 1000 // 8000,
                )
                await self.classifier_debug.save(classifier_pcm, "interrupt", label)
                await self._debug_classifier_prediction(request_id, label, started_at)
                await self.debug_resume.wait()
                if generation != self.input_generation or not self.speech_seen:
                    status = "discarded_stale"
                    logger.info(
                        "session=%s discarding stale interrupt classification",
                        self.session_id,
                    )
                    return
                if not self.assistant_active:
                    status = "discarded_assistant_idle"
                    logger.info(
                        "session=%s discarding interrupt classification after "
                        "assistant became idle",
                        self.session_id,
                    )
                    return
                await self._apply_interrupt_classification(label)
                status = "applied"
            else:
                logger.info(
                    "session=%s requesting completeness classification audio_ms=%d",
                    self.session_id,
                    len(classifier_pcm) * 1000 // 32000,
                )
                label = await self.interactive.classify(
                    messages,
                    choices,
                )
                logger.info(
                    "session=%s completeness classification=%s latency_ms=%.1f "
                    "input_advanced_ms=%d",
                    self.session_id,
                    label,
                    (time.perf_counter() - started_at) * 1000,
                    (self.received_audio_bytes - scheduled_audio_clock) * 1000 // 8000,
                )
                await self.classifier_debug.save(classifier_pcm, "semantic", label)
                await self._debug_classifier_prediction(request_id, label, started_at)
                await self.debug_resume.wait()
                if generation != self.input_generation or not self.speech_seen:
                    status = "discarded_stale"
                    logger.info(
                        "session=%s discarding stale completeness classification",
                        self.session_id,
                    )
                    return
                if self.assistant_active:
                    status = "discarded_assistant_active"
                    logger.info(
                        "session=%s discarding completeness classification after "
                        "assistant became active",
                        self.session_id,
                    )
                    return
                self._apply_completeness_classification(
                    label,
                    classified_pcm_bytes=len(classifier_pcm),
                    classified_audio_ms=candidate_audio_ms,
                )
                status = "applied"
        except Exception as exc:
            error = str(exc)
            if self.debug_enabled:
                await self.send(_event(
                    "debug.classifier.failed", request_id=request_id,
                    error=error,
                ))
            await self.debug_resume.wait()
            # Classifier failures are handled by the silence timeout path.
            self.consecutive_interrupt_count = 0
            self.interrupt_votes.clear()
            self.tts_playback_allowed.set()
            if not self.assistant_active:
                self.consecutive_complete_count = 0
            logger.exception("session=%s classifier failed", self.session_id)
            await self.send(_error_event("classifier_error", str(exc)))
        finally:
            if self.debug_enabled and not self.closed:
                await self.send(_event(
                    "debug.classifier.finished", request_id=request_id,
                    label=label, status=status, error=error,
                    state={
                        "complete_count": self.consecutive_complete_count,
                        "interrupt_votes": list(self.interrupt_votes),
                        "semantic_turn_started": self.semantic_turn_started,
                        "candidate_audio_ms": len(self.input_pcm) * 1000 // 32000,
                        "ignorable_audio_ms": len(self.ignorable_pcm) * 1000 // 32000,
                    },
                ))
            self.classification_running = False
            self.classifier_task = None
            self._schedule_classification()
            self._start_deferred_response()

    async def _debug_classifier_prediction(
        self, request_id: str, label: str, started_at: float
    ) -> None:
        if self.debug_enabled:
            await self.send(_event(
                "debug.classifier.predicted", request_id=request_id, label=label,
                latency_ms=round((time.perf_counter() - started_at) * 1000, 1),
                awaiting_resume=self.debug_paused,
            ))

    def _interrupt_window_bytes(self) -> int:
        return max(1, self.settings.interrupt_window_ms * 8000 // 1000)

    def _interrupt_audio_window(self) -> bytes:
        window_bytes = self._interrupt_window_bytes()
        audio = bytes(self.interrupt_window_ulaw[-window_bytes:])
        if len(audio) < window_bytes:
            audio = bytes([255]) * (window_bytes - len(audio)) + audio
        return audio

    def _reset_interrupt_timeline(self) -> None:
        window_bytes = self._interrupt_window_bytes()
        seed = bytes(self.input_ulaw[-window_bytes:])
        self.interrupt_window_ulaw[:] = bytes([255]) * (window_bytes - len(seed)) + seed
        self.interrupt_votes.clear()
        self.consecutive_interrupt_count = 0
        self.interrupt_negative_count = 0

    def _append_interrupt_timeline(self, audio: bytes) -> None:
        if not self.assistant_active or not self.settings.interrupt_enabled:
            return
        window_bytes = self._interrupt_window_bytes()
        self.interrupt_window_ulaw.extend(audio)
        if len(self.interrupt_window_ulaw) > window_bytes:
            del self.interrupt_window_ulaw[:-window_bytes]

    async def _apply_interrupt_classification(self, label: str) -> None:
        self.interrupt_votes.append(label == "interrupt")
        del self.interrupt_votes[:-3]
        positive_votes = sum(self.interrupt_votes)
        self.consecutive_interrupt_count = positive_votes
        if label == "interrupt":
            self.interrupt_negative_count = 0
        else:
            self.interrupt_negative_count += 1
        logger.info(
            "session=%s interrupt votes=%d/%d recent=%s negative=%d",
            self.session_id,
            positive_votes,
            self.settings.interrupt_confirmations,
            "".join("1" if vote else "0" for vote in self.interrupt_votes),
            self.interrupt_negative_count,
        )
        if positive_votes >= self.settings.interrupt_confirmations:
            await self._interrupt()
            return
        if positive_votes:
            if self.tts_playback_allowed.is_set():
                self.tts_playback_allowed.clear()
                logger.info(
                    "session=%s TTS playback gated while interrupt vote is pending",
                    self.session_id,
                )
            return
        if not self.tts_playback_allowed.is_set():
            self.tts_playback_allowed.set()
            logger.info(
                "session=%s TTS playback resumed after interrupt votes expired",
                self.session_id,
            )
        if (
            self.candidate_speech_ms >= self.settings.interrupt_window_ms
            and self.interrupt_negative_count
            >= self.settings.interrupt_negative_confirmations
        ):
            logger.info(
                "session=%s resetting rejected candidate while preserving "
                "rolling timeline candidate_speech_ms=%d",
                self.session_id,
                self.candidate_speech_ms,
            )
            self._discard_non_interrupt_candidate()

    def _append_pre_roll(self, audio: bytes) -> None:
        max_bytes = max(0, self.settings.vad_pre_roll_ms * 8000 // 1000)
        if max_bytes == 0:
            self.pre_roll_ulaw.clear()
            return
        self.pre_roll_ulaw.extend(audio)
        if len(self.pre_roll_ulaw) > max_bytes:
            del self.pre_roll_ulaw[:-max_bytes]

    def _discard_non_interrupt_candidate(self) -> None:
        pre_roll_bytes = max(0, self.settings.vad_pre_roll_ms * 8000 // 1000)
        trailing_audio = (
            bytes(self.input_ulaw[-pre_roll_bytes:]) if pre_roll_bytes else b""
        )
        self._clear_input()
        self.pre_roll_ulaw.extend(trailing_audio)
        self._start_deferred_response()

    def _apply_completeness_classification(
        self,
        label: str,
        classified_pcm_bytes: int | None = None,
        classified_audio_ms: int | None = None,
    ) -> None:
        """Advance the semantic-turn state from one classifier result."""
        if label == "ignorable" and not self.semantic_turn_started:
            prefix_pcm_bytes = min(
                len(self.input_pcm),
                len(self.input_pcm)
                if classified_pcm_bytes is None
                else classified_pcm_bytes,
            )
            retained_ms = prefix_pcm_bytes * 1000 // 32000
            self._remember_ignorable_audio(bytes(self.input_pcm[:prefix_pcm_bytes]))
            logger.info(
                "session=%s retained ignorable audio_ms=%d total_ms=%d",
                self.session_id,
                retained_ms,
                len(self.ignorable_pcm) * 1000 // 32000,
            )
            self._discard_ignorable_prefix(
                prefix_pcm_bytes,
                retained_ms if classified_audio_ms is None else classified_audio_ms,
            )
            self._start_deferred_response()
            return

        if label == "incomplete":
            self.semantic_turn_started = True
            self.consecutive_complete_count = 0
            if self.candidate_silence_ms >= self.settings.silence_ms:
                logger.info(
                    "session=%s forcing incomplete turn complete after silence_ms=%d",
                    self.session_id,
                    self.candidate_silence_ms,
                )
                self._schedule_user_commit()
            return

        if label != "complete":
            raise ValueError(f"unexpected semantic completeness label: {label!r}")

        self.semantic_turn_started = True
        self.consecutive_complete_count += 1
        logger.info(
            "session=%s completeness confirmations=%d/%d",
            self.session_id,
            self.consecutive_complete_count,
            self.settings.complete_confirmations,
        )
        if self.consecutive_complete_count >= self.settings.complete_confirmations:
            self._schedule_user_commit()

    def _discard_ignorable_prefix(self, pcm_bytes: int, audio_ms: int) -> None:
        """Remove only the audio covered by an ignorable classifier snapshot."""
        del self.input_pcm[:pcm_bytes]
        ulaw_bytes = min(len(self.input_ulaw), max(0, audio_ms * 8))
        del self.input_ulaw[:ulaw_bytes]
        self.candidate_speech_ms = max(0, self.candidate_speech_ms - audio_ms)
        if not self.input_pcm:
            self._clear_input()
            return
        self.input_generation += 1
        self.consecutive_complete_count = 0
        self.semantic_turn_started = False
        # Audio received while classification was in flight remains an active
        # candidate. tick_audio already contains exactly that newer suffix.
        self.speech_seen = True
        self.speech_start_ms = max(
            0,
            (self.received_audio_bytes - len(self.input_ulaw)) * 1000 // 8000,
        )

    def _remember_ignorable_audio(self, pcm: bytes) -> None:
        max_bytes = max(0, self.settings.ignorable_audio_max_s * 32000)
        if max_bytes == 0:
            self.ignorable_pcm.clear()
            return
        self.ignorable_pcm.extend(pcm)
        if len(self.ignorable_pcm) > max_bytes:
            del self.ignorable_pcm[:-max_bytes]

    def _classifier_messages(self, pcm: bytes, interrupt: bool) -> list[dict[str, Any]]:
        history = list(self.interaction_history)
        user_content: list[dict[str, Any]] = []
        if interrupt:
            prompt = INTERRUPT_SYSTEM_PROMPT
            query = INTERRUPT_QUERY
            # The active assistant turn is not in interaction_history yet. Its
            # preceding user audio and the current window therefore share one
            # user message, just as adjacent users are merged in ms-swift data.
            if history and history[-1]["role"] == "user":
                preceding_user = history.pop()
                user_content.extend(preceding_user["content"])
                query = "\n" + query
        else:
            prompt = COMPLETE_SYSTEM_PROMPT
            query = COMPLETE_QUERY
        user_content.extend(
            [{"type": "text", "text": query}, audio_message(pcm)]
        )
        return [
            {"role": "system", "content": prompt},
            *history,
            {
                "role": "user",
                "content": user_content,
            },
        ]

    def _remember_interaction(self, message: dict[str, Any]) -> None:
        history_limit = self.settings.interactive_history_messages
        if history_limit <= 0:
            return
        self.interaction_history.append(message)
        if len(self.interaction_history) > history_limit:
            del self.interaction_history[:-history_limit]

    def _finish_assistant_interaction_turn(self) -> None:
        if self.current_assistant_remembered or not self.current_assistant_text.strip():
            return
        self._remember_interaction(
            {"role": "assistant", "content": self.current_assistant_text}
        )
        self.current_assistant_remembered = True

    def _schedule_user_commit(self) -> None:
        if (
            self.closed
            or self.debug_paused
            or self.user_turn_processing
            or not self.speech_seen
            or not self.input_pcm
            or (self.user_commit_task and not self.user_commit_task.done())
        ):
            return
        self.user_commit_task = asyncio.create_task(self._run_user_commit())

    async def _run_user_commit(self) -> None:
        try:
            await self._commit_user_audio()
        finally:
            self.user_commit_task = None
            self._start_deferred_response()

    async def _commit_user_audio(self) -> None:
        await self.debug_resume.wait()
        if self.user_turn_processing or not self.speech_seen or not self.input_pcm:
            return
        pcm = bytes(self.input_pcm)
        interrupt_ack = (
            self.settings.interrupt_ack if self.interrupt_ack_pending else ""
        )
        audio_ms = len(pcm) * 1000 // 32000
        self.user_turn_processing = True
        self._clear_input()
        item_id = f"item_{uuid.uuid4().hex}"
        start_response = False
        debug_record = None
        try:
            try:
                logger.info(
                    "session=%s requesting ASR audio_ms=%d", self.session_id, audio_ms
                )
                prompt_mode = resolve_asr_prompt_mode(
                    self.settings.asr_prompt_mode, self.settings.asr_model
                )
                asr_context = (
                    default_asr_context(prompt_mode, self.hotword_domain)
                    if self.settings.asr_context is None
                    else self.settings.asr_context.strip()
                )
                messages = build_asr_messages(pcm, asr_context, prompt_mode)
                debug_record = await self.asr_debug.start(
                    pcm,
                    model=self.settings.asr_model,
                    prompt_mode=prompt_mode,
                    language=self.settings.asr_language,
                )
                started_at = time.perf_counter()
                raw_transcript = await self.interactive.transcribe(messages)
                transcript = parse_asr_transcript(raw_transcript)
                await self.asr_debug.finish(
                    debug_record,
                    raw_transcript=raw_transcript,
                    transcript=transcript,
                )
                logger.info(
                    "session=%s ASR completed transcript=%s raw=%s latency_ms=%.1f",
                    self.session_id,
                    transcript,
                    raw_transcript,
                    (time.perf_counter() - started_at) * 1000,
                )
            except Exception as exc:
                await self.asr_debug.finish(debug_record, error=repr(exc))
                logger.exception("session=%s ASR failed", self.session_id)
                await self.send(_error_event("asr_error", str(exc)))
                transcript = ""

            # Guard against pathological repeated-character hallucinations.
            max_chars = max(32, int(len(pcm) / 32000 * 35 + 20))
            if len(transcript) > max_chars:
                transcript = transcript[:max_chars]
            await self.send(_event("input_audio_buffer.committed", item_id=item_id))
            await self.send(
                _event(
                    "conversation.item.input_audio_transcription.completed",
                    item_id=item_id,
                    content_index=0,
                    transcript=transcript,
                )
            )
            if transcript:
                interaction_message = {
                    "role": "user",
                    "content": [audio_message(self._history_user_pcm(pcm))],
                }
                history_message = {"role": "user", "content": transcript}
                if (
                    self.response_task and not self.response_task.done()
                ) or self.pending_tool_calls:
                    self._queue_pending_user_turn(
                        history_message, interaction_message, interrupt_ack
                    )
                else:
                    if self.pending_user_turns:
                        logger.info(
                            "session=%s discarded stale pending user turn before "
                            "starting latest turn",
                            self.session_id,
                        )
                        self.pending_user_turns.clear()
                    self._remember_interaction(interaction_message)
                    self.history.append(history_message)
                    self.next_response_ack = interrupt_ack
                    self.tool_ack_spoken = False
                    self.tool_continuation_pending = False
                    start_response = True
        finally:
            self.user_turn_processing = False

        if start_response:
            await self._start_response()
        else:
            self._start_deferred_response()

    def _history_user_pcm(self, pcm: bytes) -> bytes:
        max_ms = self.settings.interactive_history_user_audio_ms
        if max_ms <= 0:
            return pcm
        max_bytes = max_ms * 32000 // 1000
        return pcm[-max_bytes:]

    def _clear_input(self) -> None:
        self.interrupt_votes.clear()
        self.input_ulaw.clear()
        self.input_generation += 1
        self.input_pcm.clear()
        self.tick_audio.clear()
        self.pre_roll_ulaw.clear()
        self.input_resample_state = None
        self.speech_seen = False
        self.speech_start_ms = None
        self.consecutive_complete_count = 0
        self.semantic_turn_started = False
        self.consecutive_interrupt_count = 0
        self.interrupt_confirmed = False
        self.interrupt_negative_count = 0
        self.interrupt_classification_attempts = 0
        self.candidate_speech_ms = 0
        self.candidate_silence_ms = 0
        self.candidate_started_during_response = False
        self.interrupt_ack_pending = False
        self.tts_playback_allowed.set()

    async def _start_response(self) -> None:
        if self.response_task and not self.response_task.done():
            return
        self.response_task = asyncio.create_task(self._run_response())

    async def _run_response(self) -> None:
        self.assistant_active = True
        self.tts_interrupted = False
        self._reset_interrupt_timeline()
        response_ack = self.next_response_ack
        self.next_response_ack = ""
        self.current_assistant_text = ""
        self.spoken_assistant_text = ""
        self.current_clause_text = ""
        self.current_clause_total = 0
        self.current_clause_sent = 0
        self.current_assistant_remembered = False
        self.current_item_id = f"item_{uuid.uuid4().hex}"
        logger.info("session=%s starting text response", self.session_id)
        await self.send(
            _event(
                "response.created",
                response={"id": f"resp_{uuid.uuid4().hex}", "status": "in_progress"},
            )
        )
        tool_calls: dict[int, dict[str, Any]] = {}
        reasoning_content = ""
        assistant_text = response_ack
        self.current_assistant_text = assistant_text
        join_ack_to_first_delta = bool(response_ack)
        think_state = "pending"
        think_buffer = ""
        tts_buffer = ""
        tts_queue: asyncio.Queue[str | None] = asyncio.Queue()
        tts_task = asyncio.create_task(self._consume_tts(tts_queue))
        self.tts_task = tts_task
        response_finished = False
        try:
            if response_ack:
                await self.send(
                    _event(
                        "response.text.delta",
                        delta=response_ack,
                        item_id=self.current_item_id,
                    )
                )
                ack_task = asyncio.create_task(self._speak(response_ack))
                self.tts_task = ack_task
                try:
                    await self._wait_for_tts(ack_task)
                except asyncio.CancelledError:
                    if not self.tts_interrupted:
                        raise
                self.tts_task = tts_task
            llm_started_at = time.perf_counter()
            async for delta in self.text.stream(self.history, self.tools):
                reasoning_part = delta.get("reasoning_content")
                if isinstance(reasoning_part, str):
                    reasoning_content += reasoning_part
                if delta.get("content"):
                    part, think_state, think_buffer = _visible_text(
                        delta["content"], think_state, think_buffer
                    )
                    if not part:
                        continue
                    if join_ack_to_first_delta:
                        if not part[0].isspace():
                            part = f" {part}"
                        join_ack_to_first_delta = False
                    assistant_text += part
                    if not self.tts_interrupted:
                        self.current_assistant_text += part
                        await self.send(
                            _event(
                                "response.text.delta",
                                delta=part,
                                item_id=self.current_item_id,
                            )
                        )
                        tts_buffer += part
                        chunks, tts_buffer = streaming_clause_chunks(tts_buffer)
                        for chunk in chunks:
                            tts_queue.put_nowait(chunk)
                        if tts_task.done():
                            await self._wait_for_tts(tts_task)
                for position, call in enumerate(delta.get("tool_calls", []) or []):
                    if (
                        not self.tts_interrupted
                        and not assistant_text.strip()
                        and not self.tool_ack_spoken
                        and self.settings.tool_call_ack
                    ):
                        tool_ack = self.settings.tool_call_ack
                        assistant_text = tool_ack
                        self.current_assistant_text = tool_ack
                        self.tool_ack_spoken = True
                        await self.send(
                            _event(
                                "response.text.delta",
                                delta=tool_ack,
                                item_id=self.current_item_id,
                            )
                        )
                        tts_queue.put_nowait(tool_ack)
                    try:
                        index = int(call.get("index", position) or 0)
                    except (TypeError, ValueError) as exc:
                        raise ValueError(
                            f"Text backend returned an invalid tool call index: "
                            f"{call.get('index')}"
                        ) from exc
                    entry = tool_calls.setdefault(
                        index,
                        {
                            "id": call.get("id") or f"call_{uuid.uuid4().hex}",
                            "name": "",
                            "arguments": "",
                        },
                    )
                    if call.get("id"):
                        entry["id"] = call["id"]
                    function = call.get("function", {})
                    entry["name"] += function.get("name", "")
                    entry["arguments"] += function.get("arguments", "")
            if not self.tts_interrupted and tts_buffer.strip():
                tts_queue.put_nowait(tts_buffer.strip())
            tts_queue.put_nowait(None)
            logger.info(
                "session=%s LLM completed assistant_text=%s latency_ms=%.1f",
                self.session_id,
                assistant_text,
                (time.perf_counter() - llm_started_at) * 1000,
            )
            for _, call in sorted(tool_calls.items()):
                call_id = call["id"]
                if not call["name"]:
                    raise ValueError("Text backend returned a tool call without a name")
                arguments = call["arguments"] or "{}"
                try:
                    parsed_arguments = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Text backend returned invalid arguments for {call['name']}: "
                        f"{arguments}"
                    ) from exc
                if not isinstance(parsed_arguments, dict):
                    raise ValueError(
                        f"Text backend returned non-object arguments for {call['name']}"
                    )
                logger.info(
                    "session=%s emitting tool call name=%s call_id=%s arguments=%s",
                    self.session_id,
                    call["name"],
                    call_id,
                    arguments[:1000],
                )
                call["_emitted_at"] = time.perf_counter()
                await self.send(
                    _event(
                        "response.output_item.added",
                        item_id=self.current_item_id,
                        item_type="function_call",
                        call_id=call_id,
                        name=call["name"],
                    )
                )
                await self.send(
                    _event(
                        "response.function_call_arguments.done",
                        call_id=call_id,
                        name=call["name"],
                        arguments=arguments,
                    )
                )
                call["arguments"] = arguments
                self.pending_tool_calls[call_id] = call
            response_interrupted = self.tts_interrupted
            if tool_calls:
                assistant_message = {
                    "role": "assistant",
                    "content": (
                        self._heard_assistant_text() or None
                        if response_interrupted
                        else assistant_text or None
                    ),
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": call["arguments"] or "{}",
                            },
                        }
                        for _, call in sorted(tool_calls.items())
                    ],
                }
                if reasoning_content:
                    assistant_message["reasoning_content"] = reasoning_content
                self.history.append(assistant_message)
            if response_interrupted:
                if not tts_task.done():
                    tts_task.cancel()
                await asyncio.gather(tts_task, return_exceptions=True)
                self._remember_interrupted_assistant_turn(
                    reasoning_content=reasoning_content
                )
                await self._send_response_cancelled()
                logger.info(
                    "session=%s interrupted response LLM drained tool_calls=%d",
                    self.session_id,
                    len(tool_calls),
                )
            else:
                await self._wait_for_tts(tts_task)
                # Publish completion only after all synthesized audio has drained.
                await self.send(
                    _event("response.output_audio.done", item_id=self.current_item_id)
                )
                # tau-voice treats text.done as the end of the assistant turn.
                if assistant_text:
                    await self.send(
                        _event(
                            "response.text.done",
                            text=assistant_text,
                            item_id=self.current_item_id,
                        )
                    )
                    logger.info(
                        "session=%s response.text.done sent after TTS playback",
                        self.session_id,
                    )
                if not tool_calls and assistant_text:
                    assistant_message = {
                        "role": "assistant",
                        "content": assistant_text,
                    }
                    if reasoning_content:
                        assistant_message["reasoning_content"] = reasoning_content
                    self.history.append(assistant_message)
                if assistant_text:
                    self._finish_assistant_interaction_turn()
                await self.send(
                    _event(
                        "response.done",
                        response={
                            "id": f"resp_{uuid.uuid4().hex}",
                            "status": "completed",
                        },
                    )
                )
            response_finished = True
        except ResponseInterrupted:
            if not tts_task.done():
                tts_task.cancel()
            await asyncio.gather(tts_task, return_exceptions=True)
            self._remember_interrupted_assistant_turn(
                reasoning_content=reasoning_content
            )
            await self._send_response_cancelled()
            response_finished = True
        except asyncio.CancelledError:
            if not tts_task.done():
                tts_task.cancel()
            await asyncio.gather(tts_task, return_exceptions=True)
            self._remember_interrupted_assistant_turn(
                reasoning_content=reasoning_content
            )
            await self._send_response_cancelled()
            response_finished = True
            raise
        except Exception as exc:
            if not tts_task.done():
                tts_task.cancel()
            await asyncio.gather(tts_task, return_exceptions=True)
            logger.exception(
                "session=%s response pipeline failed partial_assistant_text=%s",
                self.session_id,
                assistant_text,
            )
            await self.send(_error_event("response_error", str(exc)))
        finally:
            self.assistant_active = False
            self.assistant_speaking = False
            self.tts_interrupted = False
            self.tts_task = None
            self.current_item_id = None
            if response_finished and not self.closed and not self.pending_tool_calls:
                self._start_next_pending_user_turn()

    async def _wait_for_tts(self, task: asyncio.Task[None]) -> None:
        await task

    def _queue_pending_user_turn(
        self,
        history_message: dict[str, Any],
        interaction_message: dict[str, Any],
        interrupt_ack: str,
    ) -> None:
        superseded = bool(self.pending_user_turns)
        self.pending_user_turns[:] = [
            (history_message, interaction_message, interrupt_ack)
        ]
        logger.info(
            "session=%s queued latest user turn superseded=%s",
            self.session_id,
            superseded,
        )

    def _start_next_pending_user_turn(self) -> None:
        self._start_deferred_response()

    def _start_deferred_response(self) -> None:
        if self.debug_paused:
            return
        current_task = asyncio.current_task()
        response_in_progress = bool(
            self.response_task
            and self.response_task is not current_task
            and not self.response_task.done()
        )
        if (
            self.closed
            or self.assistant_active
            or self.user_turn_processing
            or (
                self.user_commit_task
                and self.user_commit_task is not current_task
                and not self.user_commit_task.done()
            )
            or response_in_progress
            or self.pending_tool_calls
        ):
            return

        if self.pending_user_turns:
            if self.speech_seen:
                return
            history_message, interaction_message, interrupt_ack = (
                self.pending_user_turns[-1]
            )
            self.pending_user_turns.clear()
            self._remember_interaction(interaction_message)
            self.history.append(history_message)
            self.next_response_ack = interrupt_ack
            self.tool_ack_spoken = False
            self.tool_continuation_pending = False
            logger.info("session=%s starting latest pending user turn", self.session_id)
            self.response_task = asyncio.create_task(self._run_response())
            return

        if self.tool_continuation_pending:
            self.tool_continuation_pending = False
            if self.speech_seen:
                self.candidate_started_during_response = True
            logger.info("session=%s resuming deferred tool response", self.session_id)
            self.response_task = asyncio.create_task(self._run_response())

    async def _consume_tts(self, queue: asyncio.Queue[str | None]) -> None:
        prepared = await self._next_synthesized_clause(queue)
        while prepared is not None:
            clause, audio = prepared
            next_clause = asyncio.create_task(self._next_synthesized_clause(queue))
            try:
                await self._play_clause_audio(clause, audio)
                prepared = await next_clause
            except BaseException:
                if not next_clause.done():
                    next_clause.cancel()
                await asyncio.gather(next_clause, return_exceptions=True)
                raise

    async def _next_synthesized_clause(
        self, queue: asyncio.Queue[str | None]
    ) -> tuple[str, bytes] | None:
        clause = await queue.get()
        if clause is None:
            return None
        return clause, await self._synthesize_clause(clause)

    async def _speak(self, text: str) -> None:
        for clause in clause_chunks(text):
            await self._speak_clause(clause)

    async def _speak_clause(self, clause: str) -> None:
        audio = await self._synthesize_clause(clause)
        await self._play_clause_audio(clause, audio)

    async def _synthesize_clause(self, clause: str) -> bytes:
        logger.info("session=%s requesting TTS chars=%d", self.session_id, len(clause))
        tts_started_at = time.perf_counter()
        if clause == self.settings.tool_call_ack:
            audio = await self.tts.synthesize_cached(clause)
        else:
            audio = await self.tts.synthesize(clause)
        logger.info(
            "session=%s TTS completed chars=%d audio_bytes=%d latency_ms=%.1f",
            self.session_id,
            len(clause),
            len(audio),
            (time.perf_counter() - tts_started_at) * 1000,
        )
        return audio

    async def _play_clause_audio(self, clause: str, audio: bytes) -> None:
        if self.tts_interrupted:
            raise ResponseInterrupted
        if not self.tts_playback_allowed.is_set():
            logger.info(
                "session=%s waiting for interaction model before TTS playback",
                self.session_id,
            )
            await self.tts_playback_allowed.wait()
            if self.tts_interrupted:
                raise ResponseInterrupted
        self.current_clause_text = clause
        self.current_clause_sent = 0
        self.current_clause_total = len(audio)
        self.assistant_speaking = True
        try:
            chunk_size = max(1, int(8000 * self.settings.tick_ms / 1000))
            input_clock_cursor = self.received_audio_bytes
            logger.info(
                "session=%s TTS playback starting chars=%d audio_bytes=%d "
                "input_clock_bytes=%d pacing=%s",
                self.session_id,
                len(clause),
                len(audio),
                input_clock_cursor,
                (
                    "input_audio_clock"
                    if self.settings.tts_realtime_pacing
                    else "disabled"
                ),
            )
            for offset in range(0, len(audio), chunk_size):
                if not self.tts_playback_allowed.is_set():
                    self.assistant_speaking = False
                    await self.tts_playback_allowed.wait()
                    if self.tts_interrupted:
                        raise ResponseInterrupted
                    self.assistant_speaking = True
                    input_clock_cursor = self.received_audio_bytes
                chunk = audio[offset : offset + chunk_size]
                self.current_clause_sent += len(chunk)
                encoded = base64.b64encode(chunk).decode("ascii")
                await self.send(
                    _event(
                        "response.output_audio.delta",
                        delta=encoded,
                        item_id=self.current_item_id,
                    )
                )
                if self.settings.tts_realtime_pacing:
                    input_clock_cursor += len(chunk)
                    await self._wait_for_input_audio_clock(input_clock_cursor)
        finally:
            self.assistant_speaking = False
        self.spoken_assistant_text += (
            " " if self.spoken_assistant_text else ""
        ) + clause
        await self.send(
            _event(
                "response.output_audio_transcript.delta",
                delta=clause,
                item_id=self.current_item_id,
            )
        )
        await self.send(
            _event(
                "response.output_audio_transcript.done",
                transcript=clause,
                item_id=self.current_item_id,
            )
        )
        logger.info(
            "session=%s TTS playback completed chars=%d audio_bytes=%d "
            "input_clock_bytes=%d",
            self.session_id,
            len(clause),
            len(audio),
            self.received_audio_bytes,
        )

    async def _interrupt(self) -> None:
        if self.interrupt_confirmed:
            return
        self.interrupt_confirmed = True
        await self.send(
            _event(
                "input_audio_buffer.speech_started",
                audio_start_ms=self.speech_start_ms or 0,
                item_id=self.current_item_id,
            )
        )
        self.tts_interrupted = True
        self.tts_playback_allowed.set()
        self.interrupt_ack_pending = True
        self.assistant_speaking = False
        logger.warning(
            "session=%s confirmed interruption after %d classifier positives",
            self.session_id,
            self.consecutive_interrupt_count,
        )
        if self.tts_task and not self.tts_task.done():
            self.tts_task.cancel()
        logger.info(
            "session=%s TTS stopped; LLM generation continues in background",
            self.session_id,
        )

    def _heard_assistant_text(self) -> str:
        heard = self.spoken_assistant_text.strip()
        if self.current_clause_total <= 0 or self.current_clause_sent <= 0:
            return heard
        fraction = min(1.0, self.current_clause_sent / self.current_clause_total)
        chars_heard = max(1, int(len(self.current_clause_text) * fraction))
        partial = self.current_clause_text[:chars_heard].rstrip()
        if not partial or (heard and heard.endswith(partial)):
            return heard
        return " ".join(part for part in (heard, partial) if part)

    def _remember_interrupted_assistant_turn(self, reasoning_content: str = "") -> None:
        heard_text = self._heard_assistant_text()
        if self.pending_tool_calls:
            for message in reversed(self.history):
                if message.get("role") == "assistant" and message.get("tool_calls"):
                    message["content"] = heard_text or None
                    break
        elif (heard_text or reasoning_content) and not (
            self.history
            and self.history[-1].get("role") == "assistant"
            and self.history[-1].get("content") == heard_text
        ):
            assistant_message = {
                "role": "assistant",
                "content": heard_text or None,
            }
            if reasoning_content:
                assistant_message["reasoning_content"] = reasoning_content
            self.history.append(assistant_message)
        if not heard_text:
            return
        self.current_assistant_text = heard_text
        self.current_assistant_remembered = False
        self._finish_assistant_interaction_turn()

    async def _send_response_cancelled(self) -> None:
        await self.send(
            _event(
                "response.cancelled",
                response={"id": "local", "status": "cancelled"},
            )
        )

    async def _advance_input_audio_clock(self, byte_count: int) -> None:
        async with self.input_audio_clock:
            self.received_audio_bytes += byte_count
            self.input_audio_clock.notify_all()

    async def _wait_for_input_audio_clock(self, target_bytes: int) -> None:
        """Pace output against tau2's input ticks instead of wall-clock time."""
        async with self.input_audio_clock:
            await self.input_audio_clock.wait_for(
                lambda: (
                    self.received_audio_bytes >= target_bytes
                    or self.closed
                    or self.tts_interrupted
                )
            )

    def _truncate(self, audio_end_ms: int) -> None:
        # Playback progress already tracks what the caller heard.
        return

    async def _conversation_item(self, item: dict[str, Any]) -> None:
        if item.get("type") != "function_call_output":
            return
        call_id = item.get("call_id")
        if call_id not in self.pending_tool_calls:
            return
        self.history.append(
            {"role": "tool", "tool_call_id": call_id, "content": item.get("output", "")}
        )
        call = self.pending_tool_calls.pop(call_id)
        emitted_at = call.get("_emitted_at")
        roundtrip_ms = (
            (time.perf_counter() - emitted_at) * 1000
            if isinstance(emitted_at, (int, float))
            else -1.0
        )
        logger.info(
            "session=%s received tool result call_id=%s pending=%d roundtrip_ms=%.1f",
            self.session_id,
            call_id,
            len(self.pending_tool_calls),
            roundtrip_ms,
        )
        await self.send(_event("conversation.item.created", item=item))
