"""Build deterministic Phase 2A Facts artifacts without network access."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.services.atomic_directory import atomic_publish_directory
from app.services.phase2_facts import (
    FACTS_SCHEMA_VERSION,
    FIXED_SYMBOLS,
    SNAPSHOT_MANIFEST,
    TRADE_DATE,
    Phase2FactsError,
    build_symbol_facts,
    canonical_json_bytes,
    validate_facts_document,
)


def _facts_filename(symbol: str) -> str:
    return f"{symbol.replace('.', '')}_facts.json"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _source_set_hash(source_files: list[dict[str, str]]) -> str:
    encoded = json.dumps(
        source_files,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_durable(path: Path, payload: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _publish_directory(staging: Path, output_dir: Path) -> None:
    if output_dir.is_symlink():
        raise Phase2FactsError(f"output directory must not be a symlink: {output_dir}")
    if output_dir.exists() and not output_dir.is_dir():
        raise Phase2FactsError(f"output path is not a directory: {output_dir}")

    atomic_publish_directory(staging, output_dir)


def build_facts_directory(repo_root: Path, output_dir: Path) -> dict[str, Any]:
    """Build all fixed-scope Facts and atomically publish a complete directory."""
    repo_root = repo_root.resolve(strict=True)
    output_dir = output_dir.expanduser()
    if output_dir.is_symlink():
        raise Phase2FactsError(f"output directory must not be a symlink: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    output_dir = output_dir.parent.resolve(strict=True) / output_dir.name

    encoded_facts: dict[str, bytes] = {}
    all_sources: dict[str, str] = {}
    symbol_status: dict[str, str] = {}
    for symbol in FIXED_SYMBOLS:
        facts = build_symbol_facts(repo_root, symbol)
        errors = validate_facts_document(repo_root, facts)
        if errors:
            raise Phase2FactsError(f"Facts validation failed for {symbol}: {errors}")
        filename = _facts_filename(symbol)
        encoded_facts[filename] = canonical_json_bytes(facts)
        symbol_status[symbol] = "VALID"
        for domain in facts["source_evidence"].values():
            for item in domain.get("source_files", []):
                previous = all_sources.setdefault(item["path"], item["sha256"])
                if previous != item["sha256"]:
                    raise Phase2FactsError(f"source hash conflict: {item['path']}")

    source_files = [
        {"path": path, "sha256": sha256}
        for path, sha256 in sorted(all_sources.items())
    ]
    output_hashes = {
        filename: _sha256_bytes(payload) for filename, payload in sorted(encoded_facts.items())
    }
    manifest: dict[str, Any] = {
        "status": "FACTS_VALID",
        "facts_schema_version": FACTS_SCHEMA_VERSION,
        "trade_date": TRADE_DATE,
        "symbols": symbol_status,
        "content_readiness": "READY_FOR_AI_REVIEW",
        "missing_source_inputs": [],
        "source_type": "existing_cloud_store_snapshot",
        "source_snapshot_manifest": SNAPSHOT_MANIFEST,
        "source_files": source_files,
        "source_set_sha256": _source_set_hash(source_files),
        "output_hashes": output_hashes,
        "new_tickflow_api_request_count": 0,
        "cloud_mutation_count": 0,
        "ai_calls": 0,
        "build_mode": "offline_existing_cloud_evidence",
    }

    staging = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.staging-", dir=output_dir.parent)
    )
    try:
        for filename, payload in sorted(encoded_facts.items()):
            _write_durable(staging / filename, payload)
        _write_durable(staging / "build_manifest.json", canonical_json_bytes(manifest))
        directory_handle = os.open(staging, os.O_RDONLY)
        try:
            os.fsync(directory_handle)
        finally:
            os.close(directory_handle)
        _publish_directory(staging, output_dir)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = build_facts_directory(args.repo_root, args.output_dir)
    except (OSError, Phase2FactsError, ValueError) as exc:
        print(json.dumps({"status": "FACTS_INVALID", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
