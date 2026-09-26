# Local Realtime service

This is a small OpenAI-Realtime-compatible WebSocket facade for tau2's current
OpenAI provider. It accepts 8 kHz mono mu-law audio and emits the same format.
Interactive classification and ASR use the request shape in
`interactive_demo/{interrupt,semantic_complete,asr_val}.jsonl`.

Install the standalone dependencies and run from the repository root:

```bash
python -m pip install -r services/realtime_service/service_app/requirements.txt
uvicorn services.realtime_service.service_app.app:app --host 0.0.0.0 --port 8765
```

Environment variables:

```text
INTERACTIVE_BASE_URL=http://127.0.0.1:9004/v1
INTERACTIVE_MODEL=interactive_model
ASR_BASE_URL=http://localhost:5005/v1
ASR_MODEL=interactive_model
TEXT_BASE_URL=http://127.0.0.1:8000/v1
TEXT_MODEL=qwen3-32b
TEXT_STREAMING=true
TTS_BASE_URL=http://tts-service.default.svc.cluster.local/v1
TTS_MODEL=qwen3_tts
TTS_PCM_SAMPLE_RATE=8000
LOCAL_MODEL_API_KEY=EMPTY
TTS_SPEAKER=dongbai
GREETING=Hi! How can I help you today?
BACKEND_TRUST_ENV=false
INTERACTIVE_HISTORY_MESSAGES=8
INTERACTIVE_HISTORY_USER_AUDIO_MS=5000
INTERACTIVE_DEBUG_DIR=/path/to/interactive_debug
CLASSIFIER_MAX_TOKENS=5
TICK_MS=200
CLASSIFIER_WINDOW_TICKS=2
INTERRUPT_WINDOW_MS=3000
INTERRUPT_NEGATIVE_CONFIRMATIONS=2
INTERRUPT_CONFIRMATIONS=2
INTERRUPT_ACK=Okay.
TOOL_CALL_ACK=One moment.
VAD_PRE_ROLL_MS=300
COMPLETE_CONFIRMATIONS=2
END_SILENCE_MS=3000
INTERRUPT_ENABLED=true
ASR_TIMEOUT_S=30
ASR_MAX_TOKENS=256
TTS_REALTIME_PACING=true
```

With `TTS_REALTIME_PACING=true`, output audio is released against the incoming
8 kHz audio clock. This keeps Tau2's discrete simulation ticks and assistant
playback synchronized even while the simulator is blocked generating its next
user utterance. Set it to `false` only for clients that do not continuously send
input audio while the assistant is speaking. The greeting uses the same pacing
path and publishes its completion only after its audio has elapsed.

The configured OpenAI-compatible TTS endpoint must return raw, mono PCM16 for
`POST /v1/audio/speech` when sent `response_format=pcm`. Set
`TTS_PCM_SAMPLE_RATE` to the service's returned PCM sample rate (8000 for the
current Kubernetes TTS service). No local TTS proxy is required.

Semantic completeness is classified on each configured audio window without a VAD
trailing-silence prerequisite. The service requires `COMPLETE_CONFIRMATIONS`
consecutive `complete` classifications (two by default) before committing a user
turn. An `incomplete` classification resets the count. If semantic completion is not confirmed, it
commits after `END_SILENCE_MS` (3 seconds by default). Set
`INTERRUPT_ENABLED=false` to ignore all user audio while the assistant is
speaking and let the response finish without interruption. It buffers two
200 ms ticks between classifier calls. Interrupt classification uses the latest
three seconds of the current user candidate, left-padded with silence at the
start of speech. Historical user audio is independently capped by
`INTERACTIVE_HISTORY_USER_AUDIO_MS` (five seconds by default). VAD only starts
candidate buffering; it cannot
pause or cancel a response and is explicitly treated as unreliable in noisy
environments. Candidates that begin anywhere during an active response, including
LLM and TTS synthesis gaps, are sent to the interaction model. The first
`interrupt` classification closes the playback gate, and the second consecutive
result stops TTS. LLM streaming continues to completion so a tool call is not
lost, while generated but unheard assistant text is discarded from history. A
subsequent `backchannel` or `ignorable` classification before confirmation
resumes playback from
the same point without catching up elapsed input ticks in a burst. Repeated
negative classifications discard the noisy candidate without requiring VAD
silence. Interruption requires `INTERRUPT_CONFIRMATIONS` consecutive `interrupt`
results (two by default); any `backchannel` or `ignorable` result resets that
count. After the interrupted user turn is committed, `INTERRUPT_ACK` is spoken immediately before the next LLM
response and stored with that response as one assistant history message. A candidate rejected repeatedly by the interaction model is
discarded. VAD-only forced interruption is disabled permanently in the session path; the legacy
`INTERRUPT_FORCE_SPEECH_MS` setting is parsed for compatibility but is ignored.
`VAD_PRE_ROLL_MS` preserves audio immediately before the first detected speech
packet. TTS synthesis is not treated as active playback, and the next clause is
prefetched while the current clause plays. Sustained VAD speech is never enough
to interrupt a response by itself; only the configured consecutive interaction
model results can stop TTS. When a response starts with a tool call, `TOOL_CALL_ACK` is emitted as soon as the first tool-call delta arrives,
while the tool request continues, and is emitted at most once per user turn. If
the model requests several tools in parallel, only the first is emitted; its
result is returned to the model before another tool can be requested.

Interruption and semantic-completeness classification use
`INTERACTIVE_BASE_URL` and `INTERACTIVE_MODEL`. ASR uses the independent
`ASR_BASE_URL` endpoint. `ASR_MODEL` defaults to `INTERACTIVE_MODEL`, so it only
needs to be set when the ASR server expects a different model name.

Classifier requests are saved by default under `service_app/interactive_debug`,
grouped by realtime session. Each WAV filename and the session's `timeline.jsonl`
contain the classifier kind and raw returned label. Set `INTERACTIVE_DEBUG_DIR`
to another directory, or set it to an empty string to disable recording.

`BACKEND_TRUST_ENV=false` prevents localhost model requests from being routed
through `HTTP_PROXY`/`HTTPS_PROXY`. Set it to `true` only when the model
endpoints intentionally require those environment proxies.

`TEXT_STREAMING=true` sends comma- or sentence-terminated text chunks to TTS
while the LLM is still generating. Tool-call deltas are buffered and emitted
only after the full name and arguments have been assembled.

## Use the local TTS for tau-voice users

The service also exposes the subset of ElevenLabs' HTTP API used by tau2. This
keeps the tau2 source unchanged: a startup hook only supplies the SDK's otherwise
hard-coded base URL. Start the service normally, then run tau2 from the repository
root with these environment variables:

```bash
PYTHONPATH="$PWD/services/realtime_service/elevenlabs_override${PYTHONPATH:+:$PYTHONPATH}" \
TAU2_ELEVENLABS_BASE_URL=http://127.0.0.1:8765 \
ELEVENLABS_API_KEY=EMPTY \
uv run tau2 run \
  --domain retail \
  --audio-native \
  --audio-native-provider br \
  --audio-native-model br-voice-realtime \
  --speech-complexity regular
```

Append the remaining evaluation flags as usual. `PYTHONPATH` is inherited by
spawned workers, so the override also works with `--max-concurrency 3`.
The base URL may also be written as `http://127.0.0.1:8765/v1` or copied from
`BR_REALTIME_BASE_URL`; the startup hook normalizes those forms to the API root.
Restart Uvicorn after changing the service code because the documented command
does not enable auto-reload.

Realtime assistant speech uses `TTS_SPEAKER` (`dongbai` by default). The
ElevenLabs compatibility endpoint maps tau2's seven built-in persona voice IDs
to `beier`, `huangyue`, `panwanting`, `wanqing`, `yaomengru`, `yumi`, and `zz`,
respectively. Unknown voice IDs are assigned deterministically across the same
speaker pool, so a custom `TAU2_VOICE_ID_*` remains stable between requests.

Override the built-in mapping with a JSON object when needed:

```bash
export ELEVENLABS_VOICE_MAP='{"custom-elevenlabs-id":"yumi"}'
```

The compatibility endpoint still ignores ElevenLabs model IDs, voice settings,
and seeds. It supports only the `pcm_16000` output requested by tau2. Responses
include `x-local-tts-speaker` so the selected local voice is visible to clients.

On the first `session.update`, `GREETING` is spoken once and added to the model
and interaction histories after playback. If interrupted, only the heard prefix is recorded. Set it to an empty string to disable the greeting.

Candidate audio that begins at any point during an active response is classified
by the interaction model, including while the LLM or TTS backend is still
generating. VAD never cancels unsent audio directly. Confirmed interruptions stop
TTS immediately but let LLM generation and tool-call assembly finish. No full
text completion event is sent, and only audio actually delivered is retained in
history. Pending user turns use latest-wins semantics: a newly committed turn
replaces any older turn that has not started, and no deferred response starts
while user speech, classification, or ASR processing owns the floor.
