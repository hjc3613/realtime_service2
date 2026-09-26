export TTS_UPSTREAM_URL="http://api-cybotforge-pre.brapp.com/v1"
export TTS_UPSTREAM_API_KEY="junchao.hu-104a732c-eaa6-452f-974e-604801e371f7"
export TTS_UPSTREAM_MODEL="nex-tts-3-6-hd"
# These must describe the raw PCM returned by the company service.
export TTS_DEFAULT_SAMPLE_RATE="8000"
export TTS_PCM_CHANNELS="1"
export TTS_PCM_BITS_PER_SAMPLE="16"
python3 openai_compat_proxy.py