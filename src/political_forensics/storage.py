"""Append-only, pod-first rollout persistence."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSON in {path} at line {line_number}") from error
            if not isinstance(value, dict):
                raise TypeError(f"Expected an object in {path} at line {line_number}")
            rows.append(value)
    return rows


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_sha256(root: Path, relative_roots: Iterable[str]) -> str:
    """Hash load-bearing source, prompt, script, and config files deterministically."""
    files: list[Path] = []
    for relative_root in relative_roots:
        candidate = root / relative_root
        if candidate.is_file():
            files.append(candidate)
            continue
        files.extend(
            path
            for path in candidate.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
        )
    digest = hashlib.sha256()
    for path in sorted(set(files)):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def json_bytes(value: dict[str, Any]) -> bytes:
    """Serialize a JSON object canonically for hashing and atomic publication."""
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_atomic_batch(files: dict[Path, bytes], *, overwrite: bool = False) -> None:
    """Publish a set of already-validated files without leaving partial new outputs.

    Temporary files are created beside their destinations so each promotion is an
    atomic rename. If promotion fails, newly-created destinations are removed and
    overwritten destinations are restored from same-filesystem backups.
    """
    if not files:
        return
    destinations = list(files)
    if len(set(destinations)) != len(destinations):
        raise ValueError("Atomic output destinations must be unique")
    existing = [path for path in destinations if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(f"Refusing to overwrite {existing[0]}")

    temporaries: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    promoted: list[Path] = []
    try:
        for destination, content in files.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
            )
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            temporaries[destination] = temporary
        for destination in existing:
            descriptor, backup_name = tempfile.mkstemp(
                prefix=f".{destination.name}.", suffix=".bak", dir=destination.parent
            )
            os.close(descriptor)
            backup = Path(backup_name)
            backup.unlink()
            backups[destination] = backup
            destination.replace(backup)
        for destination, temporary in temporaries.items():
            temporary.replace(destination)
            promoted.append(destination)
        for backup in backups.values():
            backup.unlink(missing_ok=True)
    except Exception:
        for destination in reversed(promoted):
            destination.unlink(missing_ok=True)
        for destination, backup in backups.items():
            if backup.exists():
                backup.replace(destination)
        raise
    finally:
        for temporary in temporaries.values():
            temporary.unlink(missing_ok=True)
        for backup in backups.values():
            backup.unlink(missing_ok=True)


def sha256_manifest(root: Path, output: Path) -> None:
    files = sorted(path for path in root.rglob("*") if path.is_file() and path != output)
    rows = []
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(
            {"path": str(path.relative_to(root)), "sha256": digest, "bytes": path.stat().st_size}
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"files": rows}, indent=2) + "\n", encoding="utf-8")


def verify_sha256_manifest(raw_root: Path, manifest_path: Path) -> list[str]:
    """Return structured, content-free manifest verification failures.

    This deliberately validates manifest syntax before following any listed path.
    """
    return verify_sha256_manifest_required(raw_root, manifest_path)


def verify_sha256_manifest_required(
    raw_root: Path, manifest_path: Path, *, required_paths: Iterable[str] = ()
) -> list[str]:
    """Verify a nonempty manifest and require exact relative files when requested."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"invalid manifest: {type(error).__name__}"]
    if (
        not isinstance(manifest, dict)
        or not isinstance(manifest.get("files"), list)
        or not manifest["files"]
    ):
        return ["manifest must contain a nonempty files list"]
    root = raw_root.resolve()
    failures: list[str] = []
    seen: set[str] = set()
    for index, row in enumerate(manifest["files"]):
        if not isinstance(row, dict):
            failures.append(f"entry {index}: not an object")
            continue
        relative, size, digest = row.get("path"), row.get("bytes"), row.get("sha256")
        if (
            not isinstance(relative, str)
            or not relative
            or Path(relative).is_absolute()
            or "\\" in relative
        ):
            failures.append(f"entry {index}: invalid relative path")
            continue
        candidate = Path(relative)
        if any(part in {"", ".", ".."} for part in candidate.parts):
            failures.append(f"entry {index}: invalid relative path")
            continue
        normalized = candidate.as_posix()
        if normalized in seen:
            failures.append(f"duplicate manifest path: {normalized}")
            continue
        seen.add(normalized)
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            failures.append(f"entry {index}: invalid bytes")
            continue
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            failures.append(f"entry {index}: invalid sha256")
            continue
        path = (root / candidate).resolve()
        if root not in path.parents:
            failures.append(f"entry {index}: path escapes raw root")
            continue
        if not path.is_file():
            failures.append(f"missing: {normalized}")
            continue
        if path.stat().st_size != size:
            failures.append(f"size mismatch: {normalized}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            failures.append(f"hash mismatch: {normalized}")
    for required in required_paths:
        if required not in seen:
            failures.append(f"required path absent from manifest: {required}")
    return failures
