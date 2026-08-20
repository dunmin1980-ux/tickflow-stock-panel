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
    run_daily_once,
)
from app.services.phase2_option_c_fixture import (
    ReferenceFixtureError,
    load_reference_fixture,
)
from app.services.phase2_option_c_state import OptionCStateError, OptionCStateStore
from app.services.phase2_visual_daily_input import (
    Phase2VisualDailyInputBuilder,
    VisualDailyInputError,
    load_validated_daily_bundle,
)


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

    def __init__(
        self,
        *,
        repo_root: Path,
        state_root: Path,
        input_root: Path,
        daily_input_builder: Phase2VisualDailyInputBuilder | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve(strict=True)
        self.state_root = state_root
        self.input_root = input_root
        self._ensure_parent(self.state_root.parent)
        self._ensure_parent(self.input_root)
        self.daily_input_builder = daily_input_builder or Phase2VisualDailyInputBuilder(
            input_root=self.input_root
        )

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

    @staticmethod
    def _translate_input_error(exc: VisualDailyInputError) -> VisualWorkbenchError:
        code = str(exc).split(":", 1)[0]
        if code == "MARKET_NOT_CLOSED":
            return VisualWorkbenchError("PAPER_MARKET_NOT_CLOSED")
        if code == "MARKET_SOURCE_UNAVAILABLE":
            return VisualWorkbenchError("PAPER_MARKET_SOURCE_UNAVAILABLE")
        if code == "INPUT_PREPARATION_ALREADY_LOCKED":
            return VisualWorkbenchError("PAPER_RUN_ALREADY_LOCKED")
        if code == "CORPORATE_ACTION_ACCOUNTING_UNSUPPORTED":
            return VisualWorkbenchError("PAPER_CORPORATE_ACTION_REVIEW_REQUIRED")
        if code in {
            "HISTORICAL_LIVE_PREPARATION_FORBIDDEN",
            "LATEST_TRADE_DATE_MISMATCH",
            "VALIDATED_DAILY_BUNDLE_MISSING",
        }:
            return VisualWorkbenchError("PAPER_INPUT_NOT_READY")
        return VisualWorkbenchError("PAPER_INPUT_INVALID")

    @staticmethod
    def _readiness_payload(target: date, readiness: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": readiness["status"],
            "requested_date": target.isoformat(),
            "effective_trade_date": readiness.get("effective_trade_date"),
            "source_fixture": readiness.get("source_fixture", "VALIDATED_DAILY_INPUT"),
            "is_current_date": readiness["status"] != "BLOCKED",
            "message": readiness["message"],
        }

    def _resolve_input(
        self,
        target: date,
        *,
        state,
    ) -> tuple[ContinuousDayInput | None, dict[str, Any]]:
        published_dates = {entry.trade_date for entry in state.decision_ledger}
        reference = self.reference_input()
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
        try:
            readiness = self.daily_input_builder.readiness(target)
            day_input = None
            if readiness["status"] == "READY_VALIDATED_DAILY":
                day_input = load_validated_daily_bundle(self.input_root, target).day_input
                if target in published_dates:
                    readiness = {
                        **readiness,
                        "status": "ALREADY_PUBLISHED",
                        "message": "VALIDATED_DAILY_ALREADY_PUBLISHED",
                    }
            return day_input, self._readiness_payload(target, readiness)
        except VisualDailyInputError as exc:
            raise self._translate_input_error(exc) from exc

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

    def _claims(self, target: date, state) -> dict[str, Any]:
        candidates = [target]
        if state.last_completed_trade_date is not None:
            candidates.append(state.last_completed_trade_date)
        for trade_date in candidates:
            if not (self.input_root / trade_date.isoformat()).is_dir():
                continue
            try:
                validation = load_validated_daily_bundle(
                    self.input_root, trade_date
                ).validation
            except VisualDailyInputError as exc:
                raise self._translate_input_error(exc) from exc
            return {
                **validation.to_dict(),
                "status": "VALID" if validation.status == "CLAIMS_VALID" else "INVALID",
                "source_fixture": "VALIDATED_DAILY_INPUT",
                "trade_date": trade_date.isoformat(),
            }
        try:
            validation = load_reference_fixture(self.repo_root).validation
        except ReferenceFixtureError as exc:
            raise VisualWorkbenchError("PAPER_REFERENCE_INVALID") from exc
        return {
            **validation.to_dict(),
            "status": "VALID" if validation.status == "CLAIMS_VALID" else "INVALID",
            "source_fixture": "DETERMINISTIC_REFERENCE_FIXTURE",
            "trade_date": self.reference_input().trade_date.isoformat(),
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
            "claims": self._claims(target, state),
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
        if day_input is None and readiness["status"] == "PREPARABLE":
            try:
                day_input = self.daily_input_builder.prepare(target, state).day_input
            except VisualDailyInputError as exc:
                raise self._translate_input_error(exc) from exc
        if day_input is None:
            raise VisualWorkbenchError("PAPER_INPUT_NOT_READY")
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
