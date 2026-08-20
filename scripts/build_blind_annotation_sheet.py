#!/usr/bin/env python3
"""Build a randomized cue-blinded annotation sheet and a separate private key."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import subprocess
from pathlib import Path

from political_forensics.analysis import (
    audit_job_keys,
    expected_job_keys,
    validate_scientific_metadata,
)
from political_forensics.annotations import build_blind_rows, eligible_records
from political_forensics.config import load_analysis_config, load_discovery_config, load_scenario
from political_forensics.storage import (
    json_bytes,
    read_jsonl,
    sha256_file,
    verify_sha256_manifest_required,
    write_atomic_batch,
)

ALLOWED_JUDGMENTS = "accurately_presented | downplayed | omitted | unrateable"


def csv_bytes(rows: list[dict[str, object]], fieldnames: list[str] | None = None) -> bytes:
    if not rows and not fieldnames:
        raise ValueError("No rows or fieldnames to serialize")
    output = io.StringIO(newline="")
    fields = fieldnames or list(rows[0])
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True, help="Raw rollout JSONL")
    parser.add_argument("--blind-output", type=Path, required=True)
    parser.add_argument("--key-output", type=Path, required=True)
    parser.add_argument("--exclusions-output", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--analysis-config", type=Path)
    parser.add_argument("--scoring-config", type=Path, default=Path("configs/scoring.yaml"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--raw-root", type=Path)
    parser.add_argument("--sidecar-output", type=Path)
    parser.add_argument("--randomization-seed", type=int)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    outputs = [args.blind_output, args.key_output]
    if args.exclusions_output:
        outputs.append(args.exclusions_output)
    if args.sidecar_output:
        outputs.append(args.sidecar_output)
    for path in outputs:
        if path.exists() and not args.overwrite:
            raise SystemExit(f"Refusing to overwrite {path}; pass --overwrite intentionally")

    records = read_jsonl(args.input)
    discovery_records = [row for row in records if row.get("stage") == "discovery"]
    if discovery_records and len(discovery_records) != len(records):
        raise SystemExit("Discovery blinding accepts records from one stage only")
    randomization_seed = (
        args.randomization_seed if args.randomization_seed is not None else 20260815
    )
    if discovery_records:
        if not all(row.get("run_id") == discovery_records[0].get("run_id") for row in records):
            raise SystemExit("Discovery blinding requires one run ID")
        if (
            not args.config
            or not args.analysis_config
            or not args.exclusions_output
            or not args.manifest
            or not args.raw_root
            or not args.sidecar_output
        ):
            raise SystemExit(
                "Discovery blinding requires config, analysis config, scoring config, manifest, "
                "raw root, exclusions output, and sidecar output"
            )
    if args.config:
        config = load_discovery_config(args.config)
        if config.stage == "discovery":
            analysis_config = load_analysis_config(args.analysis_config)
            if analysis_config.run_id != config.run_id:
                raise SystemExit(
                    "Analysis configuration run_id does not match discovery configuration"
                )
            grid = audit_job_keys(records, expected_job_keys(config))
            if not grid["complete"]:
                raise SystemExit("Discovery blinding requires the complete configured grid")
            try:
                relative = args.input.resolve().relative_to(args.raw_root.resolve()).as_posix()
            except ValueError as error:
                raise SystemExit("Discovery input must be within --raw-root") from error
            failures = verify_sha256_manifest_required(
                args.raw_root, args.manifest, required_paths=[relative]
            )
            if failures:
                raise SystemExit(
                    "Discovery raw manifest verification failed: " + "; ".join(failures)
                )
            provenance = validate_scientific_metadata(
                records,
                config,
                config_sha256=sha256_file(args.config),
                scoring_sha256=sha256_file(args.scoring_config),
                analysis_config=analysis_config,
            )
            if not provenance["valid"]:
                raise SystemExit(
                    "Discovery scientific provenance failed: " + "; ".join(provenance["failures"])
                )
            if args.randomization_seed is not None and (
                args.randomization_seed != analysis_config.blind_randomization_seed
            ):
                raise SystemExit("Discovery randomization seed must equal the frozen analysis seed")
            randomization_seed = analysis_config.blind_randomization_seed
    eligible, exclusions = eligible_records(records)
    if not eligible:
        raise SystemExit("No analysis-eligible records are available for blinded review")
    blind_rows, key_rows = build_blind_rows(eligible, load_scenario, randomization_seed)
    for row in key_rows:
        row["eligible_count_recorded"] = len(eligible)
    blind_content = csv_bytes(blind_rows)
    key_content = csv_bytes(key_rows)
    exclusion_fields = [
        "request_id",
        "scenario_id",
        "cue_id",
        "seed",
        "finish_reason",
        "invalid_reasons",
    ]
    exclusions_content = csv_bytes(exclusions, exclusion_fields)
    files = {args.blind_output: blind_content, args.key_output: key_content}
    if args.exclusions_output:
        files[args.exclusions_output] = exclusions_content
    if args.sidecar_output:
        sidecar = {
            "schema_version": 1,
            "run_id": records[0].get("run_id"),
            "stage": records[0].get("stage"),
            "raw_manifest_sha256": sha256_file(args.manifest) if args.manifest else None,
            "discovery_config_sha256": sha256_file(args.config) if args.config else None,
            "scoring_config_sha256": sha256_file(args.scoring_config),
            "analysis_config_sha256": sha256_file(args.analysis_config)
            if args.analysis_config
            else None,
            "blank_blind_sha256": hashlib.sha256(blind_content).hexdigest(),
            "completed_blind_sha256": None,
            "private_key_sha256": hashlib.sha256(key_content).hexdigest(),
            "exclusions_sha256": hashlib.sha256(exclusions_content).hexdigest(),
            "eligible_count": len(eligible),
            "excluded_count": len(exclusions),
            "randomization_seed": randomization_seed,
            "code_git_commit": git_commit(),
        }
        files[args.sidecar_output] = json_bytes(sidecar)
    try:
        write_atomic_batch(files, overwrite=args.overwrite)
    except FileExistsError as error:
        raise SystemExit(str(error)) from error
    print(f"Wrote {len(blind_rows)} blinded rows to {args.blind_output}")
    print(f"Eligible: {len(eligible)}; excluded for integrity: {len(exclusions)}")
    print(f"Keep {args.key_output} closed until judgments are frozen")
    print(f"Allowed content_judgment values: {ALLOWED_JUDGMENTS}")


if __name__ == "__main__":
    main()
