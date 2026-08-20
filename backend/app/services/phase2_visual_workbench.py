"""Thin, fail-closed browser facade for the released Option C engine."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from app.schemas.phase2_option_c import (
    ContinuousDayInput,
    MvpChenQuantDaily,
    SimulationConfig,
)
from app.services.phase2_option_c_continuous import ContinuousRunError
from app.services.phase2_option_c_daily import (
    OptionCDailyError,
    build_reference_day_input,
    load_day_input,
    run_daily_once,
)
from app.services.phase2_option_c_fixture import (
    ReferenceFixtureError,
    load_reference_fixture,
)
from app.services.phase2_option_c_state import OptionCStateError, OptionCStateStore


class VisualWorkbenchError(ValueError):
    """Stable, non-sensitive error raised by the visual facade."""


def _json_model(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json")


def _regular_bytes(path: Path, *, code: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise VisualWorkbenchError(code)
    return path.read_bytes()


class Phase2VisualWorkbenchService:
    """Aggregate Option C state without owning any trading or accounting rule."""

    def __init__(self, *, repo_root: Path, state_root: Path, input_root: Path) -> None:
        self.repo_root = repo_root.resolve(strict=True)
        self.state_root = state_root
        self.input_root = input_root
        self._ensure_parent(self.state_root.parent)
        self._ensure_parent(self.input_root)

    @staticmethod
    def _ensure_parent(path: Path) -> None:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise VisualWorkbenchError("PAPER_STATE_UNAVAILABLE")
        path.chmod(0o700)

    def reference_input(self) -> ContinuousDayInput:
        try:
            return build_reference_day_input(self.repo_root)
        except ReferenceFixtureError as exc:
            raise VisualWorkbenchError("PAPER_REFERENCE_INVALID") from exc

    def _store(self) -> OptionCStateStore:
        try:
            return OptionCStateStore(self.state_root)
        except OptionCStateError as exc:
            raise VisualWorkbenchError("PAPER_STATE_UNAVAILABLE") from exc

    def _state(self):
        try:
            return self._store().load_or_initialize(SimulationConfig.default())
        except OptionCStateError as exc:
            raise VisualWorkbenchError("PAPER_STATE_UNAVAILABLE") from exc

    def _staged_path(self, target: date) -> Path:
        return self.input_root / f"{target.isoformat()}_input.json"

    def _resolve_input(self, target: date, *, state) -> tuple[ContinuousDayInput | None, dict]:
        staged = self._staged_path(target)
        if staged.exists() or staged.is_symlink():
            try:
                day_input = load_day_input(staged)
            except OptionCDailyError as exc:
                raise VisualWorkbenchError("PAPER_INPUT_INVALID") from exc
            if day_input.trade_date != target:
                raise VisualWorkbenchError("PAPER_INPUT_DATE_MISMATCH")
            if day_input.source_fixture == "DETERMINISTIC_TEST_FIXTURE":
                raise VisualWorkbenchError("PAPER_TEST_FIXTURE_NOT_ALLOWED")
            return day_input, {
                "status": "READY_STAGED",
                "requested_date": target.isoformat(),
                "effective_trade_date": day_input.trade_date.isoformat(),
                "source_fixture": day_input.source_fixture,
                "is_current_date": True,
                "message": "VALIDATED_DAILY_INPUT_AVAILABLE",
            }

        reference = self.reference_input()
        published_dates = {entry.trade_date for entry in state.decision_ledger}
        if target == reference.trade_date:
            status = "ALREADY_PUBLISHED" if target in published_dates else "READY_REFERENCE"
            return reference, {
                "status": status,
                "requested_date": target.isoformat(),
                "effective_trade_date": reference.trade_date.isoformat(),
                "source_fixture": reference.source_fixture,
                "is_current_date": True,
                "message": (
                    "REFERENCE_DAY_ALREADY_PUBLISHED"
                    if status == "ALREADY_PUBLISHED"
                    else "FROZEN_REFERENCE_INITIALIZATION_AVAILABLE"
                ),
            }
        if not state.decision_ledger:
            return reference, {
                "status": "READY_REFERENCE",
                "requested_date": target.isoformat(),
                "effective_trade_date": reference.trade_date.isoformat(),
                "source_fixture": reference.source_fixture,
                "is_current_date": False,
                "message": "FROZEN_REFERENCE_INITIALIZATION_AVAILABLE",
            }
        return None, {
            "status": "MISSING",
            "requested_date": target.isoformat(),
            "effective_trade_date": None,
            "source_fixture": None,
            "is_current_date": False,
            "message": "VALIDATED_DAILY_INPUT_REQUIRED",
        }

    def _latest_daily(self, state) -> tuple[dict[str, Any] | None, str | None]:
        if state.last_completed_trade_date is None:
            return None, None
        day_root = self.state_root / "days" / state.last_completed_trade_date.isoformat()
        try:
            daily_raw = _regular_bytes(
                day_root / "chenquant_daily.json",
                code="PAPER_DAILY_UNAVAILABLE",
            )
            daily = MvpChenQuantDaily.model_validate_json(daily_raw)
            markdown = _regular_bytes(
                day_root / "chenquant_daily.md",
                code="PAPER_DAILY_UNAVAILABLE",
            ).decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            if isinstance(exc, VisualWorkbenchError):
                raise
            raise VisualWorkbenchError("PAPER_DAILY_UNAVAILABLE") from exc
        return _json_model(daily), markdown

    def _claims(self) -> dict[str, Any]:
        try:
            validation = load_reference_fixture(self.repo_root).validation
        except ReferenceFixtureError as exc:
            raise VisualWorkbenchError("PAPER_REFERENCE_INVALID") from exc
        return {
            **validation.to_dict(),
            "status": "VALID" if validation.status == "CLAIMS_VALID" else "INVALID",
        }

    @staticmethod
    def _account(state, latest_daily: dict[str, Any] | None) -> dict[str, Any]:
        account = state.account
        cumulative = (
            latest_daily["position"]["cumulative_return_percent"]
            if latest_daily is not None
            else "0.0000"
        )
        return {
            "initial_cash_cny": str(account.config.initial_cash),
            "cash_cny": str(account.cash_cny),
            "market_value_cny": str(account.market_value_cny),
            "total_equity_cny": str(account.total_equity_cny),
            "cumulative_return_percent": str(cumulative),
            "realized_pnl_cny": str(account.realized_pnl_cny),
            "unrealized_pnl_cny": str(account.unrealized_pnl_cny),
            "current_drawdown_cny": str(account.current_drawdown_cny),
            "max_drawdown_cny": str(account.max_drawdown_cny),
            "pending_action": (
                _json_model(state.pending_action) if state.pending_action is not None else None
            ),
        }

    def dashboard(self, target: date) -> dict[str, Any]:
        state = self._state()
        _, readiness = self._resolve_input(target, state=state)
        latest_daily, markdown = self._latest_daily(state)
        return {
            "status": "VISUAL_WORKBENCH_READY",
            "symbol": "000403.SZ",
            "name": "派林生物",
            "timezone": "Asia/Shanghai",
            "requested_date": target.isoformat(),
            "last_completed_trade_date": (
                state.last_completed_trade_date.isoformat()
                if state.last_completed_trade_date is not None
                else None
            ),
            "safety": {
                "simulation_only": "SIMULATION ONLY",
                "real_trading": "DISABLED",
                "can_publish": False,
                "trading_advice": False,
            },
            "input_readiness": readiness,
            "claims": self._claims(),
            "account": self._account(state, latest_daily),
            "positions": [_json_model(lot) for lot in state.account.lots],
            "decisions": [_json_model(entry) for entry in state.decision_ledger],
            "trades": [_json_model(trade) for trade in state.account.trades],
            "equity_history": [_json_model(point) for point in state.equity_history],
            "latest_daily": latest_daily,
            "chenquant_daily_markdown": markdown,
        }

    def run(self, target: date) -> dict[str, Any]:
        state = self._state()
        day_input, readiness = self._resolve_input(target, state=state)
        if day_input is None or readiness["status"] == "MISSING":
            raise VisualWorkbenchError("PAPER_INPUT_MISSING")
        try:
            result = run_daily_once(
                self.repo_root,
                self.state_root,
                day_input,
            )
        except OptionCStateError as exc:
            code = (
                "PAPER_RUN_ALREADY_LOCKED"
                if str(exc) == "daily_runner_already_locked"
                else "PAPER_STATE_UNAVAILABLE"
            )
            raise VisualWorkbenchError(code) from exc
        except OptionCDailyError as exc:
            raise VisualWorkbenchError("PAPER_RUN_REJECTED") from exc
        except ContinuousRunError as exc:
            raise VisualWorkbenchError("PAPER_RUN_REJECTED") from exc
        dashboard = self.dashboard(target)
        return {**dashboard, "run_status": result.status}
