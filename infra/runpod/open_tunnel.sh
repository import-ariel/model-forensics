#!/usr/bin/env bash
# Run this on the Mac after setting the RUNPOD_SSH_* variables in an untracked .env.
set -euo pipefail

: "${RUNPOD_SSH_HOST:?Set RUNPOD_SSH_HOST from RunPod's Connect panel.}"
: "${RUNPOD_SSH_PORT:?Set RUNPOD_SSH_PORT from RunPod's Connect panel.}"
: "${RUNPOD_SSH_KEY_PATH:?Set RUNPOD_SSH_KEY_PATH to your private SSH key.}"

exec ssh -N -L 8000:127.0.0.1:8000 \
  -p "$RUNPOD_SSH_PORT" \
  -i "$RUNPOD_SSH_KEY_PATH" \
  "root@$RUNPOD_SSH_HOST"
