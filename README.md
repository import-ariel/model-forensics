# Model Forensics Experiment

This is the minimal, runnable package for repeating the preregistered discovery
experiment on whether identity framing changes how Qwen3.5-9B weighs evidence.
It contains the fixed prompts, three scenarios, three cues, seeds, sampling
contract, rollout client, integrity checks, and blind-annotation workflow. Raw
model responses are intentionally excluded because they may contain provider
reasoning traces.

## Reproduce collection

Install the locked Python environment:

```bash
uv sync --extra dev
```

Start with the nine-response pilot, inspect and blind-annotate it, then run the
108-response discovery grid. The full secure RunPod/vLLM setup, including the
required model revision and private SSH tunnel, is documented in
[`infra/runpod/README.md`](infra/runpod/README.md).

The central commands on the Pod are:

```bash
PYTHONPATH=src python scripts/run_discovery.py --config configs/pilot.yaml
PYTHONPATH=src python scripts/run_discovery.py --confirm-manual-pilot-reviewed
```

Use `--dry-run` first. Collection requires `MODEL_REVISION` to match the pinned
revision in the selected configuration and writes append-only JSONL plus a
SHA-256 manifest beneath `POD_ARTIFACT_ROOT`.

## Included scope

- `configs/`, `prompts/`: frozen experimental contract
- `src/political_forensics/`: request construction, scoring, and integrity logic
- `scripts/`: rollout, smoke-test, manifest verification, and annotation steps
- `infra/runpod/`: private serving, sync, and result-pull instructions
