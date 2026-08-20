#!/usr/bin/env python3
"""Freeze completed blind judgments, then validate and join the private key."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import subprocess
from pathlib import Path
from typing import Any

from political_forensics.annotations import finalize_blind_rows, primary_from_judgment
from political_forensics.storage import json_bytes, sha256_file, write_atomic_batch


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    if not rows:
        raise ValueError("No finalized rows to serialize")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def read_sidecar(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("Blinding sidecar must contain a JSON object")
    return value


def git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or None


def validate_completed_blind(rows: list[dict[str, str]], expected_count: int) -> None:
    if len(rows) != expected_count:
        raise ValueError("Completed blind sheet does not match the recorded eligible count")
    blind_ids = [str(row.get("blind_id") or "") for row in rows]
    if any(not value for value in blind_ids) or len(blind_ids) != len(set(blind_ids)):
        raise ValueError("Completed blind sheet must contain unique nonempty blind IDs")
    for row in rows:
        primary_from_judgment(row.get("content_judgment"))
        if str(row.get("condition_revealed_by_output", "")).strip().lower() not in {"yes", "no"}:
            raise ValueError("Every completed blind row must record condition_revealed_by_output")


def freeze_completed(blind_input: Path, sidecar_path: Path) -> None:
    sidecar = read_sidecar(sidecar_path)
    if sidecar.get("stage") != "discovery":
        raise ValueError("The completed-hash freeze is restricted to discovery blinding sidecars")
    if sidecar.get("completed_blind_sha256"):
        raise FileExistsError("Completed blind hash is already frozen; refusing to replace it")
    rows = read_csv(blind_input)
    validate_completed_blind(rows, int(sidecar["eligible_count"]))
    sidecar["completed_blind_sha256"] = sha256_file(blind_input)
    sidecar["completed_blind_count"] = len(rows)
    write_atomic_batch({sidecar_path: json_bytes(sidecar)}, overwrite=True)


def finalize(args: argparse.Namespace) -> None:
    if not args.key_input or not args.output or not args.finalization_sidecar_output:
        raise ValueError(
            "Finalization requires --key-input, --output, and --finalization-sidecar-output"
        )
    sidecar = read_sidecar(args.blinding_sidecar)
    completed_hash = sidecar.get("completed_blind_sha256")
    if not completed_hash:
        raise ValueError("Blinding sidecar has no frozen completed_blind_sha256")
    if sha256_file(args.blind_input) != completed_hash:
        raise ValueError("Completed blind sheet hash differs from the frozen hash")
    if sha256_file(args.key_input) != sidecar.get("private_key_sha256"):
        raise ValueError("Private key hash differs from the blinding sidecar")
    blind_rows = read_csv(args.blind_input)
    key_rows = read_csv(args.key_input)
    expected_count = int(sidecar["eligible_count"])
    validate_completed_blind(blind_rows, expected_count)
    finalized = finalize_blind_rows(
        blind_rows,
        key_rows,
        expected_eligible_count=expected_count,
        require_recorded_count=True,
    )
    output_content = csv_bytes(finalized)
    finalization_sidecar = {
        "schema_version": 1,
        "run_id": sidecar.get("run_id"),
        "stage": sidecar.get("stage"),
        "blinding_sidecar_sha256": sha256_file(args.blinding_sidecar),
        "completed_blind_sha256": completed_hash,
        "private_key_sha256": sidecar.get("private_key_sha256"),
        "final_annotations_sha256": hashlib.sha256(output_content).hexdigest(),
        "eligible_count": expected_count,
        "finalized_count": len(finalized),
        "code_git_commit": git_commit(),
    }
    write_atomic_batch(
        {
            args.output: output_content,
            args.finalization_sidecar_output: json_bytes(finalization_sidecar),
        },
        overwrite=args.overwrite,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blind-input", type=Path, required=True)
    parser.add_argument("--blinding-sidecar", type=Path, required=True)
    parser.add_argument("--freeze-completed", action="store_true")
    parser.add_argument("--key-input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--finalization-sidecar-output", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    try:
        if args.freeze_completed:
            if args.key_input:
                raise ValueError("Freeze mode does not accept or read --key-input")
            freeze_completed(args.blind_input, args.blinding_sidecar)
            print("PASS: completed blind-sheet hash frozen without reading the private key")
            return
        finalize(args)
    except (
        FileExistsError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise SystemExit(str(error)) from error
    print(f"PASS: validated annotations written to {args.output}")


if __name__ == "__main__":
    main()
