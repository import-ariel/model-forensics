#!/usr/bin/env bash
# Run on the Mac. Sync code/configuration without touching Pod-side raw artifacts.
set -euo pipefail

: "${RUNPOD_SSH_HOST:?Set RUNPOD_SSH_HOST from RunPod's Connect panel.}"
: "${RUNPOD_SSH_PORT:?Set RUNPOD_SSH_PORT from RunPod's Connect panel.}"
: "${RUNPOD_SSH_KEY_PATH:?Set RUNPOD_SSH_KEY_PATH to your private SSH key.}"
: "${RUNPOD_PROJECT_ROOT:=/workspace/model-forensics}"

[[ -f pyproject.toml && -d src && -d .git ]] || {
  echo "Run this script from the initialized repository root." >&2
  exit 1
}

code_commit="$(git rev-parse HEAD)"
[[ "$code_commit" =~ ^[0-9a-f]{40,64}$ ]] || {
  echo "Could not resolve a valid Git commit." >&2
  exit 1
}
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  echo "Refusing to sync an uncommitted tree. Commit or intentionally ignore changes first." >&2
  exit 1
fi

ssh -p "$RUNPOD_SSH_PORT" -i "$RUNPOD_SSH_KEY_PATH" "root@$RUNPOD_SSH_HOST" \
  "mkdir -p '$RUNPOD_PROJECT_ROOT'"

rsync -az \
  --no-owner \
  --no-group \
  --exclude '.DS_Store' \
  --exclude '.env' \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude '.ruff_cache/' \
  --exclude 'artifacts/' \
  --exclude 'data/raw/' \
  --exclude 'tmp/' \
  --exclude 'model-forensics/' \
  --exclude '*.pdf' \
  --exclude '*.zip' \
  -e "ssh -p $RUNPOD_SSH_PORT -i $RUNPOD_SSH_KEY_PATH" \
  ./ "root@$RUNPOD_SSH_HOST:$RUNPOD_PROJECT_ROOT/"

ssh -p "$RUNPOD_SSH_PORT" -i "$RUNPOD_SSH_KEY_PATH" "root@$RUNPOD_SSH_HOST" \
  "printf '%s\n' '$code_commit' > '$RUNPOD_PROJECT_ROOT/.code-version'"

echo "Synced committed code snapshot $code_commit"
