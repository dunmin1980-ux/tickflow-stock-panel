from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

SYMBOLS = {
    "000403.SZ": "派林生物",
    "600489.SH": "中金黄金",
    "300059.SZ": "东方财富",
}
TRADE_DATE = date(2026, 7, 31)
CAPTURED_AT = "2026-08-01T09:00:00+08:00"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
EXPORT_SCRIPT = BACKEND_ROOT / "scripts" / "export_phase2_source_snapshot.py"


def test_phase2_snapshot_service_exposes_approved_contract() -> None:
    spec = importlib.util.find_spec("app.services.phase2_snapshot")
    assert spec is not None, "Phase 2A snapshot service is missing"

    module = importlib.import_module("app.services.phase2_snapshot")
    assert module.SOURCE_TYPE == "existing_cloud_store_snapshot"
    assert module.TRADE_DATE == "2026-07-31"
    assert module.FIXED_SYMBOLS == {
        "000403.SZ": "派林生物",
        "600489.SH": "中金黄金",
        "300059.SZ": "东方财富",
    }
    assert callable(module.build_snapshot_bundle)
    assert callable(module.validate_snapshot_bundle)
    assert callable(module.materialize_snapshot_bundle)
    assert callable(module.validate_snapshot_directory)


@pytest.fixture(scope="module")
def source_store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("phase2-source-store")
    raw_root = root / "kline_daily"
    qfq_root = root / "kline_daily_enriched"
    dates = [TRADE_DATE - timedelta(days=250 - index) for index in range(251)]

    for index, trade_date in enumerate(dates):
        raw_rows: list[dict[str, object]] = []
        qfq_rows: list[dict[str, object]] = []
        factor = 0.9 + index * 0.1 / 250
        for symbol_index, symbol in enumerate(SYMBOLS):
            base = 10.0 + symbol_index * 5.0 + index * 0.01
            raw = {
                "symbol": symbol,
                "date": trade_date,
                "open": base,
                "high": base + 0.05,
                "low": base - 0.03,
                "close": base + 0.02,
                "volume": 1000.0 + index + symbol_index,
                "amount": (1000.0 + index + symbol_index) * (base + 0.02),
            }
            raw_rows.append(raw)
            qfq_rows.append(
                {
                    **raw,
                    "open": raw["open"] * factor,
                    "high": raw["high"] * factor,
                    "low": raw["low"] * factor,
                    "close": raw["close"] * factor,
                }
            )
        raw_path = raw_root / f"date={trade_date.isoformat()}" / "part.parquet"
        qfq_path = qfq_root / f"date={trade_date.isoformat()}" / "part.parquet"
        raw_path.parent.mkdir(parents=True)
        qfq_path.parent.mkdir(parents=True)
        pl.DataFrame(raw_rows).write_parquet(raw_path)
        pl.DataFrame(qfq_rows).write_parquet(qfq_path)

    user_data = root / "user_data"
    user_data.mkdir()
    (user_data / "preferences.json").write_text(
        json.dumps(
            {
                "daily_data_provider": "tickflow",
                "adj_factor_provider": "same_as_daily",
            }
        ),
        encoding="utf-8",
    )
    (root / "capabilities.json").write_text(
        json.dumps({"schema_version": 5, "label": "Pro"}),
        encoding="utf-8",
    )
    job_dir = root / "job_store"
    job_dir.mkdir()
    started_at = datetime(2026, 7, 31, 7, 30, tzinfo=UTC)
    finished_at = datetime(2026, 7, 31, 7, 33, tzinfo=UTC)
    (job_dir / "phase2fixture.json").write_text(
        json.dumps(
            {
                "id": "phase2fixture",
                "status": "succeeded",
                "started_at": started_at.isoformat().replace("+00:00", "Z"),
                "finished_at": finished_at.isoformat().replace("+00:00", "Z"),
                "result": {"daily_days": 1, "enriched_days": 251, "stage_errors": []},
                "log": [
                    {
                        "ts": started_at.isoformat().replace("+00:00", "Z"),
                        "stage": "sync_daily",
                        "msg": "daily sync 2026-07-31",
                    },
                    {
                        "ts": finished_at.isoformat().replace("+00:00", "Z"),
                        "stage": "compute_enriched",
                        "msg": "enriched complete 251 days",
                    },
                    {
                        "ts": finished_at.isoformat().replace("+00:00", "Z"),
                        "stage": "done",
                        "msg": "complete",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return root


@pytest.fixture(scope="module")
def snapshot_bundle(source_store: Path) -> dict[str, object]:
    module = importlib.import_module("app.services.phase2_snapshot")
    return module.build_snapshot_bundle(source_store, CAPTURED_AT)


def test_build_snapshot_bundle_is_read_only_traceable_and_contract_valid(
    source_store: Path,
) -> None:
    module = importlib.import_module("app.services.phase2_snapshot")
    before = module.hash_source_store(source_store)

    bundle = module.build_snapshot_bundle(source_store, CAPTURED_AT)

    assert module.hash_source_store(source_store) == before
    assert module.validate_snapshot_bundle(bundle) == []
    manifest = bundle["manifest"]
    assert manifest["snapshot_schema_version"] == 1
    assert manifest["source_type"] == "existing_cloud_store_snapshot"
    assert manifest["source_store_alias"] == "tickflow_cloud_primary"
    assert manifest["captured_at"] == CAPTURED_AT
    assert manifest["as_of_trade_date"] == "2026-07-31"
    assert manifest["cloud_access_mode"] == "read_only"
    assert manifest["tickflow_api_request_count"] == 0
    assert manifest["cloud_mutation_count"] == 0
    assert manifest["phase1_relation"] == {
        "phase1_status": "PHASE1_OBSERVATION_PASSED",
        "phase1_original_payload_recovered": False,
        "phase1_byte_equivalent": False,
        "usage": "independent_phase2_indicator_source",
    }
    assert manifest["provider_evidence"]["daily_provider"] == "tickflow"
    assert manifest["provider_evidence"]["adjustment_factor_source"] == "same_as_daily"
    assert manifest["provider_evidence"]["capability_label"] == "Pro"
    assert manifest["provider_evidence"]["sync_task_status"] == "success"
    assert manifest["provider_evidence"]["enriched_task_status"] == "success"
    assert manifest["provider_evidence"]["sync_task_id"] == "phase2fixture"

    assert len(manifest["symbols"]) == 3
    for symbol_entry in manifest["symbols"]:
        assert symbol_entry["symbol"] in SYMBOLS
        assert symbol_entry["name"] == SYMBOLS[symbol_entry["symbol"]]
        for basis in ("raw", "qfq"):
            series = symbol_entry[basis]
            assert series["row_count"] == 251
            assert series["last_trade_date"] == "2026-07-31"
            assert len(series["normalized_sha256"]) == 64
            assert len(series["output_sha256"]) == 64

    assert set(bundle["documents"]) == {
        "000403SZ_raw_daily.json",
        "000403SZ_qfq_daily.json",
        "600489SH_raw_daily.json",
        "600489SH_qfq_daily.json",
        "300059SZ_raw_daily.json",
        "300059SZ_qfq_daily.json",
    }
    for document in bundle["documents"].values():
        assert document["source_type"] == "existing_cloud_store_snapshot"
        assert document["phase1_original_payload_recovered"] is False
        assert document["phase1_byte_equivalent"] is False
        dates = [row["trade_date"] for row in document["rows"]]
        assert len(dates) == 251
        assert dates == sorted(set(dates))


def test_snapshot_normalized_business_hash_ignores_only_capture_time(
    source_store: Path,
) -> None:
    module = importlib.import_module("app.services.phase2_snapshot")
    first = module.build_snapshot_bundle(source_store, CAPTURED_AT)
    second = module.build_snapshot_bundle(source_store, "2026-08-01T09:05:00+08:00")

    assert first["manifest"]["captured_at"] != second["manifest"]["captured_at"]
    assert (
        first["manifest"]["normalized_business_sha256"]
        == second["manifest"]["normalized_business_sha256"]
    )
    assert first["documents"] == second["documents"]


def test_provider_selection_uses_application_defaults_when_keys_are_absent() -> None:
    module = importlib.import_module("app.services.phase2_snapshot")

    selection = module.resolve_provider_selection(
        {"onboarding_completed": True, "realtime_quotes_enabled": True}
    )

    assert selection == {
        "daily_provider": "tickflow",
        "daily_provider_selection_origin": "application_default_absent",
        "adjustment_factor_source": "same_as_daily",
        "adjustment_factor_selection_origin": "application_default_absent",
    }


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        (
            lambda bundle: bundle["manifest"].__setitem__(
                "source_type", "phase1_original_payload"
            ),
            "source_type_invalid",
        ),
        (
            lambda bundle: bundle["manifest"]["phase1_relation"].__setitem__(
                "phase1_byte_equivalent", True
            ),
            "phase1_byte_equivalent_must_be_false",
        ),
        (
            lambda bundle: bundle["manifest"].__setitem__(
                "cloud_access_mode", "read_write"
            ),
            "cloud_access_mode_invalid",
        ),
        (
            lambda bundle: bundle["manifest"].__setitem__("cloud_mutation_count", 1),
            "cloud_mutation_count_nonzero",
        ),
        (
            lambda bundle: bundle["manifest"].__setitem__(
                "tickflow_api_request_count", 1
            ),
            "tickflow_api_request_count_nonzero",
        ),
        (
            lambda bundle: bundle["documents"]["000403SZ_raw_daily.json"][
                "rows"
            ][0].__setitem__("close", float("nan")),
            "non_finite_number:000403SZ_raw_daily.json:/rows/0/close",
        ),
        (
            lambda bundle: bundle["documents"]["000403SZ_raw_daily.json"][
                "rows"
            ][1].__setitem__(
                "trade_date",
                bundle["documents"]["000403SZ_raw_daily.json"]["rows"][0][
                    "trade_date"
                ],
            ),
            "trade_dates_not_unique:000403SZ_raw_daily.json",
        ),
        (
            lambda bundle: bundle["documents"]["000403SZ_qfq_daily.json"][
                "rows"
            ][-1].__setitem__("trade_date", "2026-08-01"),
            "future_trade_date:000403SZ_qfq_daily.json",
        ),
        (
            lambda bundle: bundle["manifest"]["provider_evidence"].__setitem__(
                "daily_provider", "unknown"
            ),
            "daily_provider_invalid",
        ),
        (
            lambda bundle: bundle.__setitem__("api_key", "secret-canary"),
            "secret_field:/api_key",
        ),
        (
            lambda bundle: bundle.__setitem__("buy_price", 10.0),
            "forbidden_trading_field:/buy_price",
        ),
    ],
)
def test_validate_snapshot_bundle_rejects_invalid_or_unsafe_content(
    snapshot_bundle: dict[str, object],
    mutation,
    expected_error: str,
) -> None:
    module = importlib.import_module("app.services.phase2_snapshot")
    bundle = copy.deepcopy(snapshot_bundle)
    mutation(bundle)

    errors = module.validate_snapshot_bundle(bundle)

    assert expected_error in errors


def test_materialize_and_validate_snapshot_directory(
    snapshot_bundle: dict[str, object], tmp_path: Path
) -> None:
    module = importlib.import_module("app.services.phase2_snapshot")
    output_dir = tmp_path / "snapshot"

    result = module.materialize_snapshot_bundle(snapshot_bundle, output_dir)
    validation = module.validate_snapshot_directory(output_dir)

    assert result["status"] == "SNAPSHOT_VALID"
    assert validation["status"] == "SNAPSHOT_VALID"
    assert validation["errors"] == []
    assert validation["metrics"] == {
        "symbol_count": 3,
        "raw_series_valid": 3,
        "qfq_series_valid": 3,
        "non_finite_numbers": 0,
        "secret_hits": 0,
        "trading_field_hits": 0,
        "cloud_mutation_count": 0,
        "tickflow_api_request_count": 0,
    }
    assert {path.name for path in output_dir.iterdir()} == {
        "manifest.json",
        "snapshot_validation.json",
        *snapshot_bundle["documents"],
    }


def test_source_partition_aggregate_hash_is_independently_checked(
    snapshot_bundle: dict[str, object],
) -> None:
    module = importlib.import_module("app.services.phase2_snapshot")
    bundle = copy.deepcopy(snapshot_bundle)
    bundle["manifest"]["source_partitions"]["raw"]["files"][0]["sha256"] = "0" * 64

    errors = module.validate_snapshot_bundle(bundle)

    assert "source_partition_aggregate_hash_mismatch:raw" in errors


def test_raw_qfq_date_set_mismatch_is_rejected(
    snapshot_bundle: dict[str, object],
) -> None:
    module = importlib.import_module("app.services.phase2_snapshot")
    bundle = copy.deepcopy(snapshot_bundle)
    bundle["documents"]["000403SZ_qfq_daily.json"]["rows"][0][
        "trade_date"
    ] = "2025-11-22"

    errors = module.validate_snapshot_bundle(bundle)

    assert "raw_qfq_date_set_mismatch:000403.SZ" in errors


def test_materialization_is_idempotent(
    snapshot_bundle: dict[str, object], tmp_path: Path
) -> None:
    module = importlib.import_module("app.services.phase2_snapshot")
    output_dir = tmp_path / "snapshot"

    first = module.materialize_snapshot_bundle(snapshot_bundle, output_dir)
    first_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in output_dir.iterdir()
    }
    second = module.materialize_snapshot_bundle(snapshot_bundle, output_dir)
    second_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in output_dir.iterdir()
    }

    assert second == first
    assert second_hashes == first_hashes


def test_materialization_failure_preserves_previous_directory(
    snapshot_bundle: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = importlib.import_module("app.services.phase2_snapshot")
    output_dir = tmp_path / "snapshot"
    module.materialize_snapshot_bundle(snapshot_bundle, output_dir)
    before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in output_dir.iterdir()
    }
    real_write = module._write_fsynced
    call_count = 0

    def fail_during_staging(path: Path, value: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            raise OSError("simulated staging write failure")
        real_write(path, value)

    monkeypatch.setattr(module, "_write_fsynced", fail_during_staging)

    with pytest.raises(OSError, match="simulated staging write failure"):
        module.materialize_snapshot_bundle(snapshot_bundle, output_dir)

    after = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in output_dir.iterdir()
    }
    assert after == before
    assert not list(tmp_path.glob(".snapshot.staging-*"))


def test_snapshot_directory_rejects_symlinked_file(
    snapshot_bundle: dict[str, object], tmp_path: Path
) -> None:
    module = importlib.import_module("app.services.phase2_snapshot")
    output_dir = tmp_path / "snapshot"
    module.materialize_snapshot_bundle(snapshot_bundle, output_dir)
    manifest = output_dir / "manifest.json"
    external = tmp_path / "external.json"
    manifest.rename(external)
    manifest.symlink_to(external)

    validation = module.validate_snapshot_directory(output_dir)

    assert validation["status"] == "SNAPSHOT_INVALID"


def test_source_store_rejects_symlinked_preferences(tmp_path: Path) -> None:
    module = importlib.import_module("app.services.phase2_snapshot")
    data_dir = tmp_path / "data"
    user_data = data_dir / "user_data"
    user_data.mkdir(parents=True)
    external = tmp_path / "preferences.json"
    external.write_text("{}", encoding="utf-8")
    (user_data / "preferences.json").symlink_to(external)

    with pytest.raises(module.SnapshotError, match="must not be a symlink"):
        module.hash_source_store(data_dir)


def test_export_cli_source_mode_emits_bundle_without_source_mutation(
    source_store: Path,
) -> None:
    module = importlib.import_module("app.services.phase2_snapshot")
    before = module.hash_source_store(source_store)

    completed = subprocess.run(
        [
            sys.executable,
            str(EXPORT_SCRIPT),
            "--data-dir",
            str(source_store),
            "--captured-at",
            CAPTURED_AT,
            "--stdout-bundle",
        ],
        cwd=BACKEND_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    bundle = json.loads(completed.stdout)
    assert module.validate_snapshot_bundle(bundle) == []
    assert module.hash_source_store(source_store) == before
    assert "SNAPSHOT_EXPORTED" in completed.stderr
    assert "api_key" not in completed.stderr.lower()


def test_export_cli_stdin_mode_materializes_valid_bundle(
    snapshot_bundle: dict[str, object], tmp_path: Path
) -> None:
    output_dir = tmp_path / "snapshot"

    completed = subprocess.run(
        [
            sys.executable,
            str(EXPORT_SCRIPT),
            "--stdin-bundle",
            "--output-dir",
            str(output_dir),
        ],
        cwd=BACKEND_ROOT,
        check=False,
        input=json.dumps(snapshot_bundle, ensure_ascii=False, allow_nan=False),
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "SNAPSHOT_VALID"
    assert result["tickflow_api_request_count"] == 0
    assert result["cloud_mutation_count"] == 0
    assert importlib.import_module(
        "app.services.phase2_snapshot"
    ).validate_snapshot_directory(output_dir)["status"] == "SNAPSHOT_VALID"


def test_export_cli_rejects_mixed_modes(
    source_store: Path, tmp_path: Path
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(EXPORT_SCRIPT),
            "--data-dir",
            str(source_store),
            "--captured-at",
            CAPTURED_AT,
            "--stdout-bundle",
            "--stdin-bundle",
            "--output-dir",
            str(tmp_path / "snapshot"),
        ],
        cwd=BACKEND_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0


def test_exporter_imports_no_network_vendor_ai_or_database_clients() -> None:
    banned = {
        "httpx",
        "requests",
        "aiohttp",
        "tickflow",
        "openai",
        "anthropic",
        "sqlalchemy",
        "duckdb",
        "psycopg",
        "pymysql",
    }
    paths = [
        EXPORT_SCRIPT,
        BACKEND_ROOT / "app" / "services" / "phase2_snapshot.py",
    ]
    for path in paths:
        source = path.read_text(encoding="utf-8")
        tree = __import__("ast").parse(source)
        imports = {
            alias.name.split(".", 1)[0]
            for node in __import__("ast").walk(tree)
            if isinstance(node, (__import__("ast").Import, __import__("ast").ImportFrom))
            for alias in node.names
        }
        assert imports.isdisjoint(banned), f"banned import in {path}: {imports & banned}"


def test_repeat_export_comparison_is_recorded_and_revalidated(
    snapshot_bundle: dict[str, object], tmp_path: Path
) -> None:
    module = importlib.import_module("app.services.phase2_snapshot")
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    repeated = copy.deepcopy(snapshot_bundle)
    repeated["manifest"]["captured_at"] = "2026-08-01T09:05:00+08:00"
    module.materialize_snapshot_bundle(snapshot_bundle, first_dir)
    module.materialize_snapshot_bundle(repeated, second_dir)

    comparison = module.record_repeat_export_validation(first_dir, second_dir)
    validation = module.validate_snapshot_directory(first_dir)
    recorded = json.loads((first_dir / "snapshot_validation.json").read_text())

    assert comparison == {
        "status": "IDEMPOTENT",
        "normalized_business_hash_match": True,
        "document_hashes_match": True,
        "source_partition_hashes_match": True,
        "capture_timestamps_differ": True,
        "normalized_business_sha256": snapshot_bundle["manifest"][
            "normalized_business_sha256"
        ],
        "document_sha256": {
            filename: hashlib.sha256((first_dir / filename).read_bytes()).hexdigest()
            for filename in sorted(snapshot_bundle["documents"])
        },
        "source_partition_sha256": {
            basis: snapshot_bundle["manifest"]["source_partitions"][basis][
                "aggregate_sha256"
            ]
            for basis in ("raw", "qfq")
        },
    }
    assert recorded["repeat_export"] == comparison
    assert validation["status"] == "SNAPSHOT_VALID"
    assert validation["repeat_export"] == comparison
