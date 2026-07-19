from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main
from app.config import Settings


def runtime_app(scheduler):
    return SimpleNamespace(
        state=SimpleNamespace(
            scheduler=scheduler,
            capabilities=object(),
        )
    )


def test_gold_workspace_flag_defaults_false(monkeypatch):
    monkeypatch.delenv("GOLD_WORKSPACE_ENABLED", raising=False)

    assert Settings(_env_file=None).gold_workspace_enabled is False


def test_disabled_runtime_does_not_initialize_gold_storage(monkeypatch, tmp_path):
    app = runtime_app(SimpleNamespace())
    monkeypatch.setattr(main, "settings", SimpleNamespace(gold_workspace_enabled=False))

    def unexpected_store(_data_dir):
        raise AssertionError("disabled Gold runtime initialized storage")

    monkeypatch.setattr(main, "GoldShadowStore", unexpected_store, raising=False)

    main._initialize_gold_runtime(app, SimpleNamespace(data_dir=tmp_path))

    assert app.state.gold_shadow_service is None
    assert app.state.gold_store is None
    assert app.state.gold_legacy_importer is None
    assert app.state.gold_comparison_runner is None
    assert app.state.gold_observation_service is None
    assert app.state.gold_sampler_registered is False
    assert not (tmp_path / "user_data" / "gold_shadow").exists()


def test_enabled_runtime_initializes_services_and_registers_five_minute_job(
    monkeypatch, tmp_path
):
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    app = runtime_app(scheduler)
    monkeypatch.setattr(main, "settings", SimpleNamespace(gold_workspace_enabled=True))

    main._initialize_gold_runtime(app, SimpleNamespace(data_dir=tmp_path))

    assert app.state.gold_shadow_service is not None
    assert app.state.gold_store is not None
    assert app.state.gold_legacy_importer is not None
    assert app.state.gold_comparison_runner is not None
    assert app.state.gold_observation_service is not None
    assert app.state.gold_sampler_registered is True
    job = scheduler.get_job("gold_sampler_5m")
    assert job is not None
    assert str(job.trigger) == "cron[day_of_week='mon-fri', minute='*/5']"


def test_enabled_runtime_without_scheduler_remains_readable_and_records_failure(
    monkeypatch, tmp_path
):
    app = runtime_app(None)
    monkeypatch.setattr(main, "settings", SimpleNamespace(gold_workspace_enabled=True))

    main._initialize_gold_runtime(app, SimpleNamespace(data_dir=tmp_path))

    status = app.state.gold_shadow_service.status()
    assert app.state.gold_sampler_registered is False
    assert status["enabled"] is True
    assert status["health"]["consecutive_failures"] == 1
    assert status["health"]["last_error"] == {
        "code": "scheduler_unavailable",
        "message": "Gold sampler scheduler is unavailable",
    }


def test_scheduler_registration_failure_does_not_abort_app_lifespan(
    monkeypatch, tmp_path
):
    class RejectingScheduler:
        def add_job(self, *_args, **_kwargs):
            raise RuntimeError("scheduler-registration-secret")

    @asynccontextmanager
    async def test_lifespan(app):
        app.state.scheduler = RejectingScheduler()
        app.state.capabilities = object()
        main._initialize_gold_runtime(app, SimpleNamespace(data_dir=tmp_path))
        yield

    monkeypatch.setattr(main, "settings", SimpleNamespace(gold_workspace_enabled=True))
    test_app = FastAPI(lifespan=test_lifespan)
    test_app.include_router(main.gold.router)

    @test_app.get("/api/runtime-probe")
    def runtime_probe():
        return {"ready": True}

    with TestClient(test_app) as client:
        normal_response = client.get("/api/runtime-probe")
        gold_status_response = client.get("/api/gold/status")
        gold_health_response = client.get("/api/gold/health")

    assert normal_response.status_code == 200
    assert normal_response.json() == {"ready": True}
    assert test_app.state.gold_sampler_registered is False
    assert gold_status_response.status_code == 200
    assert gold_status_response.json()["health"]["last_error"] == {
        "code": "scheduler_unavailable",
        "message": "Gold sampler scheduler is unavailable",
    }
    assert gold_health_response.status_code == 200
    assert gold_health_response.json()["next_scheduled_run"] is None
    assert gold_health_response.json()["last_error"] == {
        "code": "scheduler_unavailable",
        "message": "Gold sampler scheduler is unavailable",
    }
    assert "scheduler-registration-secret" not in gold_status_response.text
    assert "scheduler-registration-secret" not in gold_health_response.text
    health_text = (tmp_path / "user_data" / "gold_shadow" / "health.json").read_text()
    assert "scheduler-registration-secret" not in health_text


def test_unrelated_gold_initialization_failure_is_not_masked(monkeypatch, tmp_path):
    app = runtime_app(SimpleNamespace())
    monkeypatch.setattr(main, "settings", SimpleNamespace(gold_workspace_enabled=True))

    def broken_store(_data_dir):
        raise RuntimeError("gold store construction failed")

    monkeypatch.setattr(main, "GoldShadowStore", broken_store)

    with pytest.raises(RuntimeError, match="gold store construction failed"):
        main._initialize_gold_runtime(app, SimpleNamespace(data_dir=tmp_path))
