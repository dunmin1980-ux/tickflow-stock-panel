"""Crash-recoverable local state storage for the Option C MVP."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from app.schemas.phase2_option_c import (
    ContinuousDayInput,
    ContinuousPaperState,
    SimulationConfig,
    canonical_option_c_bytes,
)
from app.services.atomic_directory import atomic_publish_directory
from app.services.phase2_option_c_continuous import ContinuousDayResult


class OptionCStateError(ValueError):
    """Raised when local Paper Trading state is unsafe or inconsistent."""


@dataclass(frozen=True)
class PublishedDay:
    status: Literal["DAY_PUBLISHED", "DAY_ALREADY_PUBLISHED"]
    path: Path
    input_identity: str
    state: ContinuousPaperState
    artifact_sha256: dict[str, str]


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_durable(path: Path, raw: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _atomic_write(path: Path, raw: bytes) -> None:
    if path.is_symlink():
        raise OptionCStateError(f"state_file_must_not_be_symlink:{path.name}")
    temporary = path.parent / f".{path.name}.tmp-{os.getpid()}"
    temporary.unlink(missing_ok=True)
    try:
        _write_durable(temporary, raw)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _read_regular(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise OptionCStateError(f"state_file_must_be_regular:{path.name}")
    return path.read_bytes()


def _json_object(raw: bytes, *, label: str) -> dict[str, object]:
    def reject_constant(value: str) -> None:
        raise ValueError(value)

    try:
        value = json.loads(raw, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise OptionCStateError(f"{label}_json_invalid") from exc
    if not isinstance(value, dict):
        raise OptionCStateError(f"{label}_json_invalid")
    return value


class OptionCStateStore:
    """Publish immutable daily snapshots and recover the latest valid state."""

    def __init__(self, root: Path) -> None:
        if root.is_symlink():
            raise OptionCStateError("state_root_must_not_be_symlink")
        try:
            parent = root.parent.resolve(strict=True)
        except FileNotFoundError as exc:
            raise OptionCStateError("state_root_parent_missing") from exc
        candidate = parent / root.name
        if candidate.exists():
            if candidate.is_symlink():
                raise OptionCStateError("state_root_must_not_be_symlink")
            if not candidate.is_dir():
                raise OptionCStateError("state_root_must_be_directory")
        else:
            candidate.mkdir(mode=0o700)
        candidate.chmod(0o700)
        days = candidate / "days"
        if days.is_symlink():
            raise OptionCStateError("state_days_must_not_be_symlink")
        days.mkdir(mode=0o700, exist_ok=True)
        days.chmod(0o700)
        self.root = candidate
        self.days_root = days

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Hold one non-blocking process lock across a complete daily run."""
        lock_path = self.root / ".runtime.lock"
        if lock_path.is_symlink():
            raise OptionCStateError("runtime_lock_must_not_be_symlink")
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise OptionCStateError("daily_runner_already_locked") from exc
            self._cleanup_staging()
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _cleanup_staging(self) -> None:
        removed = False
        pattern = re.compile(r"^\.\d{4}-\d{2}-\d{2}\.staging-[A-Za-z0-9_-]+$")
        for path in self.days_root.iterdir():
            if not path.name.startswith("."):
                continue
            if path.is_symlink() or not path.is_dir() or not pattern.fullmatch(path.name):
                raise OptionCStateError("unknown_hidden_state_residue")
            shutil.rmtree(path)
            removed = True
        if removed:
            _fsync_directory(self.days_root)

    def _load_day(self, day_root: Path) -> PublishedDay:
        if day_root.is_symlink() or not day_root.is_dir():
            raise OptionCStateError("published_day_must_be_directory")
        receipt_raw = _read_regular(day_root / "run_receipt.json")
        receipt = _json_object(receipt_raw, label="run_receipt")
        if receipt.get("status") != "DAY_PUBLISHED":
            raise OptionCStateError("published_day_receipt_invalid")
        artifact_hashes = receipt.get("artifact_sha256")
        if not isinstance(artifact_hashes, dict):
            raise OptionCStateError("published_day_receipt_invalid")
        for name, expected in artifact_hashes.items():
            if not isinstance(name, str) or not isinstance(expected, str):
                raise OptionCStateError("published_day_receipt_invalid")
            if _sha256(_read_regular(day_root / name)) != expected:
                raise OptionCStateError("published_day_artifact_hash_mismatch")
        state_raw = _read_regular(day_root / "continuous_state.json")
        try:
            state = ContinuousPaperState.model_validate(
                _json_object(state_raw, label="continuous_state"),
                strict=False,
            )
        except ValidationError as exc:
            raise OptionCStateError("published_day_state_invalid") from exc
        input_identity = receipt.get("input_identity")
        if (
            not isinstance(input_identity, str)
            or len(input_identity) != 64
            or receipt.get("trade_date") != day_root.name
            or receipt.get("continuous_state_sha256")
            != artifact_hashes.get("continuous_state.json")
            or state.last_completed_trade_date is None
            or state.last_completed_trade_date.isoformat() != day_root.name
            or state.completed_input_identities[-1] != input_identity
        ):
            raise OptionCStateError("published_day_identity_invalid")
        return PublishedDay(
            status="DAY_ALREADY_PUBLISHED",
            path=day_root,
            input_identity=input_identity,
            state=state,
            artifact_sha256={str(k): str(v) for k, v in artifact_hashes.items()},
        )

    def _published_days(self) -> list[PublishedDay]:
        published: list[PublishedDay] = []
        for path in sorted(self.days_root.iterdir()):
            if path.name.startswith("."):
                if path.is_dir():
                    raise OptionCStateError("staging_directory_residue")
                continue
            try:
                date.fromisoformat(path.name)
            except ValueError as exc:
                raise OptionCStateError("published_day_name_invalid") from exc
            published.append(self._load_day(path))
        dates = [item.state.last_completed_trade_date for item in published]
        if dates != sorted(set(dates)):
            raise OptionCStateError("published_day_dates_invalid")
        if published:
            expected_dates = [item.state.last_completed_trade_date for item in published]
            final_dates = [entry.trade_date for entry in published[-1].state.decision_ledger]
            if final_dates != expected_dates:
                raise OptionCStateError("published_state_chain_invalid")
        return published

    def find_published_day(
        self,
        trade_date: date,
        input_identity: str,
    ) -> PublishedDay | None:
        day_root = self.days_root / trade_date.isoformat()
        if not day_root.exists():
            return None
        published = self._load_day(day_root)
        if published.input_identity != input_identity:
            raise OptionCStateError("published_day_identity_conflict")
        return published

    def load_or_initialize(self, config: SimulationConfig) -> ContinuousPaperState:
        """Load the latest immutable day, repairing a stale current pointer."""
        published = self._published_days()
        if not published:
            from app.services.phase2_option_c_continuous import new_continuous_state

            return new_continuous_state(config)
        state = published[-1].state
        if state.account.config != config:
            raise OptionCStateError("paper_config_mismatch")
        canonical = canonical_option_c_bytes(state)
        current = self.root / "current_state.json"
        if not current.exists() or _read_regular(current) != canonical:
            _atomic_write(current, canonical)
        return state

    def publish_day(
        self,
        day_input: ContinuousDayInput,
        result: ContinuousDayResult,
        *,
        daily_json: bytes,
        daily_markdown: bytes,
    ) -> PublishedDay:
        """Atomically append one immutable day and update the recoverable pointer."""
        existing = self.find_published_day(day_input.trade_date, day_input.input_identity)
        if existing is not None:
            return existing
        if (
            result.day_input != day_input
            or result.state.last_completed_trade_date != day_input.trade_date
            or result.state.completed_input_identities[-1] != day_input.input_identity
        ):
            raise OptionCStateError("daily_result_identity_mismatch")
        if b"SIMULATION ONLY" not in daily_json or b"SIMULATION ONLY" not in daily_markdown:
            raise OptionCStateError("daily_output_missing_simulation_safety")

        state = result.state
        published = self._published_days()
        if published:
            prior = published[-1].state
            if (
                state.decision_ledger[:-1] != prior.decision_ledger
                or state.equity_history[:-1] != prior.equity_history
                or state.completed_input_identities[:-1] != prior.completed_input_identities
                or state.account.trades[: len(prior.account.trades)] != prior.account.trades
                or state.account.processed_action_ids[: len(prior.account.processed_action_ids)]
                != prior.account.processed_action_ids
                or state.account.processed_decision_ids[: len(prior.account.processed_decision_ids)]
                != prior.account.processed_decision_ids
                or state.account.processed_order_ids[: len(prior.account.processed_order_ids)]
                != prior.account.processed_order_ids
                or state.account.processed_idempotency_keys[
                    : len(prior.account.processed_idempotency_keys)
                ]
                != prior.account.processed_idempotency_keys
            ):
                raise OptionCStateError("state_history_not_append_only")
        elif len(state.decision_ledger) != 1:
            raise OptionCStateError("state_history_not_append_only")
        artifacts = {
            "account_state.json": canonical_option_c_bytes(state.account),
            "chenquant_daily.json": daily_json,
            "chenquant_daily.md": daily_markdown,
            "continuous_state.json": canonical_option_c_bytes(state),
            "decision_ledger.json": canonical_option_c_bytes(
                {
                    "decision_ledger_schema_version": 1,
                    "decisions": [entry.model_dump(mode="json") for entry in state.decision_ledger],
                    "simulation_only": "SIMULATION ONLY",
                    "can_publish": False,
                    "trading_advice": False,
                }
            ),
            "equity_history.json": canonical_option_c_bytes(
                {
                    "equity_history_schema_version": 1,
                    "points": [point.model_dump(mode="json") for point in state.equity_history],
                    "simulation_only": "SIMULATION ONLY",
                    "can_publish": False,
                    "trading_advice": False,
                }
            ),
            "input.json": canonical_option_c_bytes(day_input),
            "input_identities.json": canonical_option_c_bytes(
                {
                    "input_identity_schema_version": 1,
                    "identities": state.completed_input_identities,
                    "simulation_only": "SIMULATION ONLY",
                    "can_publish": False,
                    "trading_advice": False,
                }
            ),
            "position_lots.json": canonical_option_c_bytes(
                {
                    "position_lot_ledger_schema_version": 1,
                    "lots": [lot.model_dump(mode="json") for lot in state.account.lots],
                    "simulation_only": "SIMULATION ONLY",
                    "can_publish": False,
                    "trading_advice": False,
                }
            ),
            "trade_ledger.json": canonical_option_c_bytes(
                {
                    "trade_ledger_schema_version": 1,
                    "trades": [trade.model_dump(mode="json") for trade in state.account.trades],
                    "simulation_only": "SIMULATION ONLY",
                    "can_publish": False,
                    "trading_advice": False,
                }
            ),
        }
        artifact_hashes = {name: _sha256(raw) for name, raw in sorted(artifacts.items())}
        receipt = canonical_option_c_bytes(
            {
                "run_receipt_schema_version": 1,
                "status": "DAY_PUBLISHED",
                "trade_date": day_input.trade_date.isoformat(),
                "input_identity": day_input.input_identity,
                "continuous_state_sha256": artifact_hashes["continuous_state.json"],
                "artifact_sha256": artifact_hashes,
                "simulation_only": "SIMULATION ONLY",
                "can_publish": False,
                "trading_advice": False,
            }
        )
        destination = self.days_root / day_input.trade_date.isoformat()
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{day_input.trade_date.isoformat()}.staging-", dir=self.days_root
            )
        )
        staging.chmod(0o700)
        try:
            for name, raw in sorted({**artifacts, "run_receipt.json": receipt}.items()):
                _write_durable(staging / name, raw)
            _fsync_directory(staging)
            _fsync_directory(self.days_root)
            atomic_publish_directory(staging, destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
                _fsync_directory(self.days_root)
        _atomic_write(self.root / "current_state.json", artifacts["continuous_state.json"])
        return PublishedDay(
            status="DAY_PUBLISHED",
            path=destination,
            input_identity=day_input.input_identity,
            state=state,
            artifact_sha256=artifact_hashes,
        )
