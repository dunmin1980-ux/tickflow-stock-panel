from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import stat
import zipfile
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.api import auth as auth_api
from app.api import workspace as workspace_api
from app.config import settings
from app.desktop_client import workspace_adapter as workspace_adapter_module
from app.desktop_client.cache import WorkspaceCache
from app.desktop_client.proxy import route_target
from app.desktop_client.workspace_adapter import (
    CloudWorkspaceAdapter,
    ComputeInputIntegrityError,
)
from app.main import app
from app.services import auth as auth_service
from app.services.compute_input_bundle import (
    ALLOWED_COMPUTE_INPUT_ROOTS,
    ComputeInputBundleService,
    ComputeInputConfigError,
    ComputeInputDataUnavailable,
    ComputeInputFile,
    compute_cache_key,
    compute_parameters_digest,
    normalize_compute_config,
)


def _seed_market_data(root: Path) -> None:
    payloads = {
        "kline_daily/date=2026-07-17/part.parquet": b"daily-17",
        "kline_daily/date=2026-07-20/part.parquet": b"daily-20",
        "kline_daily/date=2026-07-21/part.parquet": b"daily-21",
        "kline_daily_enriched/date=2026-07-17/part.parquet": b"enriched-17",
        "kline_daily_enriched/date=2026-07-20/part.parquet": b"enriched-20",
        "kline_daily_enriched/date=2026-07-21/part.parquet": b"enriched-21",
        "instruments/part.parquet": b"instruments",
        "adj_factor/part.parquet": b"factors",
    }
    for relative, payload in payloads.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def _seed_partition_dates(root: Path, dates: list[date]) -> None:
    for partition in dates:
        for root_name in ("kline_daily", "kline_daily_enriched"):
            path = root / root_name / f"date={partition.isoformat()}" / "part.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"{root_name}-{partition.isoformat()}".encode())


def _seed_static_compute_data(root: Path) -> None:
    for relative, payload in {
        "instruments/part.parquet": b"instruments",
        "adj_factor/part.parquet": b"factors",
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def _calendar_sequence(start: date, end: date, *, step_days: int = 7) -> list[date]:
    values: list[date] = []
    cursor = start
    while cursor < end:
        values.append(cursor)
        cursor += timedelta(days=step_days)
    values.append(end)
    return values


def _weekday_sequence(start: date, end: date) -> list[date]:
    values: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            values.append(cursor)
        cursor += timedelta(days=1)
    return values


def _strategy_config(**overrides) -> dict:
    payload = {
        "strategy_id": "macd_cross",
        "start": "2026-07-17",
        "end": "2026-07-20",
        "asset_type": "stock",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def bundle_service(tmp_path: Path) -> ComputeInputBundleService:
    data_dir = tmp_path / "data"
    _seed_market_data(data_dir)
    service = ComputeInputBundleService(data_dir, temp_root=tmp_path / "bundles")
    yield service
    service.close()


def _manifest(path: Path) -> tuple[dict, list[str]]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        return json.loads(archive.read("manifest.json")), names


def test_strategy_bundle_is_manifest_first_and_whitelisted(
    bundle_service: ComputeInputBundleService,
) -> None:
    artifact = bundle_service.build("strategy_backtest", _strategy_config())
    try:
        manifest, names = _manifest(artifact.path)
        assert names[0] == "manifest.json"
        assert names[1:] == [item["path"] for item in manifest["files"]]
        assert all(
            name == "manifest.json"
            or name.startswith(
                ("kline_daily/", "kline_daily_enriched/", "instruments/", "adj_factor/")
            )
            for name in names
        )
        assert "kline_daily/date=2026-07-21/part.parquet" not in names
        assert "kline_daily_enriched/date=2026-07-21/part.parquet" not in names
        assert manifest["schema_version"] == 1
        assert manifest["task"] == "strategy_backtest"
        assert manifest["data_as_of"] == "2026-07-20"
        assert manifest["coverage_start"] == "2026-07-17"
        assert manifest["coverage_end"] == "2026-07-20"
        assert manifest["partition_dates"] == ["2026-07-17", "2026-07-20"]
        assert manifest["parameters_digest"] == artifact.parameters_digest
        assert manifest["total_uncompressed_bytes"] == sum(
            item["size"] for item in manifest["files"]
        )
        for item in manifest["files"]:
            with zipfile.ZipFile(artifact.path) as archive:
                body = archive.read(item["path"])
            assert len(body) == item["size"]
            assert hashlib.sha256(body).hexdigest() == item["sha256"]
        assert hashlib.sha256(artifact.path.read_bytes()).hexdigest() == artifact.bundle_sha256
    finally:
        artifact.cleanup()


def test_screener_bundle_contains_only_target_enriched_and_instruments(
    bundle_service: ComputeInputBundleService,
) -> None:
    artifact = bundle_service.build(
        "screener",
        {"strategy_id": "macd_cross", "as_of": "2026-07-20", "asset_type": "stock"},
    )
    try:
        _manifest_payload, names = _manifest(artifact.path)
        assert names == [
            "manifest.json",
            "kline_daily_enriched/date=2026-07-20/part.parquet",
            "instruments/part.parquet",
        ]
    finally:
        artifact.cleanup()


@pytest.mark.parametrize(
    ("task", "config"),
    [
        ("unknown", _strategy_config()),
        ("strategy_backtest", _strategy_config(data_path="/tmp/private.parquet")),
        ("strategy_backtest", _strategy_config(params={"model_path": "models/a.pkl"})),
        ("strategy_backtest", _strategy_config(params={"source": "../outside"})),
        ("strategy_backtest", _strategy_config(params={"source": "models/private"})),
        ("strategy_backtest", _strategy_config(params={"source": "private.parquet"})),
        ("strategy_backtest", _strategy_config(params={"source": "model.pkl"})),
        ("strategy_backtest", _strategy_config(params={"source": "weights.onnx"})),
        ("strategy_backtest", _strategy_config(params={"source": "config.yaml"})),
        ("strategy_backtest", _strategy_config(params={"source": "script.sh"})),
        (
            "strategy_backtest",
            _strategy_config(params={"source": "file:///tmp/private.parquet"}),
        ),
        ("strategy_backtest", _strategy_config(params={"source": "/tmp/private"})),
        ("strategy_backtest", _strategy_config(params={"source": "C:foo.parquet"})),
        ("strategy_backtest", _strategy_config(params={"source": "folder/value"})),
        ("strategy_backtest", _strategy_config(params={"source": "folder\\value"})),
        ("strategy_backtest", _strategy_config(params={"source": "模型/权重.onnx"})),
        ("strategy_backtest", _strategy_config(params={"source": "model files/weights.onnx"})),
        ("strategy_backtest", _strategy_config(params={"source": "模型 权重.onnx"})),
        (
            "strategy_backtest",
            _strategy_config(params={"nested": [{"artifact": "weights.onnx"}]}),
        ),
        (
            "strategy_backtest",
            _strategy_config(overrides={"signals": [{"source": "config.yaml"}]}),
        ),
        ("strategy_backtest", _strategy_config(minute_fill=True)),
        ("strategy_backtest", _strategy_config(asset_type="etf")),
        (
            "screener",
            {"strategy_id": "macd_cross", "asset_type": "stock", "ext_columns": "x.y"},
        ),
    ],
)
def test_bundle_rejects_unsupported_or_path_bearing_configs(
    bundle_service: ComputeInputBundleService,
    task: str,
    config: dict,
) -> None:
    with pytest.raises(ComputeInputConfigError):
        bundle_service.build(task, config)


@pytest.mark.parametrize(
    "value",
    [
        "000403.SZ",
        "600489.SH",
        "2026-07-20",
        "open_t+1",
        "macd_cross_v2",
        "stock",
        "1d",
        "close > ma5 and volume > 0",
        "close / ma20 - 1",
        "extensionless_model_token",
        "artifact.reasonably_long_extension_name",
        "model.配置",
    ],
)
def test_config_path_rules_preserve_normal_domain_strings(
    bundle_service: ComputeInputBundleService,
    value: str,
) -> None:
    artifact = bundle_service.build(
        "strategy_backtest",
        _strategy_config(params={"domain_value": value}),
    )
    artifact.cleanup()


def test_client_strings_cannot_flow_into_compute_file_selection(
    bundle_service: ComputeInputBundleService,
    monkeypatch,
) -> None:
    baseline = bundle_service.build("strategy_backtest", _strategy_config())
    root_calls: list[str] = []
    open_calls: list[str] = []
    original_root_files = bundle_service._root_files
    original_open_source_fd = bundle_service._open_source_fd

    def audited_root_files(root_name: str):
        root_calls.append(root_name)
        return original_root_files(root_name)

    def audited_open_source_fd(archive_path: str):
        open_calls.append(archive_path)
        return original_open_source_fd(archive_path)

    monkeypatch.setattr(bundle_service, "_root_files", audited_root_files)
    monkeypatch.setattr(bundle_service, "_open_source_fd", audited_open_source_fd)
    client_values = {
        "formula": "close / ma20 - 1",
        "extensionless": "private_model_token",
        "ambiguous_extension": "artifact.experimental_format",
        "nested_override": "custom_override_token",
    }
    config = _strategy_config(
        params={
            "formula": client_values["formula"],
            "nested": {
                "extensionless": client_values["extensionless"],
                "artifact": client_values["ambiguous_extension"],
            },
        },
        overrides={"signals": [{"token": client_values["nested_override"]}]},
    )
    custom = bundle_service.build("strategy_backtest", config)
    try:
        _baseline_manifest, baseline_names = _manifest(baseline.path)
        custom_manifest, custom_names = _manifest(custom.path)
        assert root_calls == list(ALLOWED_COMPUTE_INPUT_ROOTS)
        assert open_calls
        assert all(
            path.split("/", 1)[0] in ALLOWED_COMPUTE_INPUT_ROOTS for path in open_calls
        )
        assert custom_names[1:] == baseline_names[1:]
        archive_paths = [entry["path"] for entry in custom_manifest["files"]]
        assert all(
            value not in root_calls and value not in open_calls and value not in archive_paths
            for value in client_values.values()
        )
    finally:
        baseline.cleanup()
        custom.cleanup()


def test_omitted_start_and_explicit_null_keep_distinct_semantics_and_cache_keys(
    bundle_service: ComputeInputBundleService,
) -> None:
    _seed_partition_dates(
        bundle_service.data_dir,
        _weekday_sequence(date(2025, 1, 2), date(2026, 7, 20)),
    )

    omitted = {"strategy_id": "macd_cross", "end": "2026-07-20", "asset_type": "stock"}
    explicit_null = {**omitted, "start": None}
    omitted_artifact = bundle_service.build("strategy_backtest", omitted)
    explicit_artifact = bundle_service.build("strategy_backtest", explicit_null)
    explicit_cached = bundle_service.build("strategy_backtest", explicit_null)
    try:
        omitted_manifest, omitted_names = _manifest(omitted_artifact.path)
        explicit_manifest, explicit_names = _manifest(explicit_artifact.path)
        assert omitted_manifest["parameters_digest"] != explicit_manifest["parameters_digest"]
        assert omitted_artifact.bundle_sha256 != explicit_artifact.bundle_sha256
        assert "kline_daily/date=2025-01-02/part.parquet" not in omitted_names
        assert "kline_daily/date=2025-01-02/part.parquet" in explicit_names
        assert omitted_artifact.from_cache is False
        assert explicit_artifact.from_cache is False
        assert explicit_cached.from_cache is True
    finally:
        omitted_artifact.cleanup()
        explicit_artifact.cleanup()
        explicit_cached.cleanup()


def test_client_rejects_omitted_start_for_explicit_null_bundle(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
) -> None:
    omitted = {"strategy_id": "macd_cross", "end": "2026-07-20", "asset_type": "stock"}
    explicit_null = {**omitted, "start": None}
    artifact = bundle_service.build("strategy_backtest", explicit_null)
    try:
        adapter = _adapter(tmp_path, _RemoteStub(b"", {}))
        with pytest.raises(ComputeInputIntegrityError):
            adapter.install_compute_input(
                artifact.path,
                task="strategy_backtest",
                config=omitted,
                data_as_of=artifact.data_as_of,
                bundle_sha256=artifact.bundle_sha256,
            )
    finally:
        artifact.cleanup()


def test_backtest_bundle_fails_closed_when_daily_and_enriched_dates_differ(
    bundle_service: ComputeInputBundleService,
) -> None:
    mismatched = (
        bundle_service.data_dir
        / "kline_daily_enriched"
        / "date=2026-07-17"
        / "part.parquet"
    )
    mismatched.unlink()

    with pytest.raises(ComputeInputDataUnavailable):
        bundle_service.build("strategy_backtest", _strategy_config())


def test_backtest_bundle_fails_when_requested_start_coverage_is_missing(
    bundle_service: ComputeInputBundleService,
) -> None:
    with pytest.raises(ComputeInputDataUnavailable):
        bundle_service.build(
            "strategy_backtest",
            _strategy_config(start="2026-07-01"),
        )


def test_default_window_fails_closed_when_coverage_start_is_truncated(
    bundle_service: ComputeInputBundleService,
) -> None:
    config = {"strategy_id": "macd_cross", "end": "2026-07-20", "asset_type": "stock"}

    with pytest.raises(ComputeInputDataUnavailable):
        bundle_service.build("strategy_backtest", config)


def test_backtest_bundle_fails_closed_on_common_partition_gap(
    bundle_service: ComputeInputBundleService,
) -> None:
    _seed_partition_dates(bundle_service.data_dir, [date(2026, 7, 1)])

    with pytest.raises(ComputeInputDataUnavailable):
        bundle_service.build(
            "strategy_backtest",
            _strategy_config(start="2026-07-01"),
        )


@pytest.mark.parametrize(
    "config",
    [
        {"strategy_id": "macd_cross", "end": "2026-07-20", "asset_type": "stock"},
        _strategy_config(start="2025-12-01", end="2026-07-20"),
        _strategy_config(start=None, end="2026-07-20"),
    ],
    ids=["default-window", "explicit-window", "all-history"],
)
def test_backtest_bundle_rejects_weekly_sparse_partition_density(
    tmp_path: Path,
    config: dict,
) -> None:
    data_dir = tmp_path / "weekly-sparse-data"
    _seed_static_compute_data(data_dir)
    _seed_partition_dates(
        data_dir,
        _calendar_sequence(date(2025, 12, 1), date(2026, 7, 20)),
    )
    service = ComputeInputBundleService(data_dir, temp_root=tmp_path / "weekly-sparse-cache")
    try:
        with pytest.raises(ComputeInputDataUnavailable, match="weekday density"):
            service.build("strategy_backtest", config)
    finally:
        service.close()


@pytest.mark.parametrize(
    "config",
    [
        {"strategy_id": "macd_cross", "end": "2026-07-20", "asset_type": "stock"},
        _strategy_config(start="2025-12-01", end="2026-07-20"),
        _strategy_config(start=None, end="2026-07-20"),
    ],
    ids=["default-window", "explicit-window", "all-history"],
)
def test_backtest_bundle_accepts_dense_weekdays_with_small_holiday_gaps(
    tmp_path: Path,
    config: dict,
) -> None:
    data_dir = tmp_path / "dense-data"
    _seed_static_compute_data(data_dir)
    end = date(2026, 7, 20)
    weekdays = _weekday_sequence(date(2025, 12, 1), end)
    partitions = [
        value
        for index, value in enumerate(weekdays)
        if value in {weekdays[0], end} or index % 11 != 5
    ]
    _seed_partition_dates(data_dir, partitions)
    service = ComputeInputBundleService(data_dir, temp_root=tmp_path / "dense-cache")
    artifact = service.build("strategy_backtest", config)
    try:
        manifest, _names = _manifest(artifact.path)
        assert manifest["coverage"]["minimum_weekday_density_percent"] == 80
    finally:
        artifact.cleanup()
        service.close()


def test_backtest_bundle_accepts_short_window_at_weekday_density_threshold(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "short-window-data"
    _seed_static_compute_data(data_dir)
    _seed_partition_dates(
        data_dir,
        [date(2026, 7, 13), date(2026, 7, 14), date(2026, 7, 16), date(2026, 7, 17)],
    )
    service = ComputeInputBundleService(data_dir, temp_root=tmp_path / "short-window-cache")
    artifact = service.build(
        "strategy_backtest",
        _strategy_config(start="2026-07-13", end="2026-07-17"),
    )
    try:
        manifest, _names = _manifest(artifact.path)
        assert manifest["partition_dates"] == [
            "2026-07-13",
            "2026-07-14",
            "2026-07-16",
            "2026-07-17",
        ]
    finally:
        artifact.cleanup()
        service.close()


def test_default_window_records_deterministic_coverage_policy(tmp_path: Path) -> None:
    data_dir = tmp_path / "coverage-data"
    _seed_static_compute_data(data_dir)
    effective_start = date(2026, 7, 20) - timedelta(days=180)
    first_partition = effective_start + timedelta(days=5)
    weekdays = _weekday_sequence(first_partition, date(2026, 7, 20))
    _seed_partition_dates(
        data_dir,
        [
            value
            for index, value in enumerate(weekdays)
            if value in {weekdays[0], weekdays[-1]} or index % 13 != 7
        ],
    )
    service = ComputeInputBundleService(data_dir, temp_root=tmp_path / "coverage-bundles")
    artifact = service.build(
        "strategy_backtest",
        {"strategy_id": "macd_cross", "end": "2026-07-20", "asset_type": "stock"},
    )
    try:
        manifest, _names = _manifest(artifact.path)
        assert manifest["coverage"]["calendar_basis"] == (
            "calendar_day_heuristic_without_exchange_calendar"
        )
        assert manifest["coverage"]["caveat"] == (
            "weekends_and_exchange_holidays_are_not_authoritative"
        )
        assert manifest["coverage"]["effective_start"] == effective_start.isoformat()
        assert manifest["coverage"]["start_requirement"] == "within_tolerance"
        assert manifest["coverage"]["start_tolerance_days"] == 7
        assert manifest["coverage"]["max_partition_gap_days"] == 7
        assert manifest["coverage"]["minimum_weekday_density_percent"] == 80
        assert manifest["coverage_start"] == first_partition.isoformat()
    finally:
        artifact.cleanup()
        service.close()


def test_bundle_rejects_symlinked_source_file(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.parquet"
    outside.write_bytes(b"outside")
    link = bundle_service.data_dir / "instruments" / "linked.parquet"
    link.symlink_to(outside)

    with pytest.raises(ComputeInputConfigError):
        bundle_service.build("strategy_backtest", _strategy_config())


def test_source_parent_replacement_is_rejected_before_snapshot_copy(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
) -> None:
    normalized = normalize_compute_config("strategy_backtest", _strategy_config())
    sources, _data_as_of, _coverage = bundle_service._select_sources(
        "strategy_backtest",
        normalized,
    )
    source = next(item for item in sources if item.archive_path == "instruments/part.parquet")
    original_root = bundle_service.data_dir / "instruments"
    retained_root = bundle_service.data_dir / "instruments-original"
    original_root.rename(retained_root)
    replacement = tmp_path / "replacement-instruments"
    replacement.mkdir()
    (replacement / "part.parquet").write_bytes(b"replacement")
    original_root.symlink_to(replacement, target_is_directory=True)

    staging = tmp_path / "source-staging"
    staging.mkdir()
    with pytest.raises(ComputeInputConfigError):
        bundle_service._snapshot_source(source, staging)


def test_bundle_reuses_master_for_ten_minutes_but_leases_unique_files(
    bundle_service: ComputeInputBundleService,
) -> None:
    first = bundle_service.build("strategy_backtest", _strategy_config())
    second = bundle_service.build("strategy_backtest", _strategy_config())
    try:
        assert first.from_cache is False
        assert second.from_cache is True
        assert first.path != second.path
        assert first.bundle_sha256 == second.bundle_sha256
    finally:
        first.cleanup()
        second.cleanup()
    assert not first.path.exists()
    assert not second.path.exists()


def test_master_cache_enforces_predictable_entry_capacity(tmp_path: Path) -> None:
    data_dir = tmp_path / "capacity-data"
    _seed_market_data(data_dir)
    service = ComputeInputBundleService(
        data_dir,
        temp_root=tmp_path / "capacity-cache",
        cache_max_entries=2,
        cache_max_bytes=64 * 1024 * 1024,
    )
    artifacts = [
        service.build(
            "strategy_backtest",
            _strategy_config(strategy_id=f"strategy-{index}"),
        )
        for index in range(3)
    ]
    for artifact in artifacts:
        artifact.cleanup()
    assert len(list(service.temp_root.glob("master-*.zip"))) == 2

    first_again = service.build(
        "strategy_backtest",
        _strategy_config(strategy_id="strategy-0"),
    )
    try:
        assert first_again.from_cache is False
        assert len(list(service.temp_root.glob("master-*.zip"))) == 2
    finally:
        first_again.cleanup()
        service.close()


def test_master_larger_than_cache_byte_budget_is_served_uncached(tmp_path: Path) -> None:
    data_dir = tmp_path / "byte-data"
    _seed_market_data(data_dir)
    service = ComputeInputBundleService(
        data_dir,
        temp_root=tmp_path / "byte-cache",
        cache_max_entries=2,
        cache_max_bytes=1,
    )
    artifact = service.build("strategy_backtest", _strategy_config())
    try:
        assert artifact.path.exists()
        assert not list(service.temp_root.glob("master-*.zip"))
    finally:
        artifact.cleanup()
        service.close()


def test_expired_master_is_removed_before_rebuild(tmp_path: Path) -> None:
    data_dir = tmp_path / "ttl-data"
    _seed_market_data(data_dir)
    clock = [0.0]
    service = ComputeInputBundleService(
        data_dir,
        temp_root=tmp_path / "ttl-cache",
        cache_ttl_seconds=10,
        monotonic=lambda: clock[0],
    )
    first = service.build("strategy_backtest", _strategy_config())
    first.cleanup()
    old_master = next(service.temp_root.glob("master-*.zip"))
    old_inode = old_master.stat().st_ino

    clock[0] = 11.0
    second = service.build("strategy_backtest", _strategy_config())
    try:
        assert second.from_cache is False
        assert len(list(service.temp_root.glob("master-*.zip"))) == 1
        assert old_master.stat().st_ino != old_inode
    finally:
        second.cleanup()
        service.close()


class _RemoteStub:
    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        self.body = body
        self.headers = headers
        self.calls: list[tuple[str, str, dict]] = []

    @contextmanager
    def stream(self, method: str, path: str, **kwargs):
        self.calls.append((method, path, kwargs))
        yield httpx.Response(
            200,
            content=self.body,
            headers=self.headers,
            request=httpx.Request(method, f"https://cloud.example{path}"),
        )


def _adapter(
    tmp_path: Path,
    remote,
    *,
    max_bytes: int = 2 * 1024**3,
) -> CloudWorkspaceAdapter:
    return CloudWorkspaceAdapter(
        remote,
        WorkspaceCache(tmp_path / "workspace-cache"),
        compute_root=tmp_path / "Application Support" / "TickFlowStockPanel" / "compute_inputs",
        compute_today=lambda: date(2026, 7, 21),
        compute_max_uncompressed_bytes=max_bytes,
    )


def _valid_bundle(
    bundle_service: ComputeInputBundleService,
) -> tuple[object, dict[str, str]]:
    artifact = bundle_service.build("strategy_backtest", _strategy_config())
    headers = {
        "X-Data-As-Of": artifact.data_as_of,
        "X-Bundle-SHA256": artifact.bundle_sha256,
    }
    return artifact, headers


def test_client_hashes_and_reads_zip_from_the_same_open_file_description(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact, headers = _valid_bundle(bundle_service)
    hash_fds: list[int] = []
    zip_fds: list[int] = []
    real_zip_file = zipfile.ZipFile

    def hash_open_file(handle) -> str:
        hash_fds.append(handle.fileno())
        digest = hashlib.sha256()
        handle.seek(0)
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        handle.seek(0)
        return digest.hexdigest()

    def audited_zip_file(file, *args, **kwargs):
        zip_fds.append(file.fileno() if hasattr(file, "fileno") else -1)
        return real_zip_file(file, *args, **kwargs)

    monkeypatch.setattr(
        CloudWorkspaceAdapter,
        "_sha256_open_file",
        staticmethod(hash_open_file),
        raising=False,
    )
    monkeypatch.setattr(workspace_adapter_module.zipfile, "ZipFile", audited_zip_file)
    try:
        adapter = _adapter(tmp_path, _RemoteStub(b"", {}))
        installed = adapter.install_compute_input(
            artifact.path,
            task="strategy_backtest",
            config=_strategy_config(),
            data_as_of=headers["X-Data-As-Of"],
            bundle_sha256=headers["X-Bundle-SHA256"],
        )
        assert installed.exists()
        assert len(hash_fds) == 1
        assert hash_fds == zip_fds
    finally:
        artifact.cleanup()


def test_prepare_compute_input_downloads_validates_and_publishes_read_only(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
) -> None:
    artifact, headers = _valid_bundle(bundle_service)
    try:
        remote = _RemoteStub(artifact.path.read_bytes(), headers)
        adapter = _adapter(tmp_path, remote)
        installed = adapter.prepare_compute_input("strategy_backtest", _strategy_config())

        assert installed.parent == adapter.compute_root
        assert len(installed.name) == 64
        assert (installed / "manifest.json").exists()
        assert stat.S_IMODE(installed.stat().st_mode) == 0o555
        assert all(
            stat.S_IMODE(path.stat().st_mode) == (0o555 if path.is_dir() else 0o444)
            for path in installed.rglob("*")
        )
        assert remote.calls == [
            (
                "POST",
                "/api/workspace/compute-inputs/build",
                {"json": {"task": "strategy_backtest", "config": _strategy_config()}},
            )
        ]
        assert not list(adapter.compute_root.glob(".staging-*"))
        assert not list(adapter.compute_root.parent.glob("compute-input-*.zip"))
    finally:
        artifact.cleanup()


def test_prepare_compute_input_keeps_mkstemp_fd_through_fsync_and_install(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact, headers = _valid_bundle(bundle_service)
    remote = _RemoteStub(artifact.path.read_bytes(), headers)
    adapter = _adapter(tmp_path, remote)
    real_mkstemp = workspace_adapter_module.tempfile.mkstemp
    real_fsync = workspace_adapter_module.os.fsync
    created_fds: list[int] = []
    fsync_fds: list[int] = []
    install_fds: list[int] = []
    installed = tmp_path / "installed-from-stable-fd"

    def audited_mkstemp(*args, **kwargs):
        fd, name = real_mkstemp(*args, **kwargs)
        created_fds.append(fd)
        return fd, name

    def audited_fsync(fd: int) -> None:
        fsync_fds.append(fd)
        real_fsync(fd)

    def install_from_handle(_self, handle, **_kwargs):
        install_fds.append(handle.fileno())
        assert handle.tell() == 0
        return installed

    monkeypatch.setattr(workspace_adapter_module.tempfile, "mkstemp", audited_mkstemp)
    monkeypatch.setattr(workspace_adapter_module.os, "fsync", audited_fsync)
    monkeypatch.setattr(
        CloudWorkspaceAdapter,
        "_install_compute_input_from_handle",
        install_from_handle,
        raising=False,
    )
    try:
        result = adapter.prepare_compute_input("strategy_backtest", _strategy_config())
        assert result == installed
        assert len(created_fds) == 1
        assert created_fds == fsync_fds == install_fds
        with pytest.raises(OSError):
            os.fstat(created_fds[0])
        assert not list(adapter.compute_root.parent.glob("compute-input-*.zip"))
    finally:
        artifact.cleanup()


@pytest.mark.parametrize("replacement_kind", ["symlink", "regular"])
def test_prepare_compute_input_fails_closed_when_temp_path_is_replaced(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
    monkeypatch,
    replacement_kind: str,
) -> None:
    artifact, headers = _valid_bundle(bundle_service)
    adapter = _adapter(tmp_path, _RemoteStub(artifact.path.read_bytes(), headers))
    real_fsync = workspace_adapter_module.os.fsync
    attacker = tmp_path / "attacker-owned.zip"
    attacker.write_bytes(b"attacker-canary")
    replacement: Path | None = None

    def replace_after_fsync(fd: int) -> None:
        nonlocal replacement
        real_fsync(fd)
        replacement = next(adapter.compute_root.parent.glob("compute-input-*.zip"))
        replacement.unlink()
        if replacement_kind == "symlink":
            replacement.symlink_to(attacker)
        else:
            replacement.write_bytes(b"replacement-canary")

    monkeypatch.setattr(workspace_adapter_module.os, "fsync", replace_after_fsync)
    try:
        with pytest.raises(ComputeInputIntegrityError):
            adapter.prepare_compute_input("strategy_backtest", _strategy_config())
        assert replacement is not None
        if replacement_kind == "symlink":
            assert replacement.is_symlink()
            assert attacker.read_bytes() == b"attacker-canary"
        else:
            assert replacement.read_bytes() == b"replacement-canary"
    finally:
        if replacement is not None:
            replacement.unlink(missing_ok=True)
        artifact.cleanup()


def test_prepare_compute_input_cleans_fd_and_file_when_fsync_fails(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact, headers = _valid_bundle(bundle_service)
    adapter = _adapter(tmp_path, _RemoteStub(artifact.path.read_bytes(), headers))
    real_mkstemp = workspace_adapter_module.tempfile.mkstemp
    created: list[tuple[int, Path]] = []

    def audited_mkstemp(*args, **kwargs):
        fd, name = real_mkstemp(*args, **kwargs)
        created.append((fd, Path(name)))
        return fd, name

    def fail_fsync(_fd: int) -> None:
        raise OSError("injected fsync failure")

    monkeypatch.setattr(workspace_adapter_module.tempfile, "mkstemp", audited_mkstemp)
    monkeypatch.setattr(workspace_adapter_module.os, "fsync", fail_fsync)
    try:
        with pytest.raises(ComputeInputIntegrityError):
            adapter.prepare_compute_input("strategy_backtest", _strategy_config())
        assert len(created) == 1
        fd, path = created[0]
        with pytest.raises(OSError):
            os.fstat(fd)
        assert not path.exists()
    finally:
        artifact.cleanup()


def _rewrite_zip(source: Path, target: Path, mutate) -> None:
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(target, "w") as output:
        for info in archive.infolist():
            payload = archive.read(info.filename)
            replacement = mutate(info, payload)
            if replacement is None:
                continue
            next_info, next_payload = replacement
            output.writestr(next_info, next_payload)


@pytest.mark.parametrize("member", ["../escaped", "/absolute", "C:/absolute", "safe\\bad"])
def test_client_rejects_unsafe_zip_paths(tmp_path: Path, member: str) -> None:
    malicious = tmp_path / "malicious.zip"
    with zipfile.ZipFile(malicious, "w") as archive:
        archive.writestr(member, "bad")
    adapter = _adapter(tmp_path, _RemoteStub(b"", {}))

    with pytest.raises(ComputeInputIntegrityError):
        adapter.install_compute_input(malicious)
    assert not (adapter.compute_root.parent / "escaped").exists()


def test_client_rejects_zip_symlink(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
) -> None:
    artifact, headers = _valid_bundle(bundle_service)
    malicious = tmp_path / "symlink.zip"

    def mutate(info: zipfile.ZipInfo, payload: bytes):
        if info.filename == "instruments/part.parquet":
            link = zipfile.ZipInfo(info.filename)
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            return link, b"../../outside"
        return info, payload

    try:
        _rewrite_zip(artifact.path, malicious, mutate)
        headers["X-Bundle-SHA256"] = hashlib.sha256(malicious.read_bytes()).hexdigest()
        adapter = _adapter(tmp_path, _RemoteStub(b"", {}))
        with pytest.raises(ComputeInputIntegrityError):
            adapter.install_compute_input(
                malicious,
                task="strategy_backtest",
                config=_strategy_config(),
                data_as_of=headers["X-Data-As-Of"],
                bundle_sha256=headers["X-Bundle-SHA256"],
            )
    finally:
        artifact.cleanup()


@pytest.mark.parametrize("failure", ["unlisted", "bad_size", "bad_hash", "bad_task"])
def test_client_rejects_manifest_contract_failures(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
    failure: str,
) -> None:
    artifact, headers = _valid_bundle(bundle_service)
    malicious = tmp_path / f"{failure}.zip"

    def mutate(info: zipfile.ZipInfo, payload: bytes):
        if info.filename != "manifest.json":
            return info, payload
        manifest = json.loads(payload)
        if failure == "unlisted":
            return info, payload
        if failure == "bad_size":
            manifest["files"][0]["size"] += 1
        elif failure == "bad_hash":
            manifest["files"][0]["sha256"] = "0" * 64
        else:
            manifest["task"] = "screener"
        return info, json.dumps(manifest, sort_keys=True).encode()

    try:
        _rewrite_zip(artifact.path, malicious, mutate)
        if failure == "unlisted":
            with zipfile.ZipFile(malicious, "a") as archive:
                archive.writestr("kline_daily/unlisted.parquet", b"not-declared")
        headers["X-Bundle-SHA256"] = hashlib.sha256(malicious.read_bytes()).hexdigest()
        adapter = _adapter(tmp_path, _RemoteStub(b"", {}))
        with pytest.raises(ComputeInputIntegrityError):
            adapter.install_compute_input(
                malicious,
                task="strategy_backtest",
                config=_strategy_config(),
                data_as_of=headers["X-Data-As-Of"],
                bundle_sha256=headers["X-Bundle-SHA256"],
            )
        assert not list(adapter.compute_root.glob(".staging-*"))
    finally:
        artifact.cleanup()


@pytest.mark.parametrize(
    "invalid_path",
    ["", "/absolute", "../escaped", "safe\\bad", "nul\x00name", "a//b", "./a"],
)
def test_client_classifies_malformed_manifest_file_paths_as_integrity_errors(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
    invalid_path: str,
) -> None:
    artifact, headers = _valid_bundle(bundle_service)
    malicious = tmp_path / "invalid-manifest-path.zip"

    def mutate(info: zipfile.ZipInfo, payload: bytes):
        if info.filename != "manifest.json":
            return info, payload
        manifest = json.loads(payload)
        manifest["files"][0]["path"] = invalid_path
        return info, json.dumps(manifest, sort_keys=True).encode()

    try:
        _rewrite_zip(artifact.path, malicious, mutate)
        bundle_hash = hashlib.sha256(malicious.read_bytes()).hexdigest()
        adapter = _adapter(tmp_path, _RemoteStub(b"", {}))
        with pytest.raises(ComputeInputIntegrityError):
            adapter.install_compute_input(
                malicious,
                task="strategy_backtest",
                config=_strategy_config(),
                data_as_of=headers["X-Data-As-Of"],
                bundle_sha256=bundle_hash,
            )
    finally:
        artifact.cleanup()


@pytest.mark.parametrize(
    "invalid_path",
    ["", "/absolute", "../escaped", "safe\\bad", "nul\x00name", "a//b", "./a"],
)
def test_manifest_file_model_rejects_noncanonical_paths(invalid_path: str) -> None:
    with pytest.raises(ValueError):
        ComputeInputFile(path=invalid_path, size=0, sha256="0" * 64)


@pytest.mark.parametrize("field", ["coverage_start", "coverage_end", "partition_dates"])
def test_client_rejects_manifest_coverage_tampering(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
    field: str,
) -> None:
    artifact, headers = _valid_bundle(bundle_service)
    malicious = tmp_path / f"bad-{field}.zip"

    def mutate(info: zipfile.ZipInfo, payload: bytes):
        if info.filename != "manifest.json":
            return info, payload
        manifest = json.loads(payload)
        manifest[field] = ["2026-07-20"] if field == "partition_dates" else "2026-07-18"
        return info, json.dumps(manifest, sort_keys=True).encode()

    try:
        _rewrite_zip(artifact.path, malicious, mutate)
        bundle_hash = hashlib.sha256(malicious.read_bytes()).hexdigest()
        adapter = _adapter(tmp_path, _RemoteStub(b"", {}))
        with pytest.raises(ComputeInputIntegrityError):
            adapter.install_compute_input(
                malicious,
                task="strategy_backtest",
                config=_strategy_config(),
                data_as_of=headers["X-Data-As-Of"],
                bundle_sha256=bundle_hash,
            )
    finally:
        artifact.cleanup()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("calendar_basis", "exchange_calendar"),
        ("caveat", "none"),
        ("start_tolerance_days", 30),
        ("max_partition_gap_days", 30),
        ("minimum_weekday_density_percent", 10),
    ],
)
def test_client_rejects_manifest_coverage_policy_tampering(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
    field: str,
    value: str | int,
) -> None:
    artifact, headers = _valid_bundle(bundle_service)
    malicious = tmp_path / f"bad-policy-{field}.zip"

    def mutate(info: zipfile.ZipInfo, payload: bytes):
        if info.filename != "manifest.json":
            return info, payload
        manifest = json.loads(payload)
        manifest["coverage"][field] = value
        return info, json.dumps(manifest, sort_keys=True).encode()

    try:
        _rewrite_zip(artifact.path, malicious, mutate)
        bundle_hash = hashlib.sha256(malicious.read_bytes()).hexdigest()
        adapter = _adapter(tmp_path, _RemoteStub(b"", {}))
        with pytest.raises(ComputeInputIntegrityError):
            adapter.install_compute_input(
                malicious,
                task="strategy_backtest",
                config=_strategy_config(),
                data_as_of=headers["X-Data-As-Of"],
                bundle_sha256=bundle_hash,
            )
    finally:
        artifact.cleanup()


def test_client_rejects_archive_partition_gap_even_when_file_list_is_rewritten(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
) -> None:
    artifact, headers = _valid_bundle(bundle_service)
    malicious = tmp_path / "missing-enriched-partition.zip"
    missing_path = "kline_daily_enriched/date=2026-07-17/part.parquet"

    def mutate(info: zipfile.ZipInfo, payload: bytes):
        if info.filename == missing_path:
            return None
        if info.filename != "manifest.json":
            return info, payload
        manifest = json.loads(payload)
        manifest["files"] = [
            item for item in manifest["files"] if item["path"] != missing_path
        ]
        manifest["total_uncompressed_bytes"] = sum(
            item["size"] for item in manifest["files"]
        )
        return info, json.dumps(manifest, sort_keys=True).encode()

    try:
        _rewrite_zip(artifact.path, malicious, mutate)
        bundle_hash = hashlib.sha256(malicious.read_bytes()).hexdigest()
        adapter = _adapter(tmp_path, _RemoteStub(b"", {}))
        with pytest.raises(ComputeInputIntegrityError):
            adapter.install_compute_input(
                malicious,
                task="strategy_backtest",
                config=_strategy_config(),
                data_as_of=headers["X-Data-As-Of"],
                bundle_sha256=bundle_hash,
            )
    finally:
        artifact.cleanup()


def test_client_rejects_common_partition_gap_with_self_consistent_manifest(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
) -> None:
    artifact, _headers = _valid_bundle(bundle_service)
    malicious = tmp_path / "common-partition-gap.zip"
    old_date = "2026-07-20"
    new_date = "2026-07-30"
    config = _strategy_config(end=new_date)

    def mutate(info: zipfile.ZipInfo, payload: bytes):
        if info.filename == "manifest.json":
            manifest = json.loads(payload)
            manifest["data_as_of"] = new_date
            manifest["coverage_end"] = new_date
            manifest["partition_dates"] = ["2026-07-17", new_date]
            manifest["parameters_digest"] = compute_parameters_digest(
                normalize_compute_config("strategy_backtest", config)
            )
            for entry in manifest["files"]:
                entry["path"] = entry["path"].replace(
                    f"date={old_date}", f"date={new_date}"
                )
            return info, json.dumps(manifest, sort_keys=True).encode()
        info.filename = info.filename.replace(f"date={old_date}", f"date={new_date}")
        return info, payload

    try:
        _rewrite_zip(artifact.path, malicious, mutate)
        bundle_hash = hashlib.sha256(malicious.read_bytes()).hexdigest()
        adapter = _adapter(tmp_path, _RemoteStub(b"", {}))
        with pytest.raises(ComputeInputIntegrityError):
            adapter.install_compute_input(
                malicious,
                task="strategy_backtest",
                config=config,
                data_as_of=new_date,
                bundle_sha256=bundle_hash,
            )
    finally:
        artifact.cleanup()


def test_client_rejects_weekly_sparse_density_with_self_consistent_manifest(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "client-density-data"
    _seed_static_compute_data(data_dir)
    weekdays = _weekday_sequence(date(2026, 6, 1), date(2026, 6, 30))
    _seed_partition_dates(data_dir, weekdays)
    service = ComputeInputBundleService(
        data_dir,
        temp_root=tmp_path / "client-density-cache",
    )
    config = _strategy_config(start="2026-06-01", end="2026-06-30")
    artifact = service.build("strategy_backtest", config)
    malicious = tmp_path / "client-weekly-sparse.zip"
    retained_dates = {value.isoformat() for value in weekdays[::5]}

    def retained(path: str) -> bool:
        if not path.startswith(("kline_daily/", "kline_daily_enriched/")):
            return True
        return any(f"date={value}" in path for value in retained_dates)

    def mutate(info: zipfile.ZipInfo, payload: bytes):
        if info.filename == "manifest.json":
            manifest = json.loads(payload)
            manifest["data_as_of"] = max(retained_dates)
            manifest["coverage_end"] = max(retained_dates)
            manifest["partition_dates"] = sorted(retained_dates)
            manifest["files"] = [
                entry for entry in manifest["files"] if retained(entry["path"])
            ]
            manifest["total_uncompressed_bytes"] = sum(
                entry["size"] for entry in manifest["files"]
            )
            return info, json.dumps(manifest, sort_keys=True).encode()
        return (info, payload) if retained(info.filename) else None

    try:
        _rewrite_zip(artifact.path, malicious, mutate)
        adapter = _adapter(tmp_path, _RemoteStub(b"", {}))
        with pytest.raises(ComputeInputIntegrityError, match="weekday density"):
            adapter.install_compute_input(
                malicious,
                task="strategy_backtest",
                config=config,
                data_as_of=max(retained_dates),
                bundle_sha256=hashlib.sha256(malicious.read_bytes()).hexdigest(),
            )
    finally:
        artifact.cleanup()
        service.close()


def test_client_rejects_oversized_uncompressed_bundle(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
) -> None:
    artifact, headers = _valid_bundle(bundle_service)
    try:
        adapter = _adapter(tmp_path, _RemoteStub(b"", {}), max_bytes=4)
        with pytest.raises(ComputeInputIntegrityError):
            adapter.install_compute_input(
                artifact.path,
                task="strategy_backtest",
                config=_strategy_config(),
                data_as_of=headers["X-Data-As-Of"],
                bundle_sha256=headers["X-Bundle-SHA256"],
            )
    finally:
        artifact.cleanup()


def test_client_rejects_non_whitelisted_archive_root(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
) -> None:
    artifact, headers = _valid_bundle(bundle_service)
    malicious = tmp_path / "wrong-root.zip"
    try:
        with zipfile.ZipFile(artifact.path) as source:
            manifest = json.loads(source.read("manifest.json"))
            old_name = manifest["files"][0]["path"]
            manifest["files"][0]["path"] = "secrets/part.parquet"
            with zipfile.ZipFile(malicious, "w") as output:
                output.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
                for info in source.infolist()[1:]:
                    name = "secrets/part.parquet" if info.filename == old_name else info.filename
                    output.writestr(name, source.read(info.filename))
        headers["X-Bundle-SHA256"] = hashlib.sha256(malicious.read_bytes()).hexdigest()
        adapter = _adapter(tmp_path, _RemoteStub(b"", {}))
        with pytest.raises(ComputeInputIntegrityError):
            adapter.install_compute_input(
                malicious,
                task="strategy_backtest",
                config=_strategy_config(),
                data_as_of=headers["X-Data-As-Of"],
                bundle_sha256=headers["X-Bundle-SHA256"],
            )
    finally:
        artifact.cleanup()


def test_client_rejects_parameter_digest_mismatch(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
) -> None:
    artifact, headers = _valid_bundle(bundle_service)
    try:
        adapter = _adapter(tmp_path, _RemoteStub(b"", {}))
        with pytest.raises(ComputeInputIntegrityError):
            adapter.install_compute_input(
                artifact.path,
                task="strategy_backtest",
                config=_strategy_config(strategy_id="bollinger_rebound"),
                data_as_of=headers["X-Data-As-Of"],
                bundle_sha256=headers["X-Bundle-SHA256"],
            )
    finally:
        artifact.cleanup()


def test_client_rejects_preplanted_cache_target_symlink_without_touching_destination(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
) -> None:
    artifact, headers = _valid_bundle(bundle_service)
    try:
        adapter = _adapter(tmp_path, _RemoteStub(b"", {}))
        adapter.compute_root.mkdir(parents=True)
        normalized = normalize_compute_config("strategy_backtest", _strategy_config())
        cache_key = compute_cache_key("strategy_backtest", normalized, artifact.data_as_of)
        outside = tmp_path / "outside-cache"
        outside.mkdir()
        canary = outside / "keep.txt"
        canary.write_text("keep", encoding="utf-8")
        (adapter.compute_root / cache_key).symlink_to(outside, target_is_directory=True)

        with pytest.raises(ComputeInputIntegrityError):
            adapter.install_compute_input(
                artifact.path,
                task="strategy_backtest",
                config=_strategy_config(),
                data_as_of=headers["X-Data-As-Of"],
                bundle_sha256=headers["X-Bundle-SHA256"],
            )
        assert canary.read_text(encoding="utf-8") == "keep"
    finally:
        artifact.cleanup()


def test_atomic_publish_restores_old_cache_when_final_permissions_fail(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
    monkeypatch,
) -> None:
    first, first_headers = _valid_bundle(bundle_service)
    adapter = _adapter(tmp_path, _RemoteStub(b"", {}))
    installed = adapter.install_compute_input(
        first.path,
        task="strategy_backtest",
        config=_strategy_config(),
        data_as_of=first_headers["X-Data-As-Of"],
        bundle_sha256=first_headers["X-Bundle-SHA256"],
    )
    old_bytes = (installed / "instruments" / "part.parquet").read_bytes()

    replacement_data = tmp_path / "replacement-data"
    _seed_market_data(replacement_data)
    (replacement_data / "instruments" / "part.parquet").write_bytes(b"new-instruments")
    replacement_service = ComputeInputBundleService(
        replacement_data,
        temp_root=tmp_path / "replacement-bundles",
    )
    second = replacement_service.build("strategy_backtest", _strategy_config())
    real_chmod = workspace_adapter_module.os.chmod
    injected = False

    def fail_final_target_chmod(path, mode):
        nonlocal injected
        if Path(path) == installed and mode == 0o555 and not injected:
            injected = True
            raise OSError("injected final permission failure")
        return real_chmod(path, mode)

    monkeypatch.setattr(workspace_adapter_module.os, "chmod", fail_final_target_chmod)
    try:
        with pytest.raises(ComputeInputIntegrityError):
            adapter.install_compute_input(
                second.path,
                task="strategy_backtest",
                config=_strategy_config(),
                data_as_of=second.data_as_of,
                bundle_sha256=second.bundle_sha256,
            )
        assert injected is True
        assert installed.exists()
        assert (installed / "instruments" / "part.parquet").read_bytes() == old_bytes
        assert not list(adapter.compute_root.glob(".*.retired-*"))
    finally:
        first.cleanup()
        second.cleanup()
        replacement_service.close()


def test_atomic_publish_restores_old_cache_when_replace_fails(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact, headers = _valid_bundle(bundle_service)
    adapter = _adapter(tmp_path, _RemoteStub(b"", {}))
    installed = adapter.install_compute_input(
        artifact.path,
        task="strategy_backtest",
        config=_strategy_config(),
        data_as_of=headers["X-Data-As-Of"],
        bundle_sha256=headers["X-Bundle-SHA256"],
    )
    old_manifest = (installed / "manifest.json").read_bytes()
    real_replace = workspace_adapter_module.os.replace
    injected = False

    def fail_new_publish(source, destination):
        nonlocal injected
        source_path = Path(source)
        if source_path.name.startswith(".staging-") and Path(destination) == installed:
            injected = True
            raise OSError("injected replace failure")
        return real_replace(source, destination)

    monkeypatch.setattr(workspace_adapter_module.os, "replace", fail_new_publish)
    try:
        with pytest.raises(ComputeInputIntegrityError):
            adapter.install_compute_input(
                artifact.path,
                task="strategy_backtest",
                config=_strategy_config(),
                data_as_of=headers["X-Data-As-Of"],
                bundle_sha256=headers["X-Bundle-SHA256"],
            )
        assert injected is True
        assert installed.exists()
        assert (installed / "manifest.json").read_bytes() == old_manifest
    finally:
        artifact.cleanup()


def test_client_rejects_data_older_than_requested_freshness_window(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
) -> None:
    config = _strategy_config(end="2026-08-01")
    artifact = bundle_service.build("strategy_backtest", config)
    try:
        adapter = _adapter(tmp_path, _RemoteStub(b"", {}))
        with pytest.raises(ComputeInputIntegrityError):
            adapter.install_compute_input(
                artifact.path,
                task="strategy_backtest",
                config=config,
                data_as_of=artifact.data_as_of,
                bundle_sha256=artifact.bundle_sha256,
            )
    finally:
        artifact.cleanup()


@pytest.mark.parametrize(
    ("data_as_of", "bundle_hash"),
    [
        ("2026-07-19", None),
        ("2026-06-01", None),
        ("2026-07-20", "0" * 64),
    ],
)
def test_client_rejects_header_mismatch_staleness_or_bundle_hash(
    bundle_service: ComputeInputBundleService,
    tmp_path: Path,
    data_as_of: str,
    bundle_hash: str | None,
) -> None:
    artifact, headers = _valid_bundle(bundle_service)
    try:
        adapter = _adapter(tmp_path, _RemoteStub(b"", {}))
        with pytest.raises(ComputeInputIntegrityError):
            adapter.install_compute_input(
                artifact.path,
                task="strategy_backtest",
                config=_strategy_config(),
                data_as_of=data_as_of,
                bundle_sha256=bundle_hash or headers["X-Bundle-SHA256"],
            )
    finally:
        artifact.cleanup()


class _FakeRepo:
    def __init__(self, data_dir: Path) -> None:
        self.store = SimpleNamespace(data_dir=data_dir)

    def get_enriched_latest(self):
        return None, date(2026, 7, 20)


@pytest.fixture
def compute_api_client(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "api-data"
    _seed_market_data(data_dir)
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setitem(settings.__dict__, "workspace_sync_enabled", True)
    monkeypatch.setattr(app.state, "repo", _FakeRepo(data_dir), raising=False)
    monkeypatch.setattr(auth_service, "is_configured", lambda: True)
    monkeypatch.setattr(auth_service, "is_valid_session", lambda token: token == "valid-session")
    if hasattr(app.state, "compute_input_bundle_service"):
        delattr(app.state, "compute_input_bundle_service")
    client = TestClient(app)
    client.cookies.set(auth_api.COOKIE_NAME, "valid-session")
    yield client
    client.close()


def test_compute_input_api_returns_zip_headers_and_cleans_response_lease(
    compute_api_client: TestClient,
) -> None:
    response = compute_api_client.post(
        "/api/workspace/compute-inputs/build",
        json={"task": "strategy_backtest", "config": _strategy_config()},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["x-data-as-of"] == "2026-07-20"
    assert response.headers["x-bundle-sha256"] == hashlib.sha256(response.content).hexdigest()
    with zipfile.ZipFile(io.BytesIO(response.content)):
        pass
    service = app.state.compute_input_bundle_service
    assert not list(service.temp_root.glob("lease-*.zip"))


def test_streaming_response_cleans_lease_when_client_disconnects(
    bundle_service: ComputeInputBundleService,
) -> None:
    artifact = bundle_service.build("strategy_backtest", _strategy_config())

    async def consume_one_chunk_then_disconnect() -> None:
        stream = workspace_api._stream_compute_artifact(artifact, chunk_size=1)
        await anext(stream)
        await stream.aclose()

    asyncio.run(consume_one_chunk_then_disconnect())
    assert not artifact.path.exists()


def test_application_shutdown_closes_compute_bundle_service(tmp_path: Path) -> None:
    data_dir = tmp_path / "shutdown-data"
    _seed_market_data(data_dir)
    service = ComputeInputBundleService(
        data_dir,
        temp_root=tmp_path / "shutdown-bundles",
    )
    service.build("strategy_backtest", _strategy_config()).cleanup()
    fake_app = SimpleNamespace(
        state=SimpleNamespace(compute_input_bundle_service=service),
    )

    main_module._close_compute_input_bundle_service(fake_app)

    assert not service.temp_root.exists()
    assert fake_app.state.compute_input_bundle_service is None


def test_desktop_proxy_keeps_compute_input_orchestration_local() -> None:
    assert route_target("POST", "/api/workspace/compute-inputs/build") == "local"


def test_compute_input_api_requires_workspace_gate(
    compute_api_client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setitem(settings.__dict__, "workspace_sync_enabled", False)
    response = compute_api_client.post(
        "/api/workspace/compute-inputs/build",
        json={"task": "strategy_backtest", "config": _strategy_config()},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "WORKSPACE_SYNC_DISABLED"


def test_compute_input_api_requires_authentication(
    compute_api_client: TestClient,
) -> None:
    compute_api_client.cookies.clear()
    response = compute_api_client.post(
        "/api/workspace/compute-inputs/build",
        json={"task": "strategy_backtest", "config": _strategy_config()},
    )
    assert response.status_code == 401


def test_compute_input_api_requires_session_even_before_password_setup(
    compute_api_client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(auth_service, "is_configured", lambda: False)
    monkeypatch.setattr(auth_service, "is_valid_session", lambda _token: False)
    compute_api_client.cookies.clear()
    response = compute_api_client.post(
        "/api/workspace/compute-inputs/build",
        json={"task": "strategy_backtest", "config": _strategy_config()},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "NOT_INITIALIZED"
