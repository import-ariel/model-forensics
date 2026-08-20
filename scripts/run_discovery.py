#!/usr/bin/env python3
"""Run the fixed exploratory grid on the Pod and retain append-only raw JSONL."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from political_forensics.client import EndpointClient
from political_forensics.config import (
    REPO_ROOT,
    load_cue,
    load_discovery_config,
    load_scenario,
    load_scoring_config,
)
from political_forensics.integrity import assess_response_integrity
from political_forensics.prompt_builder import build_messages, prompt_sha256
from political_forensics.schemas import RolloutRecord
from political_forensics.scoring import score_response
from political_forensics.storage import (
    append_jsonl,
    read_jsonl,
    sha256_file,
    sha256_manifest,
    tree_sha256,
    write_json,
)

COMMIT = re.compile(r"^[0-9a-f]{40,64}$")


def required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def code_git_commit() -> str | None:
    version_file = REPO_ROOT / ".code-version"
    if version_file.exists():
        value = version_file.read_text(encoding="utf-8").strip()
        if not COMMIT.fullmatch(value):
            raise SystemExit(f"Invalid commit in {version_file}: {value!r}")
        return value
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and COMMIT.fullmatch(value) else None


def job_key(scenario_id: str, cue_id: str, seed: int) -> tuple[str, str, int]:
    return scenario_id, cue_id, seed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/discovery.yaml"))
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path(os.getenv("POD_ARTIFACT_ROOT", "/workspace/model-forensics")),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume only jobs missing from an existing run file; reject duplicate existing keys.",
    )
    parser.add_argument(
        "--confirm-manual-pilot-reviewed",
        action="store_true",
        help="Required for a real discovery batch after inspecting the nine pilot responses.",
    )
    parser.add_argument(
        "--continue-on-invalid",
        action="store_true",
        help=(
            "Preserve responses that fail integrity checks, mark them analysis-ineligible, "
            "refresh the raw-data manifest, and continue to the next job. API/transport "
            "failures still stop the run."
        ),
    )
    args = parser.parse_args()

    config_path = args.config.resolve()
    scoring_path = (REPO_ROOT / "configs" / "scoring.yaml").resolve()
    config = load_discovery_config(config_path)
    scoring_config = load_scoring_config(scoring_path)
    jobs = [
        (scenario_id, cue_id, seed)
        for scenario_id in config.scenarios
        for cue_id in config.cues
        for seed in config.seeds
    ]
    output = args.artifact_root / "data" / "raw" / config.stage / f"{config.run_id}.jsonl"
    existing = read_jsonl(output)
    existing_keys = [
        job_key(str(row["scenario_id"]), str(row["cue_id"]), int(row["seed"])) for row in existing
    ]
    if len(existing_keys) != len(set(existing_keys)):
        raise SystemExit(f"Existing run contains duplicate job keys; quarantine and audit {output}")
    if existing and not args.resume:
        raise SystemExit(f"Run file already exists: {output}. Use --resume or choose a new run_id.")
    completed = set(existing_keys)
    remaining_jobs = [job for job in jobs if job_key(*job) not in completed]
    if args.dry_run:
        print(
            f"DRY RUN: {len(jobs)} configured, {len(completed)} completed, "
            f"{len(remaining_jobs)} remaining; raw output: {output}"
        )
        return
    if config.stage == "discovery" and not args.confirm_manual_pilot_reviewed:
        raise SystemExit(
            "Run and manually inspect configs/pilot.yaml first, then rerun with --confirm-manual-pilot-reviewed."
        )

    api_key = os.getenv("MODEL_API_KEY") or required("VLLM_API_KEY")
    environment_model_id = os.getenv("MODEL_ID", config.model_id)
    environment_revision = required("MODEL_REVISION")
    if environment_model_id != config.model_id:
        raise SystemExit(
            f"MODEL_ID {environment_model_id!r} does not match config {config.model_id!r}"
        )
    if environment_revision != config.model_revision:
        raise SystemExit(
            f"MODEL_REVISION {environment_revision!r} does not match config {config.model_revision!r}"
        )

    config_hash = sha256_file(config_path)
    scoring_hash = sha256_file(scoring_path)
    snapshot_hash = tree_sha256(REPO_ROOT, ["src", "scripts", "prompts", "configs"])
    commit = code_git_commit()
    environment_manifest_id = os.getenv("ENVIRONMENT_MANIFEST_ID")
    manifest_path = args.artifact_root / "artifacts" / "manifests" / f"{config.run_id}-run.json"
    write_json(
        manifest_path,
        {
            "run_id": config.run_id,
            "stage": config.stage,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "model_id": config.model_id,
            "model_revision": config.model_revision,
            "config_path": str(config_path),
            "config_sha256": config_hash,
            "scoring_primary_outcome": scoring_config.primary_outcome,
            "scoring_config_sha256": scoring_hash,
            "code_snapshot_sha256": snapshot_hash,
            "code_git_commit": commit,
            "environment_manifest_id": environment_manifest_id,
            "configured_jobs": len(jobs),
            "previously_completed_jobs": len(completed),
            "remaining_jobs": len(remaining_jobs),
            "sampling": config.sampling.model_dump(),
        },
    )

    client = EndpointClient(os.getenv("MODEL_BASE_URL", "http://127.0.0.1:8000/v1"), api_key)
    for scenario_id, cue_id, seed in remaining_jobs:
        scenario = load_scenario(scenario_id)
        messages = build_messages(scenario, load_cue(cue_id), option_order=config.option_order)
        payload = {
            "model": config.model_id,
            "messages": messages,
            "seed": seed,
            "chat_template_kwargs": config.chat_template_kwargs,
            **config.sampling.model_dump(),
        }
        raw_response, latency_seconds, retry_count = client.chat(payload)
        response_model = raw_response.get("model")
        if response_model and response_model != config.model_id:
            raise SystemExit(
                f"Endpoint returned model {response_model!r}; expected {config.model_id!r}"
            )
        message = (raw_response.get("choices") or [{}])[0].get("message") or {}
        final_text = message.get("content") or ""
        response_integrity = assess_response_integrity(raw_response)
        record = RolloutRecord.model_validate(
            {
                "run_id": config.run_id,
                "stage": config.stage,
                "request_id": str(uuid.uuid4()),
                "captured_at_utc": datetime.now(UTC).isoformat(),
                "scenario_id": scenario_id,
                "cue_id": cue_id,
                "seed": seed,
                "model_id": config.model_id,
                "model_revision": config.model_revision,
                "config_sha256": config_hash,
                "scoring_config_sha256": scoring_hash,
                "code_snapshot_sha256": snapshot_hash,
                "code_git_commit": commit,
                "environment_manifest_id": environment_manifest_id,
                "request": payload,
                "raw_response": raw_response,
                "prompt_sha256": prompt_sha256(messages),
                "latency_seconds": latency_seconds,
                "retry_count": retry_count,
                "response_integrity": response_integrity,
                "derived_score": score_response(scenario, final_text, cue_id=cue_id),
            }
        ).model_dump(mode="json")
        append_jsonl(output, record)
        print(f"saved {scenario_id=} {cue_id=} {seed=} to {output}")
        if not response_integrity["analysis_eligible"]:
            sha256_manifest(
                args.artifact_root / "data" / "raw",
                args.artifact_root / "artifacts" / "manifests" / f"{config.run_id}-raw-sha256.json",
            )
            reasons = ", ".join(response_integrity["invalid_reasons"])
            if args.continue_on_invalid:
                print(
                    "WARNING: Invalid response preserved as analysis-ineligible; "
                    f"continuing to next job: {reasons}",
                    flush=True,
                )
                continue
            raise SystemExit(
                f"Invalid response preserved; manifest generated; stopping run: {reasons}"
            )

    sha256_manifest(
        args.artifact_root / "data" / "raw",
        args.artifact_root / "artifacts" / "manifests" / f"{config.run_id}-raw-sha256.json",
    )


if __name__ == "__main__":
    main()
