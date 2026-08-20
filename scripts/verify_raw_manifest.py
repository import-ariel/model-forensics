#!/usr/bin/env python3
"""Verify a pulled raw-data directory against a Pod-generated SHA-256 manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from political_forensics.storage import verify_sha256_manifest_required


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--required-path", action="append", default=[])
    args = parser.parse_args()

    failures = verify_sha256_manifest_required(
        args.raw_root, args.manifest, required_paths=args.required_path
    )

    if failures:
        raise SystemExit("Raw-data verification failed:\n" + "\n".join(failures))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    print(f"PASS: verified {len(manifest.get('files', []))} files against {args.manifest}")


if __name__ == "__main__":
    main()
