from __future__ import annotations

import copy
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import paper_trading as paper_trading_api
from app.services.phase2_visual_daily_input import (
    DailyMarketSnapshot,
    Phase2VisualDailyInputBuilder,
)
from app.services.phase2_visual_workbench import (
    Phase2VisualWorkbenchService,
    VisualWorkbenchError,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 8, 20)
REFERENCE_DATE = date(2026, 7, 31)


class FakeDailyGateway:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, symbol: str, requested_date: date) -> DailyMarketSnapshot:
        self.calls += 1
        rows: list[dict] = []
        trading_dates: list[date] = []
        cursor = requested_date
        while len(trading_dates) < 100:
            if cursor.weekday() < 5:
                trading_dates.insert(0, cursor)
            cursor = date.fromordinal(cursor.toordinal() - 1)
        for index, trade_date in enumerate(trading_dates):
            close = 10 + (index % 3) * 0.01
            rows.append(
                {
                    "trade_date": trade_date.isoformat(),
                    "open": close - 0.01,
                    "high": close + 0.05,
                    "low": close - 0.05,
                    "close": close,
                    "volume": 100000 + index,
                    "amount": (100000 + index) * close,
                }
            )
        return DailyMarketSnapshot(
            source_provider="stocksdk_tencent_fallback",
            symbol=symbol,
            requested_date=requested_date,
            fetched_at=datetime.fromisoformat(
                f"{requested_date.isoformat()}T16:20:00+08:00"
            ),
            raw_rows=copy.deepcopy(rows),
            qfq_rows=copy.deepcopy(rows),
            trading_dates=[trading_dates[-2], requested_date, date(2026, 8, 21)],
            request_count=3,
        )


def daily_builder(tmp_path: Path, gateway: FakeDailyGateway) -> Phase2VisualDailyInputBuilder:
    return Phase2VisualDailyInputBuilder(
        input_root=tmp_path / "inputs",
        market_gateway=gateway,
        now=lambda: datetime.fromisoformat("2026-08-20T16:20:00+08:00"),
    )


def service_for(
    tmp_path: Path,
    *,
    builder: Phase2VisualDailyInputBuilder | None = None,
) -> Phase2VisualWorkbenchService:
    return Phase2VisualWorkbenchService(
        repo_root=REPO_ROOT,
        state_root=tmp_path / "state",
        input_root=tmp_path / "inputs",
        daily_input_builder=builder,
    )


def test_empty_dashboard_is_safe_and_current_input_is_preparable(tmp_path: Path) -> None:
    gateway = FakeDailyGateway()
    dashboard = service_for(
        tmp_path,
        builder=daily_builder(tmp_path, gateway),
    ).dashboard(TODAY)

    assert dashboard["status"] == "VISUAL_WORKBENCH_READY"
    assert dashboard["symbol"] == "000403.SZ"
    assert dashboard["safety"] == {
        "simulation_only": "SIMULATION ONLY",
        "real_trading": "DISABLED",
        "can_publish": False,
        "trading_advice": False,
    }
    assert dashboard["input_readiness"] == {
        "status": "PREPARABLE",
        "requested_date": "2026-08-20",
        "effective_trade_date": None,
        "source_fixture": "VALIDATED_DAILY_INPUT",
        "is_current_date": True,
        "message": "VALIDATED_DAILY_INPUT_CAN_BE_PREPARED",
    }
    assert dashboard["claims"]["status"] == "VALID"
    assert dashboard["claims"]["unsourced_claim_count"] == 0
    assert dashboard["claims"]["trading_claim_count"] == 0
    assert dashboard["account"]["cash_cny"] == "100000.00"
    assert dashboard["account"]["total_equity_cny"] == "100000.00"
    assert dashboard["account"]["cumulative_return_percent"] == "0.0000"
    assert dashboard["positions"] == []
    assert dashboard["decisions"] == []
    assert dashboard["trades"] == []
    assert dashboard["equity_history"] == []
    assert dashboard["latest_daily"] is None
    assert dashboard["chenquant_daily_markdown"] is None


def test_one_click_prepares_current_input_once_and_persists_daily(tmp_path: Path) -> None:
    gateway = FakeDailyGateway()
    builder = daily_builder(tmp_path, gateway)
    service = service_for(tmp_path, builder=builder)

    first = service.run(TODAY)
    second = service.run(TODAY)
    recovered = service_for(tmp_path, builder=builder).dashboard(TODAY)

    assert first["run_status"] == "DAY_PUBLISHED"
    assert second["run_status"] == "DAY_ALREADY_PUBLISHED"
    assert first["latest_daily"]["report_date"] == "2026-08-20"
    assert first["latest_daily"]["source_fixture"] == "VALIDATED_DAILY_INPUT"
    assert "VALIDATED_DAILY_INPUT" in first["latest_daily"]["risk_notes"]
    assert "DETERMINISTIC_TEST_FIXTURE_ONLY" not in first["latest_daily"]["risk_notes"]
    assert first["decisions"][0]["trade_date"] == "2026-08-20"
    assert first["decisions"][0]["input_identity"]
    assert first["trades"] == []
    assert first["account"]["cash_cny"] == "100000.00"
    assert first["account"]["total_equity_cny"] == "100000.00"
    assert "SIMULATION ONLY" in first["chenquant_daily_markdown"]
    assert recovered["latest_daily"] == first["latest_daily"]
    assert recovered["decisions"] == first["decisions"]
    assert recovered["equity_history"] == first["equity_history"]
    assert recovered["input_readiness"]["status"] == "ALREADY_PUBLISHED"
    assert recovered["claims"]["status"] == "VALID"
    assert gateway.calls == 1


def test_exact_reference_run_is_idempotent(tmp_path: Path) -> None:
    service = service_for(tmp_path)
    first = service.run(REFERENCE_DATE)
    second = service.run(REFERENCE_DATE)

    assert first["run_status"] == "DAY_PUBLISHED"
    assert second["run_status"] == "DAY_ALREADY_PUBLISHED"
    assert second["decisions"] == first["decisions"]
    assert second["trades"] == first["trades"]
    assert second["equity_history"] == first["equity_history"]


def test_missing_new_daily_input_fails_closed_without_mutation(tmp_path: Path) -> None:
    service = service_for(tmp_path)
    service.run(REFERENCE_DATE)
    before = service.dashboard(TODAY)

    with pytest.raises(VisualWorkbenchError, match="PAPER_INPUT_NOT_READY"):
        service.run(TODAY)

    after = service.dashboard(TODAY)
    assert after["decisions"] == before["decisions"]
    assert after["trades"] == before["trades"]
    assert after["equity_history"] == before["equity_history"]


def test_legacy_staged_input_is_ignored_before_engine(tmp_path: Path) -> None:
    service = service_for(tmp_path)
    source = service.reference_input()
    target = service.input_root / "2026-08-20_input.json"
    target.write_bytes(source.model_dump_json().encode())

    with pytest.raises(VisualWorkbenchError, match="PAPER_INPUT_NOT_READY"):
        service.run(TODAY)
    assert target.is_file()
    assert service.dashboard(TODAY)["decisions"] == []


class FakeVisualService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, date]] = []

    def dashboard(self, target: date):
        self.calls.append(("dashboard", target))
        return {"status": "VISUAL_WORKBENCH_READY", "target": target.isoformat()}

    def run(self, target: date):
        self.calls.append(("run", target))
        return {"status": "VISUAL_WORKBENCH_READY", "run_status": "DAY_PUBLISHED"}


def test_api_uses_app_service_and_defaults_to_shanghai_today(monkeypatch) -> None:
    fake = FakeVisualService()
    test_app = FastAPI()
    test_app.state.phase2_visual_workbench = fake
    test_app.include_router(paper_trading_api.router)
    monkeypatch.setattr(paper_trading_api, "shanghai_today", lambda: TODAY)

    with TestClient(test_app) as client:
        dashboard = client.get("/api/paper-trading/dashboard")
        run = client.post("/api/paper-trading/run", json={})

    assert dashboard.status_code == 200
    assert run.status_code == 200
    assert fake.calls == [("dashboard", TODAY), ("run", TODAY)]


@pytest.mark.parametrize(
    ("code", "status_code"),
    [
        ("PAPER_INPUT_MISSING", 409),
        ("PAPER_INPUT_NOT_READY", 409),
        ("PAPER_MARKET_NOT_CLOSED", 409),
        ("PAPER_MARKET_SOURCE_UNAVAILABLE", 503),
        ("PAPER_CORPORATE_ACTION_REVIEW_REQUIRED", 409),
        ("PAPER_INPUT_DATE_MISMATCH", 422),
        ("PAPER_STATE_UNAVAILABLE", 503),
    ],
)
def test_api_returns_stable_friendly_errors(code: str, status_code: int) -> None:
    class BrokenService:
        def run(self, _target: date):
            raise VisualWorkbenchError(code)

    test_app = FastAPI()
    test_app.state.phase2_visual_workbench = BrokenService()
    test_app.include_router(paper_trading_api.router)

    with TestClient(test_app) as client:
        response = client.post(
            "/api/paper-trading/run",
            json={"target_date": "2026-08-20"},
        )

    assert response.status_code == status_code
    assert response.json() == {
        "detail": {
            "code": code,
            "message": paper_trading_api.ERROR_MESSAGES[code],
        }
    }


def test_router_is_mounted_in_main_app() -> None:
    from app.main import app

    paths = {route.path for route in app.routes}
    assert "/api/paper-trading/dashboard" in paths
    assert "/api/paper-trading/run" in paths
