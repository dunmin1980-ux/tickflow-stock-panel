"""Repository-local publication for deterministic Phase 2 Claims artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal

from app.schemas.phase2_claims import ClaimsDocument, claims_json_schema
from app.services.atomic_directory import atomic_publish_directory
from app.services.phase2_claims_renderer import (
    render_claims_document,
    validate_rendered_document,
)
from app.services.phase2_claims_service import (
    CLAIMS_INVALID,
    CLAIMS_SCHEMA_VERSION,
    CLAIMS_VALID,
    Phase2ClaimsError,
    build_claims_fixtures,
    canonical_json_bytes,
    validate_claims_directory,
    validate_claims_document,
)
from app.services.phase2_facts import FIXED_SYMBOLS

RENDERED_VALID = "RENDERED_VALID"
TRANSACTION_MARKER_NAME = ".phase2_claims_delivery_transaction.json"
TYPED_PREVIEW_PREFIX = "typed_"


class Phase2ClaimsDeliveryError(ValueError):
    """Raised when deterministic Claims artifacts cannot be published safely."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _resolve_reports_root(
    repo_root: Path,
    reports_root: Path,
    *,
    allow_test_output_root: bool,
) -> Path:
    repo_root = repo_root.resolve(strict=True)
    if reports_root.is_symlink():
        raise Phase2ClaimsDeliveryError("reports_root_must_not_be_symlink")
    reports_root = reports_root.resolve(strict=True)
    if not allow_test_output_root and reports_root != repo_root / "reports":
        raise Phase2ClaimsDeliveryError("reports_root_must_be_repository_reports")
    return reports_root


def _assert_regular_tree(root: Path, *, label: str) -> None:
    if root.is_symlink() or not root.is_dir():
        raise Phase2ClaimsDeliveryError(f"{label}_directory_invalid")
    with os.scandir(root) as entries:
        for entry in entries:
            if entry.is_symlink():
                raise Phase2ClaimsDeliveryError(f"{label}_symlink_forbidden")
            if entry.is_dir(follow_symlinks=False):
                _assert_regular_tree(Path(entry.path), label=label)
            elif not entry.is_file(follow_symlinks=False):
                raise Phase2ClaimsDeliveryError(f"{label}_entry_invalid")


def _regular_file_hashes(root: Path) -> dict[str, str]:
    _assert_regular_tree(root, label="preview_route")
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[path.relative_to(root).as_posix()] = _sha256_bytes(path.read_bytes())
    return result


def _ensure_preview_tree(reports_root: Path) -> Path:
    preview_root = reports_root / "phase2_obsidian_preview"
    if preview_root.is_symlink():
        raise Phase2ClaimsDeliveryError("preview_root_must_not_be_symlink")
    preview_root.mkdir(exist_ok=True)
    if not preview_root.is_dir():
        raise Phase2ClaimsDeliveryError("preview_root_invalid")
    for route in ("inbox", "reviewed", "rejected"):
        path = preview_root / route
        if path.is_symlink():
            raise Phase2ClaimsDeliveryError("preview_route_symlink_forbidden")
        path.mkdir(exist_ok=True)
        if not path.is_dir():
            raise Phase2ClaimsDeliveryError("preview_route_directory_invalid")
    if any((preview_root / "reviewed").iterdir()):
        raise Phase2ClaimsDeliveryError("reviewed_directory_must_be_empty")
    return preview_root


def _write_durable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.exists():
        raise Phase2ClaimsDeliveryError("staging_destination_already_exists")
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _copy_regular_tree(source: Path, destination: Path) -> None:
    _assert_regular_tree(source, label="publication_source")
    for source_path in sorted(source.rglob("*")):
        relative = source_path.relative_to(source)
        destination_path = destination / relative
        if source_path.is_dir():
            destination_path.mkdir(parents=True, exist_ok=False)
        else:
            _write_durable(destination_path, source_path.read_bytes())


def _snapshot_directory(source: Path, *, prefix: str) -> Path | None:
    if not source.exists():
        return None
    _assert_regular_tree(source, label="publication_destination")
    snapshot = Path(tempfile.mkdtemp(prefix=prefix, dir=source.parent))
    try:
        _copy_regular_tree(source, snapshot)
        _fsync_directory(snapshot)
        _fsync_directory(snapshot.parent)
    except Exception:
        shutil.rmtree(snapshot, ignore_errors=True)
        raise
    return snapshot


def _restore_directory_snapshot(destination: Path, snapshot: Path | None) -> None:
    parent = destination.parent
    displaced: Path | None = None
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir():
            raise Phase2ClaimsDeliveryError("rollback_destination_invalid")
        displaced = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}.rollback-", dir=parent)
        )
        displaced.rmdir()
        os.replace(destination, displaced)
        _fsync_directory(parent)
    try:
        if snapshot is not None:
            if snapshot.is_symlink() or not snapshot.is_dir():
                raise Phase2ClaimsDeliveryError("rollback_snapshot_invalid")
            os.replace(snapshot, destination)
            _fsync_directory(parent)
    except Exception:
        if displaced is not None and displaced.exists() and not destination.exists():
            os.replace(displaced, destination)
            _fsync_directory(parent)
        raise
    if displaced is not None:
        shutil.rmtree(displaced)
        _fsync_directory(parent)


def _write_transaction_marker(reports_root: Path) -> Path:
    marker = reports_root / TRANSACTION_MARKER_NAME
    if marker.is_symlink() or marker.exists():
        raise Phase2ClaimsDeliveryError("publication_transaction_already_active")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(marker, flags, 0o600)
    try:
        payload = canonical_json_bytes(
            {
                "version": 1,
                "destinations": [
                    "phase2_claims",
                    "phase2_obsidian_preview/inbox",
                ],
            }
        )
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(reports_root)
    return marker


def _remove_transaction_marker(marker: Path) -> None:
    marker.unlink()
    _fsync_directory(marker.parent)


def _atomic_replace_durable(path: Path, data: bytes) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise Phase2ClaimsDeliveryError("artifact_parent_invalid")
    if path.is_symlink():
        raise Phase2ClaimsDeliveryError("artifact_destination_symlink_forbidden")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _stage_inbox(
    preview_root: Path,
    rendered_bytes: dict[str, bytes],
) -> Path:
    inbox = preview_root / "inbox"
    _assert_regular_tree(inbox, label="preview_inbox")
    staging = Path(tempfile.mkdtemp(prefix=".inbox.staging-", dir=preview_root))
    try:
        for source_path in sorted(inbox.iterdir()):
            if source_path.name.startswith(TYPED_PREVIEW_PREFIX):
                continue
            destination_path = staging / source_path.name
            if source_path.is_dir():
                destination_path.mkdir()
                _copy_regular_tree(source_path, destination_path)
            else:
                _write_durable(destination_path, source_path.read_bytes())
        for filename, data in rendered_bytes.items():
            _write_durable(staging / f"{TYPED_PREVIEW_PREFIX}{filename}", data)
        _fsync_directory(staging)
        _fsync_directory(preview_root)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return staging


def route_claims_preview(
    status: str,
) -> Literal["inbox", "rejected"]:
    """Route only validated Claims to the isolated preview inbox."""
    if status == CLAIMS_VALID:
        return "inbox"
    if status == CLAIMS_INVALID:
        return "rejected"
    raise Phase2ClaimsDeliveryError("claims_status_unknown")


def publish_invalid_claims_receipt(
    repo_root: Path,
    reports_root: Path,
    *,
    symbol: str,
    candidate_bytes: bytes,
    validation_errors: list[str],
    _allow_test_output_root: bool = False,
) -> Path:
    """Publish a deterministic rejection receipt without candidate content."""
    repo_root = repo_root.resolve(strict=True)
    reports_root = _resolve_reports_root(
        repo_root,
        reports_root,
        allow_test_output_root=_allow_test_output_root,
    )
    if not isinstance(candidate_bytes, bytes):
        raise Phase2ClaimsDeliveryError("candidate_bytes_invalid")
    if not validation_errors or any(
        not isinstance(item, str) or not item for item in validation_errors
    ):
        raise Phase2ClaimsDeliveryError("validation_errors_invalid")
    if route_claims_preview(CLAIMS_INVALID) != "rejected":  # pragma: no cover
        raise Phase2ClaimsDeliveryError("invalid_route_not_closed")
    preview_root = _ensure_preview_tree(reports_root)
    rejected_root = preview_root / "rejected"
    before = _regular_file_hashes(rejected_root)
    name = FIXED_SYMBOLS.get(symbol)
    if name is None:
        raise Phase2ClaimsDeliveryError("symbol_name_outside_fixed_scope")
    candidate_sha256 = _sha256_bytes(candidate_bytes)
    errors_sha256 = _sha256_bytes(canonical_json_bytes(validation_errors))
    receipt = (
        "---\n"
        "type: stock-typed-claims-rejection\n"
        "source_system: tickflow-stock-panel\n"
        "data_scope: single-symbol\n"
        "can_publish: false\n"
        "trading_advice: false\n"
        "validation_status: rejected\n"
        "---\n"
        "# Typed Claims Rejection Receipt\n\n"
        f"- symbol: `{symbol}`\n"
        f"- candidate_sha256: `{candidate_sha256}`\n"
        f"- validation_error_count: {len(validation_errors)}\n"
        f"- validation_errors_sha256: `{errors_sha256}`\n"
        "- candidate_content_persisted: false\n"
    ).encode()
    destination = (
        rejected_root
        / f'typed_{symbol.replace(".", "")}_{name}_rejected.md'
    )
    if destination.exists():
        if destination.is_symlink() or not destination.is_file():
            raise Phase2ClaimsDeliveryError("rejection_receipt_destination_invalid")
        if destination.read_bytes() != receipt:
            raise Phase2ClaimsDeliveryError("rejection_receipt_conflict")
        return destination
    _atomic_replace_durable(destination, receipt)
    after = _regular_file_hashes(rejected_root)
    if any(after.get(path) != digest for path, digest in before.items()):
        raise Phase2ClaimsDeliveryError("historical_rejected_artifacts_mutated")
    return destination


def _rendered_filename(symbol: str, name: str) -> str:
    if FIXED_SYMBOLS.get(symbol) != name:
        raise Phase2ClaimsDeliveryError("symbol_name_outside_fixed_scope")
    return f'{symbol.replace(".", "")}_{name}.md'


def publish_claims_bundle(
    repo_root: Path,
    reports_root: Path,
    *,
    _allow_test_output_root: bool = False,
) -> dict[str, Any]:
    """Atomically publish Claims and durably route typed previews."""
    repo_root = repo_root.resolve(strict=True)
    reports_root = _resolve_reports_root(
        repo_root,
        reports_root,
        allow_test_output_root=_allow_test_output_root,
    )
    preview_root = _ensure_preview_tree(reports_root)
    rejected_before = _regular_file_hashes(preview_root / "rejected")
    fixtures = build_claims_fixtures(repo_root)
    schema_bytes = canonical_json_bytes(claims_json_schema())
    fixture_bytes: dict[str, bytes] = {}
    rendered_bytes: dict[str, bytes] = {}
    entries: list[dict[str, Any]] = []
    for symbol, name in FIXED_SYMBOLS.items():
        document = fixtures[symbol]
        validation = validate_claims_document(repo_root, document)
        if validation.status != CLAIMS_VALID:
            raise Phase2ClaimsDeliveryError(f"generated_claims_invalid:{symbol}")
        rendered = render_claims_document(document, validation)
        render_errors = validate_rendered_document(document, validation, rendered)
        if render_errors:
            raise Phase2ClaimsDeliveryError(f"generated_render_invalid:{symbol}")
        compact = symbol.replace(".", "")
        fixture_filename = f"{compact}_claims.json"
        rendered_filename = _rendered_filename(symbol, name)
        fixture_data = canonical_json_bytes(document.model_dump(mode="json"))
        rendered_data = rendered.encode("utf-8")
        fixture_bytes[fixture_filename] = fixture_data
        rendered_bytes[rendered_filename] = rendered_data
        entries.append(
            {
                "symbol": symbol,
                "name": name,
                "fixture_file": f"fixtures/{fixture_filename}",
                "fixture_sha256": _sha256_bytes(fixture_data),
                "normalized_sha256": validation.normalized_sha256,
                "claim_count": validation.claim_count,
                "validation_status": validation.status,
                "render_status": RENDERED_VALID,
                "rendered_file": f"rendered/{rendered_filename}",
                "rendered_sha256": _sha256_bytes(rendered_data),
                "preview_route": "inbox",
                "preview_file": f"inbox/typed_{rendered_filename}",
            }
        )
    index = {
        "claims_schema_version": CLAIMS_SCHEMA_VERSION,
        "status": CLAIMS_VALID,
        "rendering_status": RENDERED_VALID,
        "schema_file": "schema/phase2_claims.schema.json",
        "schema_sha256": _sha256_bytes(schema_bytes),
        "symbol_scope": list(FIXED_SYMBOLS),
        "can_publish": False,
        "trading_advice": False,
        "reviewed_auto_write": False,
        "new_tickflow_api_request_count": 0,
        "new_ai_call_count": 0,
        "new_provider_attempt_count": 0,
        "cloud_mutation_count": 0,
        "obsidian_real_vault_write": False,
        "paper_trading_started": False,
        "integrated_gold_enabled": False,
        "external_send_count": 0,
        "entries": entries,
    }
    bundle_staging = Path(
        tempfile.mkdtemp(prefix=".phase2_claims.staging-", dir=reports_root)
    )
    inbox_staging: Path | None = None
    marker: Path | None = None
    bundle_snapshot: Path | None = None
    inbox_snapshot: Path | None = None
    bundle_destination = reports_root / "phase2_claims"
    inbox_destination = preview_root / "inbox"
    try:
        _write_durable(
            bundle_staging / "schema/phase2_claims.schema.json",
            schema_bytes,
        )
        for filename, data in fixture_bytes.items():
            _write_durable(bundle_staging / "fixtures" / filename, data)
        for filename, data in rendered_bytes.items():
            _write_durable(bundle_staging / "rendered" / filename, data)
        _write_durable(
            bundle_staging / "claims_index.json",
            canonical_json_bytes(index),
        )
        inbox_staging = _stage_inbox(preview_root, rendered_bytes)
        bundle_snapshot = _snapshot_directory(
            bundle_destination,
            prefix=".phase2_claims.backup-",
        )
        inbox_snapshot = _snapshot_directory(
            inbox_destination,
            prefix=".inbox.backup-",
        )
        marker = _write_transaction_marker(reports_root)
        try:
            atomic_publish_directory(bundle_staging, bundle_destination)
            atomic_publish_directory(inbox_staging, inbox_destination)
            if _regular_file_hashes(preview_root / "rejected") != rejected_before:
                raise Phase2ClaimsDeliveryError(
                    "historical_rejected_artifacts_mutated"
                )
            if any((preview_root / "reviewed").iterdir()):
                raise Phase2ClaimsDeliveryError("reviewed_directory_must_be_empty")
        except Exception as exc:
            rollback_errors: list[str] = []
            for destination, snapshot in (
                (inbox_destination, inbox_snapshot),
                (bundle_destination, bundle_snapshot),
            ):
                try:
                    _restore_directory_snapshot(destination, snapshot)
                except Exception as rollback_exc:  # pragma: no cover - fatal path
                    rollback_errors.append(
                        f"{destination.name}:{type(rollback_exc).__name__}"
                    )
            if rollback_errors:
                raise Phase2ClaimsDeliveryError(
                    "publication_failed_rollback_incomplete:"
                    + ",".join(rollback_errors)
                ) from exc
            _remove_transaction_marker(marker)
            marker = None
            raise Phase2ClaimsDeliveryError("publication_failed") from exc
        _remove_transaction_marker(marker)
        marker = None
    finally:
        shutil.rmtree(bundle_staging, ignore_errors=True)
        if inbox_staging is not None:
            shutil.rmtree(inbox_staging, ignore_errors=True)
        if marker is None:
            if bundle_snapshot is not None:
                shutil.rmtree(bundle_snapshot, ignore_errors=True)
            if inbox_snapshot is not None:
                shutil.rmtree(inbox_snapshot, ignore_errors=True)

    if _regular_file_hashes(preview_root / "rejected") != rejected_before:
        raise Phase2ClaimsDeliveryError("historical_rejected_artifacts_mutated")
    if any((preview_root / "reviewed").iterdir()):
        raise Phase2ClaimsDeliveryError("reviewed_directory_must_be_empty")
    return index


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise Phase2ClaimsDeliveryError("artifact_file_invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Phase2ClaimsDeliveryError("artifact_json_invalid") from exc
    if not isinstance(value, dict):
        raise Phase2ClaimsDeliveryError("artifact_json_must_be_object")
    return value


def validate_claims_bundle(repo_root: Path, reports_root: Path) -> dict[str, Any]:
    """Validate rendered artifacts and typed preview routes offline."""
    repo_root = repo_root.resolve(strict=True)
    reports_root = reports_root.resolve(strict=True)
    marker = reports_root / TRANSACTION_MARKER_NAME
    if marker.is_symlink() or marker.exists():
        return {
            "status": CLAIMS_INVALID,
            "errors": ["publication_transaction_incomplete"],
            "rendering_status": "RENDERED_INVALID",
            "typed_preview_count": 0,
            "reviewed_file_count": -1,
        }
    claims_root = reports_root / "phase2_claims"
    preview_root = reports_root / "phase2_obsidian_preview"
    try:
        _assert_regular_tree(claims_root, label="claims_tree")
        _assert_regular_tree(preview_root, label="preview_tree")
    except Phase2ClaimsDeliveryError as exc:
        return {
            "status": CLAIMS_INVALID,
            "errors": [str(exc)],
            "rendering_status": "RENDERED_INVALID",
            "typed_preview_count": 0,
            "reviewed_file_count": -1,
        }
    base = validate_claims_directory(repo_root, claims_root)
    errors = list(base.get("errors", []))
    try:
        index = _load_json(claims_root / "claims_index.json")
    except Phase2ClaimsDeliveryError as exc:
        return {**base, "status": CLAIMS_INVALID, "errors": [str(exc)]}
    entries = index.get("entries")
    if not isinstance(entries, list):
        entries = []
        errors.append("claims_index_entries_invalid")
    entries_by_symbol = {
        item.get("symbol"): item
        for item in entries
        if isinstance(item, dict) and isinstance(item.get("symbol"), str)
    }
    expected_rendered: set[str] = set()
    expected_typed: set[str] = set()
    for symbol, name in FIXED_SYMBOLS.items():
        compact = symbol.replace(".", "")
        fixture_path = claims_root / "fixtures" / f"{compact}_claims.json"
        filename = _rendered_filename(symbol, name)
        rendered_path = claims_root / "rendered" / filename
        preview_path = preview_root / "inbox" / f"typed_{filename}"
        expected_rendered.add(filename)
        expected_typed.add(f"typed_{filename}")
        if rendered_path.is_symlink():
            errors.append(f"rendered_artifact_symlink_forbidden:{symbol}")
            continue
        if not rendered_path.is_file():
            errors.append(f"rendered_artifact_missing:{symbol}")
            continue
        try:
            document = ClaimsDocument.model_validate(_load_json(fixture_path))
            validation = validate_claims_document(repo_root, document)
            rendered = rendered_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, Phase2ClaimsError, ValueError) as exc:
            errors.append(f"rendered_artifact_invalid:{symbol}:{type(exc).__name__}")
            continue
        errors.extend(
            f"{symbol}:{item}"
            for item in validate_rendered_document(document, validation, rendered)
        )
        rendered_data = rendered.encode("utf-8")
        if preview_path.is_symlink() or not preview_path.is_file():
            errors.append(f"typed_preview_missing:{symbol}")
        elif preview_path.read_bytes() != rendered_data:
            errors.append(f"typed_preview_hash_mismatch:{symbol}")
        entry = entries_by_symbol.get(symbol)
        expected_entry = {
            "render_status": RENDERED_VALID,
            "rendered_file": f"rendered/{filename}",
            "rendered_sha256": _sha256_bytes(rendered_data),
            "preview_route": "inbox",
            "preview_file": f"inbox/typed_{filename}",
        }
        if not isinstance(entry, dict) or any(
            entry.get(key) != value for key, value in expected_entry.items()
        ):
            errors.append(f"rendered_index_entry_mismatch:{symbol}")
    rendered_root = claims_root / "rendered"
    actual_rendered = (
        {item.name for item in rendered_root.iterdir() if item.is_file()}
        if rendered_root.is_dir() and not rendered_root.is_symlink()
        else set()
    )
    if actual_rendered != expected_rendered:
        errors.append("rendered_file_set_invalid")
    inbox_root = preview_root / "inbox"
    actual_typed = (
        {
            item.name
            for item in inbox_root.iterdir()
            if item.is_file() and item.name.startswith("typed_")
        }
        if inbox_root.is_dir() and not inbox_root.is_symlink()
        else set()
    )
    if actual_typed != expected_typed:
        errors.append("typed_preview_file_set_invalid")
    reviewed_root = preview_root / "reviewed"
    if (
        reviewed_root.is_symlink()
        or not reviewed_root.is_dir()
        or any(reviewed_root.iterdir())
    ):
        errors.append("reviewed_directory_must_be_empty")
    for key, expected in {
        "status": CLAIMS_VALID,
        "rendering_status": RENDERED_VALID,
        "can_publish": False,
        "trading_advice": False,
        "reviewed_auto_write": False,
    }.items():
        if index.get(key) != expected:
            errors.append(f"claims_index_field_invalid:{key}")
    unique_errors = sorted(set(errors))
    return {
        **base,
        "status": CLAIMS_VALID if not unique_errors else CLAIMS_INVALID,
        "errors": unique_errors,
        "rendering_status": (
            RENDERED_VALID if not unique_errors else "RENDERED_INVALID"
        ),
        "typed_preview_count": len(actual_typed),
        "reviewed_file_count": (
            len(list(reviewed_root.iterdir()))
            if reviewed_root.is_dir() and not reviewed_root.is_symlink()
            else -1
        ),
    }
