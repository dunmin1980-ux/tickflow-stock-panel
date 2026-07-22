from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException

from app.api import backtest as backtest_api
from app.api import screener as screener_api
from app.backtest.worker import (
    BacktestWorkerError,
    BacktestWorkerInfrastructureError,
)
from app.desktop_client.workspace_adapter import ComputeInputIntegrityError
from app.services.compute_input_bundle import (
    ComputeInputDataUnavailable,
    ComputeInputFile,
    ComputeInputManifest,
    compute_coverage_policy,
    compute_parameters_digest,
    normalize_compute_config,
)
from app.services.compute_router import (
    ComputeRouter,
    LocalComputeUnavailable,
    lease_compute_input,
    open_read_only_compute_repository,
    prepare_local_compute_input,
    run_local_worker,
)
from app.workspace.models import (
    ResourceName,
    ResourceSnapshot,
    WorkspaceRevisionConflict,
)


def _raise(error: BaseException):
    raise error


def _http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://tickflow.invalid/api/compute")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}",
        request=request,
        response=response,
    )


def _request_with_state(**state):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state)))


def _snapshot(revision: str = "a" * 64) -> ResourceSnapshot:
    return ResourceSnapshot(
        resource=ResourceName.BACKTEST_SUMMARIES,
        revision=revision,
        updated_at=datetime(2026, 7, 22, tzinfo=UTC),
        data={"summaries": []},
    )


def _compute_dir(
    root: Path,
    task: str,
    config: dict,
    *,
    data_as_of: str = "2026-07-22",
    generation: str = "A",
) -> Path:
    normalized = normalize_compute_config(task, config)
    reference = date.fromisoformat(data_as_of)
    coverage = compute_coverage_policy(task, normalized, reference_date=reference)
    required_roots = (
        ("adj_factor", "instruments", "kline_daily", "kline_daily_enriched")
        if task == "strategy_backtest"
        else ("instruments", "kline_daily_enriched")
    )
    files = []
    total_size = 0
    root.mkdir(parents=True)
    for root_name in required_roots:
        relative_path = f"{root_name}/part.parquet"
        content = f"{generation}:{root_name}".encode()
        target = root / relative_path
        target.parent.mkdir()
        target.write_bytes(content)
        total_size += len(content)
        files.append(
            ComputeInputFile(
                path=relative_path,
                size=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )
        )
    manifest = ComputeInputManifest(
        schema_version=1,
        task=task,
        parameters_digest=compute_parameters_digest(normalized),
        data_as_of=data_as_of,
        coverage_start=data_as_of,
        coverage_end=data_as_of,
        partition_dates=[data_as_of],
        coverage=coverage,
        files=files,
        total_uncompressed_bytes=total_size,
    )
    (root / "manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    return root


class _RemoteStub:
    def __init__(self, response: httpx.Response | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, path: str, **kwargs):
        self.calls.append((method, path, kwargs))
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


class _AdapterStub:
    def __init__(self, compute_dir: Path, remote: _RemoteStub):
        self.compute_dir = compute_dir
        self.remote = remote
        self.prepare_error: BaseException | None = None
        self.prepare_calls: list[tuple[str, dict]] = []
        self.get_calls = 0
        self.command_calls: list[tuple] = []
        self.command_error: BaseException | None = None

    def prepare_compute_input(self, task: str, config: dict) -> Path:
        self.prepare_calls.append((task, config))
        if self.prepare_error is not None:
            raise self.prepare_error
        return self.compute_dir

    def _remote(self) -> _RemoteStub:
        return self.remote

    def get(self, resource: ResourceName) -> ResourceSnapshot:
        assert resource is ResourceName.BACKTEST_SUMMARIES
        self.get_calls += 1
        return _snapshot(("a" if self.get_calls == 1 else "b") * 64)

    def command(
        self,
        resource: ResourceName,
        operation: str,
        payload: dict,
        revision: str,
    ) -> ResourceSnapshot:
        self.command_calls.append((resource, operation, payload, revision))
        if self.command_error is not None:
            raise self.command_error
        return _snapshot("c" * 64)


def _http_json(status_code: int, payload: dict) -> httpx.Response:
    request = httpx.Request("POST", "https://tickflow.invalid/api/compute")
    return httpx.Response(status_code, request=request, json=payload)


def _backtest_result() -> dict:
    return {
        "run_id": "bt_run_1",
        "config": {"end": "2026-07-22"},
        "stats": {
            "total_return": 0.03,
            "max_drawdown": -0.01,
            "sharpe": 1.2,
            "profit_factor": "REPORT_BODY_CANARY",
            "worker": {"pid": 123, "secret_key": "MUST_NOT_SYNC"},
        },
        "equity_curve": [{"date": "2026-07-22", "equity": 1.03}],
        "trades": [{"symbol": "000403.SZ"}],
        "error": None,
    }


def test_local_infrastructure_failure_falls_back_once() -> None:
    local = Mock(side_effect=LocalComputeUnavailable("worker_start_failed"))
    cloud = Mock(return_value={"stats": {"total_return": 0.01}})

    result = ComputeRouter(cloud_enabled=True).execute(
        "strategy_backtest",
        local,
        cloud,
    )

    assert result.execution_target == "cloud"
    assert result.value == {"stats": {"total_return": 0.01}}
    local.assert_called_once_with()
    cloud.assert_called_once_with()


def test_local_success_never_calls_cloud() -> None:
    cloud = Mock()

    result = ComputeRouter(cloud_enabled=True).execute(
        "screener",
        lambda: {"rows": []},
        cloud,
    )

    assert result.execution_target == "local"
    assert result.value == {"rows": []}
    cloud.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        ValueError("bad parameters"),
        PermissionError("capability denied"),
        ComputeInputIntegrityError("digest mismatch"),
        ComputeInputDataUnavailable("coverage unavailable"),
        _http_error(429),
        _http_error(409),
    ],
)
def test_non_infrastructure_errors_never_fall_back(error: BaseException) -> None:
    cloud = Mock()

    with pytest.raises(type(error)) as raised:
        ComputeRouter(cloud_enabled=True).execute(
            "strategy_backtest",
            lambda: _raise(error),
            cloud,
        )

    assert raised.value is error
    cloud.assert_not_called()


def test_disabled_cloud_re_raises_original_local_failure() -> None:
    error = LocalComputeUnavailable("worker_start_failed")
    cloud = Mock()

    with pytest.raises(LocalComputeUnavailable) as raised:
        ComputeRouter(cloud_enabled=False).execute(
            "strategy_backtest",
            lambda: _raise(error),
            cloud,
        )

    assert raised.value is error
    cloud.assert_not_called()


def test_cloud_failure_is_not_retried() -> None:
    cloud_error = _http_error(503)
    cloud = Mock(side_effect=cloud_error)

    with pytest.raises(httpx.HTTPStatusError) as raised:
        ComputeRouter(cloud_enabled=True).execute(
            "strategy_backtest",
            lambda: _raise(LocalComputeUnavailable("worker_start_failed")),
            cloud,
        )

    assert raised.value is cloud_error
    cloud.assert_called_once_with()


def test_router_logs_only_bounded_metadata(caplog: pytest.LogCaptureFixture) -> None:
    canary = "SECRET_PARAMETER_AND_COOKIE_CANARY"
    caplog.set_level(logging.INFO, logger="app.services.compute_router")

    ComputeRouter(cloud_enabled=True).execute(
        "strategy_backtest",
        lambda: _raise(LocalComputeUnavailable("worker_start_failed", detail=canary)),
        lambda: {"content": canary},
    )

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert canary not in log_text
    assert "task_id=" in log_text
    assert "target=local" in log_text
    assert "target=cloud" in log_text
    assert "elapsed_ms=" in log_text
    assert "error_code=worker_start_failed" in log_text


def test_router_rejects_unsupported_task_before_execution() -> None:
    local = Mock()
    cloud = Mock()

    with pytest.raises(ValueError, match="unsupported compute task"):
        ComputeRouter(cloud_enabled=True).execute("market_recap", local, cloud)

    local.assert_not_called()
    cloud.assert_not_called()


def test_unknown_local_failure_code_cannot_enable_fallback() -> None:
    with pytest.raises(ValueError, match="unsupported local infrastructure error"):
        LocalComputeUnavailable("business_error")


@pytest.mark.parametrize(
    ("error", "error_code"),
    [
        (httpx.ConnectError("offline"), "compute_input_transport_unavailable"),
        (httpx.ReadTimeout("slow"), "compute_input_download_timeout"),
    ],
)
def test_prepare_boundary_converts_only_transport_failures(
    error: httpx.RequestError,
    error_code: str,
) -> None:
    adapter = Mock()
    adapter.prepare_compute_input.side_effect = error

    with pytest.raises(LocalComputeUnavailable) as raised:
        prepare_local_compute_input(adapter, "screener", {"conditions": ["close > ma20"]})

    assert raised.value.error_code == error_code
    adapter.prepare_compute_input.assert_called_once_with(
        "screener",
        {"conditions": ["close > ma20"]},
    )


@pytest.mark.parametrize(
    "error",
    [
        ComputeInputIntegrityError("freshness mismatch"),
        ComputeInputDataUnavailable("coverage unavailable"),
        PermissionError("capability denied"),
        _http_error(429),
        ValueError("digest mismatch"),
    ],
)
def test_prepare_boundary_preserves_non_transport_errors(error: BaseException) -> None:
    adapter = Mock()
    adapter.prepare_compute_input.side_effect = error

    with pytest.raises(type(error)) as raised:
        prepare_local_compute_input(adapter, "screener", {"conditions": ["close > ma20"]})

    assert raised.value is error


def test_prepare_boundary_returns_verified_directory() -> None:
    adapter = Mock()
    adapter.prepare_compute_input.return_value = Path("/verified/compute-input")

    result = prepare_local_compute_input(
        adapter,
        "strategy_backtest",
        {"strategy_id": "macd_golden"},
    )

    assert result == Path("/verified/compute-input")


def test_local_repository_initialization_failure_is_classified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.compute_router.tempfile.TemporaryDirectory",
        Mock(side_effect=OSError("disk unavailable")),
    )

    with pytest.raises(LocalComputeUnavailable) as raised:
        open_read_only_compute_repository(Path("/verified/compute-input"))

    assert raised.value.error_code == "local_repo_init_failed"


def test_local_repository_rejects_snapshot_root_symlink_as_integrity_failure(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "snapshot"
    outside = tmp_path / "outside"
    snapshot.mkdir()
    outside.mkdir()
    (snapshot / "kline_daily").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ComputeInputIntegrityError, match="root is invalid"):
        open_read_only_compute_repository(snapshot)


def test_compute_lease_pins_one_cache_generation_during_atomic_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {
        "strategy_id": "macd_golden",
        "start": "2026-07-22",
        "end": "2026-07-22",
    }
    published = _compute_dir(
        tmp_path / "cache-key",
        "strategy_backtest",
        config,
        generation="A",
    )
    replacement = _compute_dir(
        tmp_path / "replacement",
        "strategy_backtest",
        config,
        generation="B",
    )
    retired = tmp_path / "retired"
    from app.services import compute_router as compute_router_service

    original_copy = compute_router_service._copy_manifest_entry
    replaced = False

    def replace_before_first_open(*args, **kwargs):
        nonlocal replaced
        if not replaced:
            replaced = True
            os.replace(published, retired)
            os.replace(replacement, published)
        return original_copy(*args, **kwargs)

    monkeypatch.setattr(
        compute_router_service,
        "_copy_manifest_entry",
        replace_before_first_open,
    )

    with lease_compute_input(published, "strategy_backtest", config) as lease:
        lease_path = lease.path
        copied = {
            entry.path: (lease.path / entry.path).read_bytes()
            for entry in lease.manifest.files
        }
        assert copied
        assert all(content.startswith(b"A:") for content in copied.values())
        assert not any(content.startswith(b"B:") for content in copied.values())
        assert lease.path != published
        assert lease.path.stat().st_mode & 0o222 == 0

    assert not lease_path.exists()


def test_compute_lease_copies_only_manifest_authenticated_data_roots(tmp_path: Path) -> None:
    config = {
        "strategy_id": "macd_golden",
        "start": "2026-07-22",
        "end": "2026-07-22",
    }
    published = _compute_dir(
        tmp_path / "cache-key",
        "strategy_backtest",
        config,
    )
    malicious = published / "strategies" / "custom" / "malicious.py"
    malicious.parent.mkdir(parents=True)
    malicious.write_text("raise RuntimeError('must not execute')", encoding="utf-8")

    with lease_compute_input(published, "strategy_backtest", config) as lease:
        assert not (lease.path / "strategies").exists()
        assert {path.parts[0] for path in map(Path, (item.path for item in lease.manifest.files))} <= {
            "adj_factor",
            "instruments",
            "kline_daily",
            "kline_daily_enriched",
        }


def test_compute_lease_rejects_same_size_data_tampering_without_cloud(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = backtest_api.StrategyBacktestRequest(
        strategy_id="macd_golden",
        start=date(2026, 7, 22),
        end=date(2026, 7, 22),
    )
    raw_config = req.model_dump(mode="json", exclude_unset=True)
    compute_dir = _compute_dir(tmp_path / "verified", "strategy_backtest", raw_config)
    target = compute_dir / "kline_daily" / "part.parquet"
    target.write_bytes(b"X" * target.stat().st_size)
    adapter = _AdapterStub(compute_dir, _RemoteStub())
    worker = Mock()
    monkeypatch.setattr(backtest_api, "_run_strategy_worker", worker)

    with pytest.raises(ComputeInputIntegrityError, match="integrity does not match"):
        backtest_api.strategy_run(req, _request_with_state(workspace_adapter=adapter))

    worker.assert_not_called()
    assert adapter.remote.calls == []


@pytest.mark.parametrize(
    "error_code",
    [
        "worker_start_failed",
        "native_library_unavailable",
        "local_repo_init_failed",
    ],
)
def test_worker_boundary_converts_only_typed_infrastructure_errors(
    error_code: str,
) -> None:
    error = BacktestWorkerInfrastructureError(error_code)

    with pytest.raises(LocalComputeUnavailable) as raised:
        run_local_worker(lambda: _raise(error))

    assert raised.value.error_code == error_code


def test_worker_exit_failure_is_not_fallback_eligible() -> None:
    with pytest.raises(ValueError, match="not fallback eligible"):
        BacktestWorkerInfrastructureError("worker_exit_failed")


@pytest.mark.parametrize(
    "error",
    [
        BacktestWorkerError("unknown strategy"),
        ValueError("bad parameters"),
        PermissionError("capability denied"),
        ComputeInputIntegrityError("manifest mismatch"),
    ],
)
def test_worker_boundary_preserves_business_and_integrity_errors(
    error: BaseException,
) -> None:
    with pytest.raises(type(error)) as raised:
        run_local_worker(lambda: _raise(error))

    assert raised.value is error


def test_desktop_strategy_run_prepares_verified_input_and_writes_safe_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = backtest_api.StrategyBacktestRequest(
        strategy_id="macd_golden",
        symbols=["000403.SZ"],
        start=date(2026, 7, 22),
        end=date(2026, 7, 22),
    )
    raw_config = req.model_dump(mode="json", exclude_unset=True)
    compute_dir = _compute_dir(tmp_path / "verified", "strategy_backtest", raw_config)
    remote = _RemoteStub()
    adapter = _AdapterStub(compute_dir, remote)
    worker_result = _backtest_result()
    worker_result["run_id"] = "token=RUN_ID_CANARY"
    worker = Mock(return_value=worker_result)
    monkeypatch.setattr(backtest_api, "_run_strategy_worker", worker)

    payload = backtest_api.strategy_run(req, _request_with_state(workspace_adapter=adapter))

    assert payload["execution_target"] == "local"
    assert payload["summary_sync"] == {"status": "saved", "revision": "c" * 64}
    assert adapter.prepare_calls == [("strategy_backtest", raw_config)]
    assert worker.call_count == 1
    leased_dir = worker.call_args.args[0]
    assert leased_dir != compute_dir
    assert not leased_dir.exists()
    assert worker.call_args.kwargs["read_only_snapshot"] is True
    assert remote.calls == []
    assert len(adapter.command_calls) == 1
    resource, operation, summary, revision = adapter.command_calls[0]
    assert resource is ResourceName.BACKTEST_SUMMARIES
    assert operation == "append"
    assert revision == "a" * 64
    assert set(summary) == {
        "id",
        "task",
        "strategy_id",
        "parameters_digest",
        "stats",
        "started_at",
        "finished_at",
        "data_as_of",
        "engine",
        "execution_target",
    }
    assert summary["execution_target"] == "local"
    assert summary["data_as_of"] == "2026-07-22"
    assert summary["stats"] == {
        "max_drawdown": -0.01,
        "sharpe": 1.2,
        "total_return": 0.03,
    }
    assert "trades" not in summary
    assert "MUST_NOT_SYNC" not in json.dumps(summary)
    assert "RUN_ID_CANARY" not in json.dumps(summary)


def test_desktop_strategy_worker_infrastructure_failure_calls_cloud_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = backtest_api.StrategyBacktestRequest(
        strategy_id="macd_golden",
        start=date(2026, 7, 22),
        end=date(2026, 7, 22),
    )
    raw_config = req.model_dump(mode="json", exclude_unset=True)
    compute_dir = _compute_dir(tmp_path / "verified", "strategy_backtest", raw_config)
    cloud_payload = {
        **_backtest_result(),
        "execution_target": "cloud",
        "summary_sync": {"status": "saved", "revision": "d" * 64},
    }
    remote = _RemoteStub(_http_json(200, cloud_payload))
    adapter = _AdapterStub(compute_dir, remote)
    worker = Mock(side_effect=BacktestWorkerInfrastructureError("worker_start_failed"))
    monkeypatch.setattr(backtest_api, "_run_strategy_worker", worker)

    payload = backtest_api.strategy_run(req, _request_with_state(workspace_adapter=adapter))

    assert payload["execution_target"] == "cloud"
    assert worker.call_count == 1
    assert len(remote.calls) == 1
    assert remote.calls[0][0:2] == ("POST", "/api/backtest/strategy/run")
    assert remote.calls[0][2]["json"] == raw_config
    assert adapter.command_calls == []


@pytest.mark.parametrize(
    "error",
    [
        ComputeInputIntegrityError("coverage mismatch"),
        ComputeInputDataUnavailable("coverage unavailable"),
        PermissionError("capability denied"),
        _http_error(429),
    ],
)
def test_desktop_strategy_prepare_fail_closed_without_cloud(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> None:
    req = backtest_api.StrategyBacktestRequest(
        strategy_id="macd_golden",
        start=date(2026, 7, 22),
        end=date(2026, 7, 22),
    )
    compute_dir = tmp_path / "unused"
    remote = _RemoteStub()
    adapter = _AdapterStub(compute_dir, remote)
    adapter.prepare_error = error
    worker = Mock()
    monkeypatch.setattr(backtest_api, "_run_strategy_worker", worker)

    with pytest.raises(type(error)) as raised:
        backtest_api.strategy_run(req, _request_with_state(workspace_adapter=adapter))

    assert raised.value is error
    worker.assert_not_called()
    assert remote.calls == []


def test_desktop_strategy_business_worker_error_never_calls_cloud(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = backtest_api.StrategyBacktestRequest(
        strategy_id="missing",
        start=date(2026, 7, 22),
        end=date(2026, 7, 22),
    )
    raw_config = req.model_dump(mode="json", exclude_unset=True)
    compute_dir = _compute_dir(tmp_path / "verified", "strategy_backtest", raw_config)
    remote = _RemoteStub()
    adapter = _AdapterStub(compute_dir, remote)
    error = BacktestWorkerError("unknown strategy")
    monkeypatch.setattr(backtest_api, "_run_strategy_worker", Mock(side_effect=error))

    with pytest.raises(BacktestWorkerError) as raised:
        backtest_api.strategy_run(req, _request_with_state(workspace_adapter=adapter))

    assert raised.value is error
    assert remote.calls == []


def test_desktop_strategy_worker_exit_failure_never_calls_cloud(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = backtest_api.StrategyBacktestRequest(
        strategy_id="macd_golden",
        start=date(2026, 7, 22),
        end=date(2026, 7, 22),
    )
    raw_config = req.model_dump(mode="json", exclude_unset=True)
    compute_dir = _compute_dir(tmp_path / "verified", "strategy_backtest", raw_config)
    remote = _RemoteStub()
    adapter = _AdapterStub(compute_dir, remote)
    error = BacktestWorkerError("worker exited without result (exitcode=-9)")
    monkeypatch.setattr(backtest_api, "_run_strategy_worker", Mock(side_effect=error))

    with pytest.raises(BacktestWorkerError) as raised:
        backtest_api.strategy_run(req, _request_with_state(workspace_adapter=adapter))

    assert raised.value is error
    assert remote.calls == []


def test_desktop_strategy_rejects_replaced_manifest_before_worker_or_cloud(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = backtest_api.StrategyBacktestRequest(
        strategy_id="macd_golden",
        start=date(2026, 7, 22),
        end=date(2026, 7, 22),
    )
    different = backtest_api.StrategyBacktestRequest(
        strategy_id="boll_breakout",
        start=date(2026, 7, 22),
        end=date(2026, 7, 22),
    )
    compute_dir = _compute_dir(
        tmp_path / "replaced",
        "strategy_backtest",
        different.model_dump(mode="json", exclude_unset=True),
    )
    adapter = _AdapterStub(compute_dir, _RemoteStub())
    worker = Mock()
    monkeypatch.setattr(backtest_api, "_run_strategy_worker", worker)

    with pytest.raises(ComputeInputIntegrityError, match="contract does not match"):
        backtest_api.strategy_run(req, _request_with_state(workspace_adapter=adapter))

    worker.assert_not_called()
    assert adapter.remote.calls == []


def test_desktop_strategy_rejects_symlinked_snapshot_root_without_cloud(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = backtest_api.StrategyBacktestRequest(
        strategy_id="macd_golden",
        start=date(2026, 7, 22),
        end=date(2026, 7, 22),
    )
    raw_config = req.model_dump(mode="json", exclude_unset=True)
    compute_dir = _compute_dir(tmp_path / "verified", "strategy_backtest", raw_config)
    outside = tmp_path / "outside"
    outside.mkdir()
    (compute_dir / "kline_daily" / "part.parquet").unlink()
    (compute_dir / "kline_daily").rmdir()
    (compute_dir / "kline_daily").symlink_to(outside, target_is_directory=True)
    adapter = _AdapterStub(compute_dir, _RemoteStub())
    worker = Mock()
    monkeypatch.setattr(backtest_api, "_run_strategy_worker", worker)

    with pytest.raises(ComputeInputIntegrityError, match="cannot be pinned"):
        backtest_api.strategy_run(req, _request_with_state(workspace_adapter=adapter))

    worker.assert_not_called()
    assert adapter.remote.calls == []


def test_unsuccessful_backtest_does_not_write_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = backtest_api.StrategyBacktestRequest(
        strategy_id="macd_golden",
        start=date(2026, 7, 22),
        end=date(2026, 7, 22),
    )
    raw_config = req.model_dump(mode="json", exclude_unset=True)
    compute_dir = _compute_dir(tmp_path / "verified", "strategy_backtest", raw_config)
    adapter = _AdapterStub(compute_dir, _RemoteStub())
    failed = {**_backtest_result(), "error": "cancelled"}
    monkeypatch.setattr(backtest_api, "_run_strategy_worker", Mock(return_value=failed))

    payload = backtest_api.strategy_run(req, _request_with_state(workspace_adapter=adapter))

    assert payload["error"] == "cancelled"
    assert "summary_sync" not in payload
    assert adapter.command_calls == []


def test_strategy_cloud_fallback_preserves_429_and_does_not_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = backtest_api.StrategyBacktestRequest(
        strategy_id="macd_golden",
        start=date(2026, 7, 22),
        end=date(2026, 7, 22),
    )
    raw_config = req.model_dump(mode="json", exclude_unset=True)
    compute_dir = _compute_dir(tmp_path / "verified", "strategy_backtest", raw_config)
    adapter = _AdapterStub(
        compute_dir,
        _RemoteStub(_http_json(429, {"detail": "rate limited"})),
    )
    monkeypatch.setattr(
        backtest_api,
        "_run_strategy_worker",
        Mock(side_effect=BacktestWorkerInfrastructureError("worker_start_failed")),
    )

    with pytest.raises(HTTPException) as raised:
        backtest_api.strategy_run(req, _request_with_state(workspace_adapter=adapter))

    assert raised.value.status_code == 429
    assert len(adapter.remote.calls) == 1
    assert adapter.command_calls == []


def test_summary_revision_conflict_refreshes_without_recompute_or_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = backtest_api.StrategyBacktestRequest(
        strategy_id="macd_golden",
        start=date(2026, 7, 22),
        end=date(2026, 7, 22),
    )
    raw_config = req.model_dump(mode="json", exclude_unset=True)
    compute_dir = _compute_dir(tmp_path / "verified", "strategy_backtest", raw_config)
    adapter = _AdapterStub(compute_dir, _RemoteStub())
    adapter.command_error = WorkspaceRevisionConflict(
        ResourceName.BACKTEST_SUMMARIES,
        "b" * 64,
    )
    worker = Mock(return_value=_backtest_result())
    monkeypatch.setattr(backtest_api, "_run_strategy_worker", worker)

    payload = backtest_api.strategy_run(req, _request_with_state(workspace_adapter=adapter))

    sync = payload["summary_sync"]
    assert sync["status"] == "confirmation_required"
    assert sync["current_revision"] == "b" * 64
    assert "revision" not in sync
    assert set(sync["pending_summary"]) == {
        "id",
        "task",
        "strategy_id",
        "parameters_digest",
        "stats",
        "started_at",
        "finished_at",
        "data_as_of",
        "engine",
        "execution_target",
    }
    assert worker.call_count == 1
    assert adapter.get_calls == 2
    assert len(adapter.command_calls) == 1
    assert adapter.remote.calls == []


def test_cloud_strategy_executes_directly_without_router_recursion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = backtest_api.StrategyBacktestRequest(
        strategy_id="macd_golden",
        start=date(2026, 7, 22),
        end=date(2026, 7, 22),
    )
    worker = Mock(return_value=_backtest_result())
    monkeypatch.setattr(backtest_api, "_run_strategy_worker", worker)
    monkeypatch.setattr(backtest_api.settings, "data_dir", tmp_path)

    payload = backtest_api.strategy_run(req, _request_with_state())

    assert payload["execution_target"] == "cloud"
    assert payload["summary_sync"]["status"] == "saved"
    assert worker.call_count == 1
    assert worker.call_args.args[0] == tmp_path
    assert worker.call_args.kwargs["read_only_snapshot"] is False


def test_strategy_stream_worker_infrastructure_failure_emits_cloud_done_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_config = backtest_api.StrategyBacktestRequest(
        strategy_id="macd_golden",
        start=date(2026, 7, 22),
        end=date(2026, 7, 22),
    ).model_dump(mode="json", exclude_unset=True)
    compute_dir = _compute_dir(tmp_path / "verified", "strategy_backtest", raw_config)
    cloud_payload = {
        **_backtest_result(),
        "execution_target": "cloud",
        "summary_sync": {"status": "saved", "revision": "d" * 64},
    }
    adapter = _AdapterStub(compute_dir, _RemoteStub(_http_json(200, cloud_payload)))
    monkeypatch.setattr(
        backtest_api,
        "_run_strategy_worker",
        Mock(side_effect=BacktestWorkerInfrastructureError("worker_start_failed")),
    )
    monkeypatch.setattr(backtest_api, "_JOB_TTL", 0)
    backtest_api._running_jobs.clear()

    class _StreamRequest:
        app = SimpleNamespace(state=SimpleNamespace(workspace_adapter=adapter))

        async def is_disconnected(self) -> bool:
            return False

    async def _collect() -> str:
        response = await backtest_api.strategy_stream(
            _StreamRequest(),
            strategy_id="macd_golden",
            start="2026-07-22",
            end="2026-07-22",
        )
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return "".join(chunks)

    body = asyncio.run(_collect())

    assert "event: done" in body
    assert "event: error" not in body
    assert '"execution_target": "cloud"' in body
    assert len(adapter.remote.calls) == 1
    assert adapter.command_calls == []


def test_strategy_stream_preserves_summary_confirmation_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_config = backtest_api.StrategyBacktestRequest(
        strategy_id="macd_golden",
        start=date(2026, 7, 22),
        end=date(2026, 7, 22),
    ).model_dump(mode="json", exclude_unset=True)
    compute_dir = _compute_dir(tmp_path / "verified", "strategy_backtest", raw_config)
    adapter = _AdapterStub(compute_dir, _RemoteStub())
    adapter.command_error = WorkspaceRevisionConflict(
        ResourceName.BACKTEST_SUMMARIES,
        "b" * 64,
    )
    monkeypatch.setattr(
        backtest_api,
        "_run_strategy_worker",
        Mock(return_value=_backtest_result()),
    )
    monkeypatch.setattr(backtest_api, "_JOB_TTL", 0)
    backtest_api._running_jobs.clear()

    class _StreamRequest:
        app = SimpleNamespace(state=SimpleNamespace(workspace_adapter=adapter))

        async def is_disconnected(self) -> bool:
            return False

    async def _collect() -> str:
        response = await backtest_api.strategy_stream(
            _StreamRequest(),
            strategy_id="macd_golden",
            start="2026-07-22",
            end="2026-07-22",
        )
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return "".join(chunks)

    body = asyncio.run(_collect())
    done_data = next(
        line.removeprefix("data: ")
        for line in body.splitlines()
        if line.startswith("data: ")
    )
    payload = json.loads(done_data)

    assert payload["summary_sync"]["status"] == "confirmation_required"
    assert payload["summary_sync"]["current_revision"] == "b" * 64
    assert payload["summary_sync"]["pending_summary"]["strategy_id"] == "macd_golden"


def test_strategy_stream_worker_exit_failure_emits_error_without_cloud(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_config = backtest_api.StrategyBacktestRequest(
        strategy_id="macd_golden",
        start=date(2026, 7, 22),
        end=date(2026, 7, 22),
    ).model_dump(mode="json", exclude_unset=True)
    compute_dir = _compute_dir(tmp_path / "verified", "strategy_backtest", raw_config)
    adapter = _AdapterStub(compute_dir, _RemoteStub())
    monkeypatch.setattr(
        backtest_api,
        "_run_strategy_worker",
        Mock(side_effect=BacktestWorkerError("worker exited without result (exitcode=-9)")),
    )
    monkeypatch.setattr(backtest_api, "_JOB_TTL", 0)
    backtest_api._running_jobs.clear()

    class _StreamRequest:
        app = SimpleNamespace(state=SimpleNamespace(workspace_adapter=adapter))

        async def is_disconnected(self) -> bool:
            return False

    async def _collect() -> str:
        response = await backtest_api.strategy_stream(
            _StreamRequest(),
            strategy_id="macd_golden",
            start="2026-07-22",
            end="2026-07-22",
        )
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return "".join(chunks)

    body = asyncio.run(_collect())

    assert "event: error" in body
    assert "event: done" not in body
    assert adapter.remote.calls == []


def test_desktop_screener_routes_local_snapshot_before_cloud(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = screener_api.CustomRequest(
        conditions=["close > ma20"],
        as_of=date(2026, 7, 22),
    )
    raw_config = req.model_dump(mode="json", exclude_unset=True)
    compute_dir = _compute_dir(tmp_path / "verified", "screener", raw_config)
    adapter = _AdapterStub(compute_dir, _RemoteStub())
    local = Mock(
        return_value={
            "as_of": "2026-07-22",
            "strategy": None,
            "rows": [],
            "total": 0,
            "elapsed_ms": 1.0,
        }
    )
    monkeypatch.setattr(screener_api, "_run_local_screener", local)

    payload = screener_api.run_custom(req, _request_with_state(workspace_adapter=adapter))

    assert payload["execution_target"] == "local"
    assert adapter.prepare_calls == [("screener", raw_config)]
    leased_dir = local.call_args.args[0]
    assert local.call_args.args[1] == req
    assert leased_dir != compute_dir
    assert not leased_dir.exists()
    assert adapter.remote.calls == []


def test_desktop_screener_repo_failure_falls_back_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = screener_api.CustomRequest(
        conditions=["close > ma20"],
        as_of=date(2026, 7, 22),
    )
    raw_config = req.model_dump(mode="json", exclude_unset=True)
    compute_dir = _compute_dir(tmp_path / "verified", "screener", raw_config)
    cloud_payload = {"as_of": "2026-07-22", "rows": [], "total": 0}
    adapter = _AdapterStub(compute_dir, _RemoteStub(_http_json(200, cloud_payload)))
    monkeypatch.setattr(
        screener_api,
        "_run_local_screener",
        Mock(side_effect=LocalComputeUnavailable("local_repo_init_failed")),
    )

    payload = screener_api.run_custom(req, _request_with_state(workspace_adapter=adapter))

    assert payload["execution_target"] == "cloud"
    assert len(adapter.remote.calls) == 1
    assert adapter.remote.calls[0][0:2] == ("POST", "/api/screener/run")


@pytest.mark.parametrize(
    "error",
    [
        ComputeInputIntegrityError("digest mismatch"),
        ComputeInputDataUnavailable("coverage unavailable"),
        ValueError("bad condition"),
        PermissionError("capability denied"),
    ],
)
def test_desktop_screener_non_infrastructure_failure_never_calls_cloud(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> None:
    req = screener_api.CustomRequest(
        conditions=["close > ma20"],
        as_of=date(2026, 7, 22),
    )
    raw_config = req.model_dump(mode="json", exclude_unset=True)
    compute_dir = _compute_dir(tmp_path / "verified", "screener", raw_config)
    adapter = _AdapterStub(compute_dir, _RemoteStub())
    if isinstance(error, (ComputeInputIntegrityError, ComputeInputDataUnavailable, PermissionError)):
        adapter.prepare_error = error
    else:
        monkeypatch.setattr(screener_api, "_run_local_screener", Mock(side_effect=error))

    with pytest.raises(type(error)) as raised:
        screener_api.run_custom(req, _request_with_state(workspace_adapter=adapter))

    assert raised.value is error
    assert adapter.remote.calls == []


def test_cloud_screener_executes_existing_repo_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = screener_api.CustomRequest(
        conditions=["close > ma20"],
        as_of=date(2026, 7, 22),
    )
    repo = object()
    direct = Mock(return_value={"as_of": "2026-07-22", "rows": [], "total": 0})
    monkeypatch.setattr(screener_api, "_run_custom_with_repo", direct)

    payload = screener_api.run_custom(req, _request_with_state(repo=repo))

    assert payload["execution_target"] == "cloud"
    direct.assert_called_once_with(repo, req)
