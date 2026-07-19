from __future__ import annotations

from types import SimpleNamespace

from apscheduler.schedulers.asyncio import AsyncIOScheduler

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
    assert status["enabled"] is True
    assert status["health"]["consecutive_failures"] == 1
    assert status["health"]["last_error"] == {
        "code": "scheduler_unavailable",
        "message": "Gold sampler scheduler is unavailable",
    }
