# RunPod + vLLM setup

This experiment uses a temporary RunPod **on-demand Pod** as a private inference
server. The Mac remains the source of truth for code and configurations; the
Pod's persistent `/workspace/model-forensics` stores full raw outputs, manifests,
and generated analysis artifacts.

## 1. Account and Pod

1. Create a RunPod account, add billing, and add an SSH public key in the console.
2. Create an on-demand Pod with one 48 GB GPU (prefer A40 or RTX A6000), a current
   CUDA/PyTorch template, and 80--100 GB of container disk.
3. Do **not** expose port 8000 publicly. Use the SSH connection details shown in
   RunPod's Connect panel instead.
4. Record Pod GPU, region, hourly price, CUDA driver, and start time in
   `artifacts/manifests/compute.md` on the Mac.

## 2. Pin and serve the model (on the Pod)

Open the Qwen repository's commit history, copy the full SHA, then start a
persistent terminal:

```bash
tmux new -s qwen
python -m venv /root/bootstrap-venv
source /root/bootstrap-venv/bin/activate
python -m pip install --upgrade uv

# Qwen3.5 text-only support was added in vLLM 0.27. Pin the official CUDA
# 12.9 wheel: the generic/nightly index can select a CUDA 13-linked binary.
uv venv --python 3.12 --seed /root/vllm-0.27.1-cu129
uv pip install \
  --python /root/vllm-0.27.1-cu129/bin/python \
  'https://github.com/vllm-project/vllm/releases/download/v0.27.1/vllm-0.27.1%2Bcu129-cp38-abi3-manylinux_2_28_x86_64.whl' \
  --extra-index-url https://download.pytorch.org/whl/cu129 \
  --index-strategy unsafe-best-match
source /root/vllm-0.27.1-cu129/bin/activate

export MODEL_ID=Qwen/Qwen3.5-9B
export MODEL_REVISION=<full-commit-sha>
export VLLM_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
umask 077
printf 'MODEL_ID=%s\nMODEL_REVISION=%s\nVLLM_API_KEY=%s\n' \
  "$MODEL_ID" "$MODEL_REVISION" "$VLLM_API_KEY" > /root/model-forensics.env
chmod 600 /root/model-forensics.env
bash infra/runpod/start_vllm.sh
```

Keep the virtual environment under `/root` on this template. `/workspace` is a
FUSE-backed network filesystem and Python imports from a venv there can be very
slow. Model weights can still use an `HF_HOME` under `/workspace`.

The API key is not a RunPod credential. It is a newly generated password for this
private vLLM server; copy it into the Mac's untracked `.env` file. Also store the
Pod-side model variables in a root-owned file that is never synchronized or
committed, as shown above, so a separate rollout shell can recover the exact
values.

## 3. Tunnel it privately (on the Mac)

```bash
cp .env.example .env
# Edit .env with the API key, revision, and values from RunPod's Connect panel.
set -a; source .env; set +a
bash infra/runpod/open_tunnel.sh
```

Keep that terminal open. In a second terminal, the endpoint is available only at
`http://127.0.0.1:8000/v1`. Use the tunnel for smoke tests and interactive
inspection; run large rollout batches on the Pod so raw responses stay in
`/workspace/model-forensics`.

## 4. Verify before collecting research data

```bash
set -a; source .env; set +a
python scripts/smoke_test.py --output-dir artifacts/smoke
python scripts/smoke_test.py --output-dir artifacts/smoke --full-length
```

The second command permits a 2,048-token output and can take a few minutes. Do
not begin experimental data collection unless the saved raw JSON includes a final
answer and a separate `reasoning_content` field (or an explicitly retained
provider-specific equivalent).

## 5. Sync and run rollout batches on the Pod

On the Mac, sync only the code and configurations (the helper deliberately never
overwrites Pod-side raw data):

```bash
set -a; source .env; set +a
bash infra/runpod/sync_project.sh
```

Then run the nine-response prompt-validation pilot on the Pod:

```bash
cd /workspace/model-forensics
source /root/vllm-0.27.1-cu129/bin/activate
set -a; source /root/model-forensics.env; set +a
export POD_ARTIFACT_ROOT=/workspace/model-forensics
export MODEL_BASE_URL=http://127.0.0.1:8000/v1
export MODEL_API_KEY="$VLLM_API_KEY"
PYTHONPATH=src python scripts/run_discovery.py --config configs/pilot.yaml --dry-run
PYTHONPATH=src python scripts/run_discovery.py --config configs/pilot.yaml
```

The pilot is one scenario × three cues × three seeds. Pull all nine responses to
the Mac and create a cue-blinded annotation sheet:

```bash
set -a; source .env; set +a
bash infra/runpod/pull_results.sh
PYTHONPATH=src python scripts/build_blind_annotation_sheet.py \
  --input data/raw/pilot/prompt_pilot_v1.jsonl \
  --blind-output data/annotations/prompt_pilot_v1-blind.csv \
  --key-output data/annotations/prompt_pilot_v1-key.csv
```

Open only the blind sheet until every `content_judgment` is frozen. Manually
inspect all nine final answers before any full batch. The pilot must show that the
model faces a natural identity-conditioned evidentiary choice; a missing
bracketed citation alone is not a finding. Only then run the 108-response
discovery grid:

```bash
PYTHONPATH=src python scripts/finalize_annotations.py \
  --blind-input data/annotations/prompt_pilot_v1-blind.csv \
  --key-input data/annotations/prompt_pilot_v1-key.csv \
  --output data/annotations/prompt_pilot_v1-final.csv
```

Only open the finalized cue-linked file after the command validates that every
judgment was completed. If the gate passes, run the discovery grid on the Pod:

```bash
PYTHONPATH=src python scripts/run_discovery.py --dry-run
PYTHONPATH=src python scripts/run_discovery.py --confirm-manual-pilot-reviewed
```

The default behavior is to preserve an integrity-invalid response and stop for
review. For a pre-authorized exploratory batch, add `--continue-on-invalid` to
preserve the response as `analysis_eligible: false`, refresh the raw-data hash
manifest, and continue. This flag does not suppress API or transport failures.
Resume an existing append-only batch with both explicit flags:

```bash
PYTHONPATH=src python scripts/run_discovery.py \
  --resume \
  --continue-on-invalid \
  --confirm-manual-pilot-reviewed
```

If a rollout command is interrupted, rerun the same command with `--resume`.
Without `--resume`, the runner refuses to append to an existing run file.

Raw JSONL and SHA-256 manifests are written beneath `POD_ARTIFACT_ROOT`, under
separate `data/raw/pilot` and `data/raw/discovery` directories.

## 6. Stop billing

Run `infra/runpod/pull_results.sh` and confirm that it verifies every raw JSONL
against the Pod-generated manifests. Retain the full raw backup outside Git.
Only then stop vLLM, terminate the Pod in the RunPod console, and delete the Pod
volume. Stopping vLLM alone does not stop Pod billing.
