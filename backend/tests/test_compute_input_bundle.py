from __future__ import annotations

import hashlib
import io
import json
import stat
import zipfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.config import settings
from app.desktop_client.cache import WorkspaceCache
from app.desktop_client.proxy import route_target
from app.desktop_client.workspace_adapter import (
    CloudWorkspaceAdapter,
    ComputeInputIntegrityError,
)
from app.main import app
from app.services import auth as auth_service
from app.services.compute_input_bundle import (
    ComputeInputBundleService,
    ComputeInputConfigError,
    compute_cache_key,
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
