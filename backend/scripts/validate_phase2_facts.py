"""Validate deterministic Phase 2A Facts artifacts and their source hashes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from app.services.phase2_facts import (
    FACTS_SCHEMA_VERSION,
    FIXED_SYMBOLS,
    MISSING_SOURCE_INPUTS,
    TRADE_DATE,
    Phase2FactsError,
    validate_facts_document,
)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise Phase2FactsError(f"artifact must be a regular file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant)
    if not isinstance(value, dict):
        raise Phase2FactsError(f"artifact must contain a JSON object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_set_hash(source_files: list[dict[str, str]]) -> str:
    encoded = json.dumps(
        source_files,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validation_metrics(errors: dict[str, list[str]]) -> dict[str, int]:
    flattened = [error for group in errors.values() for error in group]
    unsupported_prefixes = (
        "numeric_provenance_missing:",
        "numeric_provenance_value_mismatch:",
        "numeric_provenance_source_invalid:",
        "numeric_provenance_source_mismatch:",
        "numeric_provenance_kind_invalid:",
    )
    return {
        "unsupported_numeric_claims": sum(
            error.startswith(unsupported_prefixes) for error in flattened
        ),
        "non_finite_numbers": sum(
            error.startswith("non_finite_number:") for error in flattened
        ),
        "source_hash_mismatches": sum(
            error == "source_hash_mismatch" for error in flattened
        ),
        "secret_hits": sum(error.startswith("secret_field:") for error in flattened),
        "trading_field_hits": sum(
            error.startswith(("forbidden_trading_field:", "forbidden_trading_text:"))
            for error in flattened
        ),
    }


def validate_facts_directory(repo_root: Path, facts_dir: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve(strict=True)
    facts_dir = facts_dir.resolve(strict=True)
    expected_fact_files = {
        f"{symbol.replace('.', '')}_facts.json": symbol for symbol in FIXED_SYMBOLS
    }
    expected_files = set(expected_fact_files) | {"build_manifest.json"}
    actual_files = {path.name for path in facts_dir.iterdir() if path.is_file()}
    top_level_non_files = [path.name for path in facts_dir.iterdir() if not path.is_file()]

    directory_errors: list[str] = []
    if facts_dir.is_symlink():
        directory_errors.append("facts_directory_is_symlink")
    if actual_files != expected_files or top_level_non_files:
        directory_errors.append("artifact_file_set_invalid")

    try:
        manifest = _load_json(facts_dir / "build_manifest.json")
    except (OSError, ValueError, Phase2FactsError) as exc:
        errors = {"directory": [f"manifest_invalid:{exc}"]}
        return {
            "status": "FACTS_INVALID",
            "symbols": {symbol: "INVALID" for symbol in FIXED_SYMBOLS},
            "errors": errors,
            "metrics": _validation_metrics(errors),
        }

    if manifest.get("status") != "FACTS_VALID":
        directory_errors.append("manifest_status_invalid")
    if manifest.get("facts_schema_version") != FACTS_SCHEMA_VERSION:
        directory_errors.append("manifest_schema_version_invalid")
    if manifest.get("trade_date") != TRADE_DATE:
        directory_errors.append("manifest_trade_date_invalid")
    if manifest.get("content_readiness") != "BLOCKED_SOURCE_EVIDENCE":
        directory_errors.append("manifest_content_readiness_invalid")
    if manifest.get("missing_source_inputs") != MISSING_SOURCE_INPUTS:
        directory_errors.append("manifest_missing_source_inputs_invalid")
    if manifest.get("new_tickflow_api_request_count") != 0:
        directory_errors.append("manifest_tickflow_request_count_nonzero")
    if manifest.get("ai_calls") != 0:
        directory_errors.append("manifest_ai_call_count_nonzero")
    source_files = manifest.get("source_files")
    if not isinstance(source_files, list):
        directory_errors.append("manifest_source_files_invalid")
    elif manifest.get("source_set_sha256") != _source_set_hash(source_files):
        directory_errors.append("manifest_source_set_hash_invalid")

    output_hashes = manifest.get("output_hashes")
    if not isinstance(output_hashes, dict) or set(output_hashes) != set(expected_fact_files):
        directory_errors.append("manifest_output_hashes_invalid")
        output_hashes = {}

    symbol_status: dict[str, str] = {}
    symbol_errors: dict[str, list[str]] = {}
    for filename, symbol in expected_fact_files.items():
        path = facts_dir / filename
        errors: list[str] = []
        try:
            facts = _load_json(path)
        except (OSError, ValueError, Phase2FactsError) as exc:
            errors.append(f"facts_file_invalid:{exc}")
        else:
            errors.extend(validate_facts_document(repo_root, facts))
            if output_hashes.get(filename) != _sha256_file(path):
                errors.append("output_hash_mismatch")
        symbol_status[symbol] = "VALID" if not errors else "INVALID"
        if errors:
            symbol_errors[symbol] = sorted(set(errors))

    if manifest.get("symbols") != symbol_status:
        directory_errors.append("manifest_symbol_status_invalid")
    if directory_errors:
        symbol_errors["directory"] = sorted(set(directory_errors))
    status = "FACTS_VALID" if not symbol_errors else "FACTS_INVALID"
    return {
        "status": status,
        "symbols": symbol_status,
        "errors": symbol_errors,
        "metrics": _validation_metrics(symbol_errors),
        "new_tickflow_api_request_count": 0,
        "ai_calls": 0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--facts-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = validate_facts_directory(args.repo_root, args.facts_dir)
    except (OSError, ValueError, Phase2FactsError) as exc:
        result = {"status": "FACTS_INVALID", "errors": {"directory": [str(exc)]}}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "FACTS_VALID" else 2


if __name__ == "__main__":
    raise SystemExit(main())
