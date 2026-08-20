from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import paper_trading as paper_trading_api
from app.services.phase2_visual_workbench import (
    Phase2VisualWorkbenchService,
    VisualWorkbenchError,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 8, 20)
REFERENCE_DATE = date(2026, 7, 31)


def service_for(tmp_path: Path) -> Phase2VisualWorkbenchService:
    return Phase2VisualWorkbenchService(
        repo_root=REPO_ROOT,
        state_root=tmp_path / "state",
        input_root=tmp_path / "inputs",
    )


def test_empty_dashboard_is_safe_and_reference_ready(tmp_path: Path) -> None:
    dashboard = service_for(tmp_path).dashboard(TODAY)

    assert dashboard["status"] == "VISUAL_WORKBENCH_READY"
    assert dashboard["symbol"] == "000403.SZ"
    assert dashboard["safety"] == {
        "simulation_only": "SIMULATION ONLY",
        "real_trading": "DISABLED",
        "can_publish": False,
        "trading_advice": False,
    }
    assert dashboard["input_readiness"] == {
        "status": "READY_REFERENCE",
        "requested_date": "2026-08-20",
        "effective_trade_date": "2026-07-31",
        "source_fixture": "DETERMINISTIC_REFERENCE_FIXTURE",
        "is_current_date": False,
        "message": "FROZEN_REFERENCE_INITIALIZATION_AVAILABLE",
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


def test_one_click_reference_run_persists_hold_and_daily(tmp_path: Path) -> None:
    service = service_for(tmp_path)

    result = service.run(TODAY)
    recovered = service_for(tmp_path).dashboard(TODAY)

    assert result["run_status"] == "DAY_PUBLISHED"
    assert result["latest_daily"]["report_date"] == "2026-07-31"
    assert result["latest_daily"]["research_signal"] == "MIXED_OBSERVATION"
    assert result["latest_daily"]["paper_action"] == "HOLD"
    assert result["decisions"][0]["paper_action"] == "HOLD"
    assert result["trades"] == []
    assert result["account"]["cash_cny"] == "100000.00"
    assert result["account"]["total_equity_cny"] == "100000.00"
    assert "SIMULATION ONLY" in result["chenquant_daily_markdown"]
    assert recovered["latest_daily"] == result["latest_daily"]
    assert recovered["decisions"] == result["decisions"]
    assert recovered["equity_history"] == result["equity_history"]
    assert recovered["input_readiness"]["status"] == "MISSING"


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

    with pytest.raises(VisualWorkbenchError, match="PAPER_INPUT_MISSING"):
        service.run(TODAY)

    after = service.dashboard(TODAY)
    assert after["decisions"] == before["decisions"]
    assert after["trades"] == before["trades"]
    assert after["equity_history"] == before["equity_history"]


def test_staged_input_date_mismatch_fails_before_engine(tmp_path: Path) -> None:
    service = service_for(tmp_path)
    source = service.reference_input()
    target = service.input_root / "2026-08-20_input.json"
    target.write_bytes(source.model_dump_json().encode())

    with pytest.raises(VisualWorkbenchError, match="PAPER_INPUT_DATE_MISMATCH"):
        service.run(TODAY)
    target.unlink()
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
