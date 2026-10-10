# Local Realtime service

This is a small OpenAI-Realtime-compatible WebSocket facade for tau2's current
OpenAI provider. It accepts 8 kHz mono mu-law audio and emits the same format.
Interactive classification uses the exact system prompts and message layout in
`new_interactive_assets/ms_swift/{interrupt,semantic_complete}/train.jsonl`.
Each `<audio>` is converted to an `audio_url` block using the same ordering and
text boundaries as `new_interactive_assets/evaluate_duplex.py`.
During interruption detection, the last historical user audio is merged into
the final user message as audio, `\nstage=speaking; classify the current audio
window.\n`, then current-window audio. The unfinished assistant response is not
included. Without a preceding user, the final message contains the stage text
and current audio only. Semantic completion uses `stage=idle; classify the
current accumulated audio.` followed by the accumulated audio. The trained
interrupt prompt lists `ignorable` and `interrupt`; the service continues to
accept `backchannel` through its output choices and interruption state machine
for compatibility with models that produce it.

Install the standalone dependencies and run from the repository root:

```bash
python -m pip install -r services/realtime_service/service_app/requirements.txt
uvicorn services.realtime_service.service_app.app:app --host 0.0.0.0 --port 8765
```

After the server starts, open `http://127.0.0.1:8765/` for the built-in browser
debugger. It can configure a session, stream microphone audio as 8 kHz G.711
mu-law, play response audio, inspect raw events, manually commit a turn, and
return function-call results. The client keeps sending silent audio packets while
the microphone is off so that input-clock-paced TTS can continue to play.

The browser opts into per-session debug events with `session.debug=true`.
The **Audio** tab records each interaction-model request, label choices,
prediction, latency, and whether the result was applied or discarded. The browser
sets `session.debug_audio_mode="on_demand"`: full WAV/Base64 messages stay on the
server until **加载本次完整输入与试听音频** is clicked. This avoids repeatedly
transmitting accumulated audio and history over an SSH tunnel. Inputs for the
latest 20 requests are retained in memory; load an input before exporting its
JSON. Other debug clients can omit this option to keep inline messages.
Audio players expose the classified window/accumulated
audio, preceding user voice context, and historical user audio. Pausing sends
one separate snapshot of the current candidate, ignorable buffer, and interrupt
window. Large caches are not retransmitted with every prediction. A reviewer can choose an expected
label and export the request, audio data URLs, prediction, and annotation as
JSON. These annotations are local to the page; they do not change inference.
The page keeps the most recent 20 requests in memory.

The microphone upload queue keeps at most two 200 ms packets and drains both
on the next tick if needed. During congestion it discards the oldest unsent
microphone packets instead of replaying seconds of stale audio; `INPUT QUEUE`
shows queued duration, WebSocket buffered bytes, heartbeat RTT, scheduled
playback duration, and discarded packet count. While microphone capture is
healthy, an empty upload tick waits for captured audio instead of adding an
extra silent packet. Synthetic silence is used when the microphone is off or
capture has stalled for 400 ms. Confirmed interruption clears audio already
scheduled in the browser, including a pending AudioContext resume.
Uploads stop adding audio while the socket has at least 8 KiB buffered. Debug
heartbeats continue every five seconds even during pause, so pausing the audio
does not leave the connection entirely idle. Actual closure is recorded with
its code and reason in Events and the conversation display.

`start.sh` defaults to a 400 ms classifier window (`200 ms × 2`); set
`CLASSIFIER_WINDOW_TICKS` in the environment to override it. This cadence and
the confirmation counts add delay beyond the logged model-request latency.
Interrupt detection still requires 800 ms of VAD speech before its first vote.
The startup log includes the effective classifier window. Pause/resume and
disconnect logs include session IDs, pause state, close reason, and input idle
time to distinguish paused audio from a lost transport. Heartbeat RTT measures
the browser-to-service round trip, including SSH forwarding and event handling;
classifier latency measures the service-side request path.

Click **暂停检查** to stop microphone uploads and silent clock packets and
suspend live assistant playback. The server acknowledges `debug.pause`, ignores
subsequent audio packets, and holds normal output. In-flight model requests may
finish; classifier predictions remain visible but are applied only after
**继续运行** (`debug.resume`). Review audio players remain usable while paused.
Input buffers and conversation history are preserved, and audio captured during
the pause is not replayed on resume. Debug events and pause controls are enabled
only for sessions that explicitly opt in, independently of debug WAV recording.

Environment variables:

```text
INTERACTIVE_BASE_URL=http://127.0.0.1:9004/v1
INTERACTIVE_MODEL=interactive_model
ASR_BASE_URL=http://localhost:5005/v1
ASR_MODEL=interactive_model
ASR_PROMPT_MODE=auto
ASR_LANGUAGE=en
ASR_CONTEXT=
ASR_DEBUG_DIR=/path/to/asr_debug
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
turn. Before a target-user turn starts, `ignorable` identifies noise, non-speech,
or speech not addressed to the assistant. That audio is removed from the active
turn and retained in a separate rolling PCM buffer capped by
`IGNORABLE_AUDIO_MAX_S` (15 seconds by default). The first `incomplete` result
locks the active target-user turn; subsequent classifications are constrained to
`incomplete` or `complete`, so the turn cannot fall back to `ignorable`. An
`incomplete` classification resets the completion count. Once locked, the turn
is committed after `END_SILENCE_MS` of continuous silence (3 seconds by default)
even if the classifier never returns `complete`. Set
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
needs to be set when the ASR server expects a different model name. For
Qwen3-ASR, `ASR_LANGUAGE` optionally forces a language (for example,
`en`); leave it unset for automatic language detection.
`ASR_PROMPT_MODE` accepts `qwen3_asr`, `qwen3_omni`, or `auto`. Auto
selects Qwen3-ASR only when the model name contains `qwen3-asr`; other model
names retain the Qwen3-Omni prompt shape. Qwen3-ASR defaults to the documented
audio-only Chat request: it sends no system message and no hotwords. Qwen3-Omni
receives only the hotword list selected for the Tau2 domain in its
natural-language system context, plus an additional text instruction after
`audio_url` in the user content. `ASR_CONTEXT` can explicitly replace the
system message for either mode;
set it to an empty string to omit that message. Both raw
`language English<asr_text>...` and text-only responses are accepted.

The service identifies `retail`, `airline`, or `telecom` from the domain
policy embedded in Tau2's full-duplex system prompt. Only that domain's
canonical candidates are appended to the text LLM system prompt and, in
Qwen3-Omni mode, to the ASR system message. If the prompt is unknown or
ambiguous, all three hotword lists are appended and a warning is logged. The
LLM is instructed to
normalize an ASR-corrupted name, ID, username, email, phone number, address, ZIP
code, airport code, or product term only when there is one strong phonetic or
spelling match that fits the tool parameter. It must not insert a candidate the
user did not attempt to say, and must ask for clarification when several
candidates are plausible. This context is rebuilt on every session update and
does not duplicate `SERVER_INSTRUCTION_SUFFIX`.

Set `ASR_DEBUG_DIR` to save the exact 16 kHz mono PCM WAV passed to each ASR
request. Files are grouped by realtime session; the adjacent JSON and
`timeline.jsonl` record the model, prompt mode, language, raw response, parsed
transcript, and request error. Recording is disabled when the variable is unset
or empty because these files may contain user speech. `start.sh` enables it at
`service_app/asr_debug` for local diagnosis.

Replay any captured WAV against both model families with:

```bash
python scripts/test_asr_audio.py \
  service_app/asr_debug/<session-id>/<request>.wav \
  --domain retail \
  --output service_app/asr_debug/<session-id>/comparison.json
```

The script reads `LOCAL_MODEL_API_KEY`, defaults both base URLs to
`ASR_BASE_URL`, bypasses environment proxies, and runs three comparisons:
Qwen3-ASR's documented `/audio/transcriptions` endpoint, Qwen3-ASR chat, and
Qwen3-Omni chat. Use `--domain retail|airline|telecom` (or
`ASR_HOTWORD_DOMAIN`) to give Qwen3-Omni the matching list; omit it to use all
hotwords. Override `QWEN3_ASR_MODEL`, `OMNI_ASR_MODEL`, or
`OMNI_ASR_BASE_URL` when the deployments use different names or gateways.

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
Standalone punctuation/quote fragments are excluded from TTS, including when
they arrive in a separate streaming delta after a sentence boundary. The
pre-production TTS endpoint returns an upstream-stream 502 for inputs such as
`”；`; the complete assistant text remains unchanged. TTS HTTP errors include
the upstream response body, model, voice, input length, and `x-request-id` for
tracing failures at the gateway.

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

Realtime assistant speech uses `TTS_SPEAKER` (`dongbai` by default).

When `TTS_MODEL=qwen3_tts`, tau2's seven built-in persona voice IDs map one to
one to `ryan`, `vivian`, `ono_anna`, `aiden`, `dylan`, `eric`, and `serena`.
Unknown IDs are assigned deterministically across the supported Qwen voice pool.
This mapping is independent of `TTS_SPEAKER`, which remains the fixed voice for
realtime assistant speech.

For other models, including `nex-tts-3-6-hd`, tau2's seven built-in persona
voice IDs map to `beier`, `huangyue`, `panwanting`, `wanqing`, `yaomengru`,
`yumi`, and `zz`. Unknown IDs are assigned deterministically across that pool.

Override this K8s mapping with a JSON object when needed:

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
