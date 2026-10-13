#!/usr/bin/env bash

# This script is the container entrypoint. The TTS service is reached directly
# through its Kubernetes address; it is not started as a local sidecar.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export LOCAL_MODEL_API_KEY='junchao.hu-104a732c-eaa6-452f-974e-604801e371f7'

# export INTERACTIVE_BASE_URL=http://127.0.0.1:9010/v1
# export INTERACTIVE_MODEL=interaction-gateway
# export CLASSIFIER_TIMEOUT_S=2

# export INTERACTIVE_BASE_URL='http://127.0.0.1:9004/v1'
# export INTERACTIVE_MODEL='interactive_model'

export INTERACTIVE_BASE_URL='http://api-cybotforge-pre.brapp.com/v1'
export INTERACTIVE_MODEL='qwen2-5-omni-tauvoice'

# export ASR_BASE_URL='http://localhost:5005/v1'
# export ASR_MODEL='qwen3_omni_asr'
# export ASR_BASE_URL='http://localhost:9004/v1'
# export ASR_MODEL='qwen3_omni'
export ASR_BASE_URL='http://api-cybotforge-pre.brapp.com/v1'
# export ASR_MODEL='br-audio-waihu-v2'
# export ASR_PROMPT_MODE='qwen3_omni'

export ASR_MODEL='qwen3-asr'
export ASR_PROMPT_MODE='qwen3_asr'
export ASR_LANGUAGE='en'
# Save the exact 16 kHz mono WAV sent to ASR, grouped by realtime session.
# export ASR_DEBUG_DIR="${ASR_DEBUG_DIR:-$SCRIPT_DIR/service_app/asr_debug}"
export ASR_DEBUG_DIR="${ASR_DEBUG_DIR:-}"

# Override these in the Kubernetes Deployment. The default preserves the
# existing pre-production environment for local testing.
export TTS_BASE_URL="${TTS_BASE_URL:-http://api-cybotforge-pre.brapp.com/v1}"
export TTS_MODEL="${TTS_MODEL:-nex-tts-3-6-hd}"
export TTS_PCM_SAMPLE_RATE="${TTS_PCM_SAMPLE_RATE:-8000}"
export TTS_SPEAKER=dongbai

# export TTS_BASE_URL="${TTS_BASE_URL:-http://localhost:8091/v1}"
# export TTS_MODEL="${TTS_MODEL:-qwen3_tts}"
# export TTS_PCM_SAMPLE_RATE="${TTS_PCM_SAMPLE_RATE:-24000}"
# export TTS_SPEAKER=vivian

# export LOCAL_MODEL_API_KEY='EMPTY'
export INTERACTIVE_DEBUG_DIR=''

export TEXT_BASE_URL='http://api-cybotforge-pre.brapp.com/v1'
export TEXT_API_KEY='junchao.hu-104a732c-eaa6-452f-974e-604801e371f7'
export TEXT_MODEL='qwen3-32b-tauvoice'
# export TEXT_MODEL='br-audio-v2-mbti'

# export TEXT_BASE_URL='http://localhost:5004/v1'
# export TEXT_API_KEY='EMPTY'
# export TEXT_MODEL='qwen3_32b'
# export TEXT_BASE_URL='https://api.deepseek.com/v1'
# export TEXT_API_KEY='sk-746e7a6977dc461e9e93da2828f19331'
# export TEXT_MODEL='deepseek-v4-flash'
# export TEXT_BASE_URL='https://llm-axskkn30220h5cux.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'
# export TEXT_API_KEY='sk-39204ca0f48f4279a80846112795ed63'
# export TEXT_MODEL='qwen3.7-flash'
# export TEXT_ENABLE_THINKING=false

# export TEXT_BASE_URL='https://ark.cn-beijing.volces.com/api/v3'
# export TEXT_API_KEY='6d1f1bfd-2a7c-412d-a99c-acae0d10445a'
# export TEXT_MODEL='deepseek-v4-flash-ga-260731'
# export BACKEND_TRUST_ENV=true

export INTERACTIVE_HISTORY_MESSAGES=10

export COMPLETE_CONFIRMATIONS=1
export END_SILENCE_MS=3000
export INTERRUPT_ENABLED=true
export CLASSIFIER_WINDOW_TICKS="${CLASSIFIER_WINDOW_TICKS:-2}"
export SEMANTIC_WINDOW_MS="${SEMANTIC_WINDOW_MS:-1000}"
export INTERRUPT_WINDOW_MS=3000
export INTERRUPT_MIN_SPEECH_MS=800
export INTERRUPT_NEGATIVE_CONFIRMATIONS=2
export INTERRUPT_CONFIRMATIONS=2
export INTERRUPT_ACK=''
export TOOL_CALL_ACK='One moment while I check that.'
export INTERRUPT_FORCE_SPEECH_MS=0
export VAD_PRE_ROLL_MS=300
export TTS_REALTIME_PACING=true

exec uvicorn service_app.app:app --host 0.0.0.0 --port 8765 --log-level info
