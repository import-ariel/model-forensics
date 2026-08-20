#!/usr/bin/env bash
# Run this on the RunPod Pod, inside tmux. It intentionally binds only to loopback;
# access it from the Mac through infra/runpod/open_tunnel.sh.
set -euo pipefail

: "${MODEL_ID:=Qwen/Qwen3.5-9B}"
: "${MODEL_REVISION:?Set MODEL_REVISION to the full pinned Hugging Face commit SHA.}"
if [[ -z "${VLLM_API_KEY:-}" && -n "${MODEL_API_KEY:-}" ]]; then
  VLLM_API_KEY="$MODEL_API_KEY"
fi
: "${VLLM_API_KEY:?Set VLLM_API_KEY to a newly generated random value.}"
: "${MAX_MODEL_LEN:=16384}"
: "${GPU_MEMORY_UTILIZATION:=0.90}"

exec vllm serve "$MODEL_ID" \
  --revision "$MODEL_REVISION" \
  --host 127.0.0.1 \
  --port 8000 \
  --tensor-parallel-size 1 \
  --max-model-len "$MAX_MODEL_LEN" \
  --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
  --reasoning-parser qwen3 \
  --language-model-only \
  --api-key "$VLLM_API_KEY"
