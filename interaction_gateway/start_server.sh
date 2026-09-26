export ASR_BASE_URL=http://api-cybotforge-pre.brapp.com/v1
export ASR_API_KEY='junchao.hu-104a732c-eaa6-452f-974e-604801e371f7'
export ASR_MODEL=br-audio-waihu-v2

# export ASR_BASE_URL=http://localhost:5005/v1
# export ASR_API_KEY='EMPTY'
# export ASR_MODEL=qwen3_omni

# export SEMANTIC_ONNX_PATH=/Users/admin/Desktop/换机/git_root/agent_eval/realtime_service/turnsense_demo/models/model_fp32.onnx
export SEMANTIC_DEBUG_DIR=interaction_gateway/semantic_debug
uvicorn interaction_gateway.app:app \
  --host 127.0.0.1 --port 9010 --log-level info