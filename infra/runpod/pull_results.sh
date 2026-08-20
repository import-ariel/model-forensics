#!/usr/bin/env bash
# Run on the Mac. Pull all raw forensic evidence and verify Pod-generated hashes.
set -euo pipefail

: "${RUNPOD_SSH_HOST:?Set RUNPOD_SSH_HOST from RunPod's Connect panel.}"
: "${RUNPOD_SSH_PORT:?Set RUNPOD_SSH_PORT from RunPod's Connect panel.}"
: "${RUNPOD_SSH_KEY_PATH:?Set RUNPOD_SSH_KEY_PATH to your private SSH key.}"
: "${RUNPOD_PROJECT_ROOT:=/workspace/model-forensics}"

[[ -f plan.md && -d src ]] || {
  echo "Run this script from the repository root." >&2
  exit 1
}

mkdir -p data/raw artifacts/manifests

rsync -az \
  --no-owner \
  --no-group \
  -e "ssh -p $RUNPOD_SSH_PORT -i $RUNPOD_SSH_KEY_PATH" \
  "root@$RUNPOD_SSH_HOST:$RUNPOD_PROJECT_ROOT/data/raw/" data/raw/

rsync -az \
  --no-owner \
  --no-group \
  --include '*-raw-sha256.json' \
  --include '*-run.json' \
  --include '*-summary.json' \
  --exclude '*' \
  -e "ssh -p $RUNPOD_SSH_PORT -i $RUNPOD_SSH_KEY_PATH" \
  "root@$RUNPOD_SSH_HOST:$RUNPOD_PROJECT_ROOT/artifacts/manifests/" artifacts/manifests/

found_manifest=0
final_discovery_manifest="artifacts/manifests/discovery_v1-raw-sha256.json"
for manifest in artifacts/manifests/*-raw-sha256.json; do
  [[ -e "$manifest" ]] || continue
  found_manifest=1
  if [[ "$manifest" == *-checkpoint-* ]]; then
    echo "ARCHIVED, NOT FINAL-STATE VERIFIED: $manifest"
    continue
  fi
  python scripts/verify_raw_manifest.py --raw-root data/raw --manifest "$manifest"
done

if [[ "$found_manifest" -ne 1 ]]; then
  echo "No raw-data hash manifest was pulled; do not delete the Pod." >&2
  exit 1
fi

if [[ ! -f "$final_discovery_manifest" ]]; then
  echo "Missing canonical final discovery manifest: $final_discovery_manifest" >&2
  exit 1
fi
python scripts/verify_raw_manifest.py \
  --raw-root data/raw \
  --manifest "$final_discovery_manifest" \
  --required-path discovery/discovery_v1.jsonl

echo "PASS: full raw data and manifests are present and verified on the Mac."
