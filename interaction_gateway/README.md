# Interaction compatibility gateway

This gateway keeps `service_app` on its existing OpenAI-compatible interaction
endpoint while routing semantic-completeness requests to the local TurnSense
ONNX model and ASR requests to a separate upstream service.

## Start the gateway

From the repository root:

```bash
python -m pip install -r interaction_gateway/requirements.txt

export ASR_BASE_URL=http://api-cybotforge-pre.brapp.com/v1
export ASR_API_KEY='replace-me'
export ASR_MODEL=br-audio-waihu-v2

uvicorn interaction_gateway.app:app \
  --host 127.0.0.1 --port 9010 --log-level info
```

The semantic model and CMVN paths default to the files under
`turnsense_demo/models`. Optional settings:

```text
SEMANTIC_ONNX_PATH=/absolute/path/to/model_int8.onnx
SEMANTIC_CMVN_PATH=/absolute/path/to/am.mvn
SEMANTIC_USE_CUDA=false
SEMANTIC_CONCURRENCY=1
SEMANTIC_INVALID_LABEL=incomplete
SEMANTIC_DEBUG_DIR=/path/to/semantic_debug
ASR_TIMEOUT_S=30
GATEWAY_TRUST_ENV=false
```

`SEMANTIC_INVALID_LABEL=incomplete` prevents invalid/non-target audio from
prematurely completing a turn while preserving the realtime service's existing
two-label contract.

Semantic requests are saved by default under `interaction_gateway/semantic_debug`.
Each request produces a timestamped WAV whose filename includes the raw model label,
and `timeline.jsonl` records that value as `label`. The possibly mapped API result is
recorded separately as `returned_label`. Set `SEMANTIC_DEBUG_DIR` to another
directory, or set it to an empty string to disable recording.

## Point the realtime service at the gateway

Keep the realtime service's own model key unchanged and configure only its
interaction endpoint:

```bash
export INTERACTIVE_BASE_URL=http://127.0.0.1:9010/v1
export INTERACTIVE_MODEL=interaction-gateway
export INTERRUPT_ENABLED=false
export COMPLETE_CONFIRMATIONS=1
export CLASSIFIER_TIMEOUT_S=2
```

Then start `service_app` normally. Interrupt requests are defensively answered
with `ignorable`, but disabling interruption prevents user audio from stopping
an active assistant response.
