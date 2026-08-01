from __future__ import annotations

import ast
import copy
import hashlib
import importlib
import importlib.util
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_ROOT = REPO_ROOT / "reports/phase2_source_snapshot/2026-07-31"
SYMBOLS = {
    "000403.SZ": "派林生物",
    "600489.SH": "中金黄金",
    "300059.SZ": "东方财富",
}
VENDOR_PENDING = [
    "intraday_batch_entitlement",
    "first_30m_bucket_includes_09_30",
    "volume_unit",
    "amount_unit",
]
INDICATORS = {
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "macd_dif",
    "macd_dea",
    "macd_hist",
    "rsi6",
    "rsi14",
    "boll_upper",
    "boll_middle",
    "boll_lower",
    "atr14",
    "volume_ma5",
    "volume_ma10",
    "volume_ratio",
}
EXPECTED_DAILY = {
    "000403.SZ": {
        "open": 10.36,
        "high": 10.47,
        "low": 10.25,
        "close": 10.43,
        "volume": 107884.0,
        "amount": 111975400.0,
    },
    "600489.SH": {
        "open": 22.18,
        "high": 23.28,
        "low": 22.14,
        "close": 23.01,
        "volume": 1063929.0,
        "amount": 2433954100.0,
    },
    "300059.SZ": {
        "open": 19.98,
        "high": 20.52,
        "low": 19.91,
        "close": 20.17,
        "volume": 3424840.0,
        "amount": 6938438000.0,
    },
}
EXPECTED_INDICATORS = {
    "000403.SZ": {
        "ma5": 10.136,
        "ma10": 9.866,
        "ma20": 9.6425,
        "ma60": 10.683,
        "macd_dif": -0.007465131240531164,
        "macd_dea": -0.18656732410368454,
        "macd_hist": 0.35820438572630675,
        "rsi6": 76.21830812329453,
        "rsi14": 58.59950492944132,
        "boll_upper": 10.501063672039606,
        "boll_middle": 9.6425,
        "boll_lower": 8.783936327960394,
        "atr14": 0.429555321578577,
        "volume_ma5": 122719.2,
        "volume_ma10": 137735.6,
        "volume_ratio": 0.8720365355858223,
    },
    "600489.SH": {
        "ma5": 21.906,
        "ma10": 21.576,
        "ma20": 20.779500000000002,
        "ma60": 21.82283333333333,
        "macd_dif": 0.36503679211833884,
        "macd_dea": 0.042592094253742524,
        "macd_hist": 0.6448893957291926,
        "rsi6": 74.95739861741134,
        "rsi14": 62.358731346228076,
        "boll_upper": 22.962153865077298,
        "boll_middle": 20.779500000000002,
        "boll_lower": 18.596846134922707,
        "atr14": 1.1134470901367544,
        "volume_ma5": 810624.6,
        "volume_ma10": 955557.5,
        "volume_ratio": 1.3805218720918222,
    },
    "300059.SZ": {
        "ma5": 19.826,
        "ma10": 19.936,
        "ma20": 20.042,
        "ma60": 19.782666666666668,
        "macd_dif": -0.013730564457485883,
        "macd_dea": 0.012188214888996223,
        "macd_hist": -0.05183755869296421,
        "rsi6": 57.77034061172677,
        "rsi14": 52.28308759960673,
        "boll_upper": 20.86650878518162,
        "boll_middle": 20.042,
        "boll_lower": 19.217491214818384,
        "atr14": 0.6825505771218069,
        "volume_ma5": 2627315.8,
        "volume_ma10": 2972586.1,
        "volume_ratio": 1.3376076287424494,
    },
}


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _directory_bytes(path: Path) -> dict[str, bytes]:
    return {
        item.relative_to(path).as_posix(): item.read_bytes()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def test_phase2_facts_service_exposes_required_contract() -> None:
    spec = importlib.util.find_spec("app.services.phase2_facts")
    assert spec is not None, "Phase 2A Facts service is missing"

    module = importlib.import_module("app.services.phase2_facts")
    assert callable(module.build_symbol_facts)
    assert callable(module.validate_facts_document)
    assert callable(module.canonical_json_bytes)


def test_build_symbol_facts_separates_snapshot_and_phase1_evidence() -> None:
    module = importlib.import_module("app.services.phase2_facts")

    for symbol, name in SYMBOLS.items():
        facts = module.build_symbol_facts(REPO_ROOT, symbol)
        indicator_source = facts["source_evidence"]["indicator_source"]
        phase1 = facts["source_evidence"]["phase1_contract_evidence"]

        assert facts["facts_schema_version"] == 1
        assert facts["source_system"] == "tickflow-stock-panel"
        assert facts["symbol"] == symbol
        assert facts["name"] == name
        assert facts["trade_date"] == "2026-07-31"
        assert facts["timezone"] == "Asia/Shanghai"
        assert facts["data_freshness"] == "fresh"
        assert facts["content_readiness"] == "READY_FOR_AI_REVIEW"
        assert facts["missing_source_inputs"] == []

        assert indicator_source["source_type"] == "existing_cloud_store_snapshot"
        assert indicator_source["source_snapshot_manifest"] == (
            "reports/phase2_source_snapshot/2026-07-31/manifest.json"
        )
        assert indicator_source["phase1_original_payload_recovered"] is False
        assert indicator_source["phase1_byte_equivalent"] is False
        assert indicator_source["request_mode"] == "offline_existing_cloud_evidence"
        assert indicator_source["cloud_access_mode"] == "read_only"
        assert indicator_source["cloud_mutation_count"] == 0
        assert indicator_source["tickflow_api_request_count"] == 0
        assert indicator_source["row_count"] == 251
        assert indicator_source["last_trade_date"] == "2026-07-31"
        assert set(indicator_source["source_hashes"]) == {
            "manifest",
            "raw_daily",
            "qfq_daily",
            "indicator_implementation",
        }
        assert all(len(value) == 64 for value in indicator_source["source_hashes"].values())

        assert phase1["phase1_status"] == "PHASE1_OBSERVATION_PASSED"
        assert phase1["observation_days"] == 5
        assert len(phase1["source_files"]) == 7
        assert all(len(item["sha256"]) == 64 for item in phase1["source_files"])

        assert facts["price_basis"] == {
            "daily_ohlcv": "raw",
            "indicator_prices": "qfq",
            "indicator_volume": "raw",
            "minute": "none",
        }
        assert facts["units"] == {
            "volume": "VENDOR_CONFIRMATION_PENDING",
            "amount": "VENDOR_CONFIRMATION_PENDING",
        }
        assert facts["daily"]["status"] == "AVAILABLE"
        assert facts["daily"]["price_basis"] == "raw"
        assert facts["daily"]["trade_date"] == "2026-07-31"
        for field, expected in EXPECTED_DAILY[symbol].items():
            assert facts["daily"][field] == expected

        assert facts["minute_1m"]["bar_count"] == 241
        assert facts["minute_1m"]["material_ohlc_anomaly_count"] == 0
        assert facts["minute_30m"]["bar_count"] == 8
        assert facts["minute_30m"]["ohlc_mismatch_count"] == 0
        assert facts["adjustment_factors"]["qfq_relation_match_rate"] == 1.0

        assert set(facts["indicators"]) == INDICATORS
        for indicator, item in facts["indicators"].items():
            assert item["status"] == "VALID"
            assert isinstance(item["value"], float)
            assert math.isfinite(item["value"])
            assert item["implementation"] == {
                "module": "app.indicators.pipeline",
                "function": "compute_indicators",
                "output_column": module.INDICATOR_COLUMNS[indicator],
            }
            assert item["history_window"] == {
                "row_count": 251,
                "first_trade_date": indicator_source["first_trade_date"],
                "last_trade_date": "2026-07-31",
            }
            assert item["result_precision"] == "float64_unrounded"
            assert item["data_basis"] == (
                "raw" if indicator.startswith("volume_") else "qfq"
            )

        assert facts["indicators"]["volume_ma5"]["volume_unit"] == (
            "VENDOR_CONFIRMATION_PENDING"
        )
        assert facts["indicators"]["volume_ma10"]["source_unit_unconfirmed"] is True
        assert facts["indicators"]["volume_ratio"]["dimensionless"] is True
        assert facts["indicators"]["volume_ratio"]["source_unit_unconfirmed"] is True
        assert facts["key_levels"] == {
            "status": "NOT_IMPLEMENTED",
            "observed_supports": [],
            "observed_resistances": [],
            "calculation_method": None,
            "reason": "No approved deterministic key-level algorithm",
        }
        assert facts["vendor_pending"] == VENDOR_PENDING
        assert facts["trading"] is False
        assert facts["generation"] == {
            "mode": "offline_existing_cloud_evidence",
            "new_tickflow_api_request_count": 0,
            "cloud_mutation_count": 0,
            "ai_calls": 0,
        }


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_indicator_values_match_the_approved_pipeline_snapshot(symbol: str) -> None:
    module = importlib.import_module("app.services.phase2_facts")
    facts = module.build_symbol_facts(REPO_ROOT, symbol)

    for indicator, expected in EXPECTED_INDICATORS[symbol].items():
        assert facts["indicators"][indicator]["value"] == pytest.approx(
            expected, rel=0, abs=1e-12
        )


def test_builder_calls_official_pipeline_once_with_qfq_prices_and_raw_volume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("app.services.phase2_facts")
    real_compute = module.compute_indicators
    calls: list[set[str]] = []
    raw = _load_json(SNAPSHOT_ROOT / "000403SZ_raw_daily.json")["rows"]
    qfq = _load_json(SNAPSHOT_ROOT / "000403SZ_qfq_daily.json")["rows"]

    def spy(frame, needed=None):
        calls.append(set(needed or set()))
        latest = frame.sort("date").tail(1).to_dicts()[0]
        assert latest["close"] == qfq[-1]["close"]
        assert latest["volume"] == raw[-1]["volume"]
        return real_compute(frame, needed=needed)

    monkeypatch.setattr(module, "compute_indicators", spy)

    module.build_symbol_facts(REPO_ROOT, "000403.SZ")

    assert calls == [set(module.INDICATOR_COLUMNS.values())]


def test_validate_facts_accepts_the_builder_output() -> None:
    module = importlib.import_module("app.services.phase2_facts")

    for symbol in SYMBOLS:
        facts = module.build_symbol_facts(REPO_ROOT, symbol)
        assert module.validate_facts_document(REPO_ROOT, facts) == []


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        (
            lambda facts: facts["source_evidence"]["indicator_source"][
                "source_hashes"
            ].__setitem__("raw_daily", "0" * 64),
            "source_hash_mismatch",
        ),
        (
            lambda facts: facts["source_evidence"]["indicator_source"].__setitem__(
                "source_type", "phase1_source_evidence"
            ),
            "indicator_source_type_invalid",
        ),
        (
            lambda facts: facts["source_evidence"]["indicator_source"].__setitem__(
                "phase1_byte_equivalent", True
            ),
            "phase1_byte_equivalent_must_be_false",
        ),
        (
            lambda facts: facts["source_evidence"]["indicator_source"].__setitem__(
                "cloud_mutation_count", 1
            ),
            "cloud_mutation_count_nonzero",
        ),
        (
            lambda facts: facts["source_evidence"]["indicator_source"].__setitem__(
                "tickflow_api_request_count", 1
            ),
            "tickflow_api_request_count_nonzero",
        ),
        (
            lambda facts: facts["numeric_provenance"].pop("/daily/close"),
            "numeric_provenance_missing:/daily/close",
        ),
        (
            lambda facts: facts["daily"].__setitem__("close", 10.25),
            "numeric_provenance_value_mismatch:/daily/close",
        ),
        (
            lambda facts: facts["indicators"]["ma5"].__setitem__("value", 10.2),
            "indicator_value_mismatch:ma5",
        ),
        (
            lambda facts: facts["indicators"]["ma5"].__setitem__("value", math.nan),
            "non_finite_number:/indicators/ma5/value",
        ),
        (
            lambda facts: facts.__setitem__("api_key", "tf-secret-canary-value"),
            "secret_field:/api_key",
        ),
        (
            lambda facts: facts.__setitem__("buy_price", 10.0),
            "forbidden_trading_field:/buy_price",
        ),
        (
            lambda facts: facts.__setitem__(
                "price_basis",
                {
                    "daily_ohlcv": "qfq",
                    "indicator_prices": "raw",
                    "indicator_volume": "qfq",
                    "minute": "none",
                },
            ),
            "raw_qfq_basis_invalid",
        ),
        (
            lambda facts: facts["units"].__setitem__("volume", "shares"),
            "volume_unit_invalid",
        ),
        (
            lambda facts: facts["indicators"]["volume_ratio"].__setitem__(
                "dimensionless", False
            ),
            "volume_ratio_dimensionless_invalid",
        ),
        (
            lambda facts: facts["vendor_pending"].remove("amount_unit"),
            "vendor_pending_invalid",
        ),
        (
            lambda facts: facts["key_levels"]["observed_supports"].append(10.0),
            "key_levels_invalid",
        ),
    ],
)
def test_validate_facts_rejects_untraceable_or_unsafe_content(
    mutation,
    expected_error: str,
) -> None:
    module = importlib.import_module("app.services.phase2_facts")
    facts = copy.deepcopy(module.build_symbol_facts(REPO_ROOT, "000403.SZ"))
    mutation(facts)

    errors = module.validate_facts_document(REPO_ROOT, facts)

    assert expected_error in errors


def test_every_numeric_value_has_provenance() -> None:
    module = importlib.import_module("app.services.phase2_facts")
    facts = module.build_symbol_facts(REPO_ROOT, "000403.SZ")

    assert set(module.numeric_paths(facts)) == set(facts["numeric_provenance"])
    assert not [
        entry
        for entry in facts["numeric_provenance"].values()
        if not entry.get("calculation_function") or not entry.get("price_basis")
    ]


def test_canonical_json_rejects_nan_and_is_byte_stable() -> None:
    module = importlib.import_module("app.services.phase2_facts")
    facts = module.build_symbol_facts(REPO_ROOT, "000403.SZ")

    assert module.canonical_json_bytes(facts) == module.canonical_json_bytes(facts)
    with pytest.raises(ValueError):
        module.canonical_json_bytes({"invalid": math.inf})


def _copy_all_facts_inputs(destination: Path) -> None:
    module = importlib.import_module("app.services.phase2_facts")
    paths: set[str] = set()
    for symbol in SYMBOLS:
        facts = module.build_symbol_facts(REPO_ROOT, symbol)
        for domain in facts["source_evidence"].values():
            for item in domain.get("source_files", []):
                paths.add(item["path"])
    for relative in sorted(paths):
        source = REPO_ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def test_build_facts_directory_is_idempotent_and_records_zero_requests(
    tmp_path: Path,
) -> None:
    builder = importlib.import_module("scripts.build_phase2_facts")
    validator = importlib.import_module("scripts.validate_phase2_facts")
    output_dir = tmp_path / "phase2_facts"

    first = builder.build_facts_directory(REPO_ROOT, output_dir)
    first_bytes = _directory_bytes(output_dir)
    second = builder.build_facts_directory(REPO_ROOT, output_dir)
    second_bytes = _directory_bytes(output_dir)

    assert first == second
    assert first_bytes == second_bytes
    assert first["status"] == "FACTS_VALID"
    assert first["new_tickflow_api_request_count"] == 0
    assert first["cloud_mutation_count"] == 0
    assert first["ai_calls"] == 0
    assert first["content_readiness"] == "READY_FOR_AI_REVIEW"
    assert first["source_type"] == "existing_cloud_store_snapshot"
    assert first["build_mode"] == "offline_existing_cloud_evidence"
    assert set(first["output_hashes"]) == {
        "000403SZ_facts.json",
        "600489SH_facts.json",
        "300059SZ_facts.json",
    }
    validation = validator.validate_facts_directory(REPO_ROOT, output_dir)
    assert validation["status"] == "FACTS_VALID"
    assert validation["metrics"] == {
        "unsupported_numeric_claims": 0,
        "non_finite_numbers": 0,
        "source_hash_mismatches": 0,
        "secret_hits": 0,
        "trading_field_hits": 0,
    }


def test_failed_rebuild_preserves_the_previous_facts_directory(tmp_path: Path) -> None:
    builder = importlib.import_module("scripts.build_phase2_facts")
    module = importlib.import_module("app.services.phase2_facts")
    fixture_root = tmp_path / "repo"
    _copy_all_facts_inputs(fixture_root)
    output_dir = fixture_root / "reports/phase2_facts"
    builder.build_facts_directory(fixture_root, output_dir)
    before = _directory_bytes(output_dir)

    summary_path = fixture_root / "reports/phase1_observation/2026-07-31/daily_summary.json"
    summary = _load_json(summary_path)
    summary["status"] = "DAY_BLOCKED"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    with pytest.raises(module.Phase2FactsError):
        builder.build_facts_directory(fixture_root, output_dir)

    assert _directory_bytes(output_dir) == before
    assert not list(output_dir.parent.glob(".phase2_facts.staging-*"))
    assert not list(output_dir.parent.glob(".phase2_facts.backup-*"))


def test_validate_facts_directory_rejects_a_tampered_document(tmp_path: Path) -> None:
    builder = importlib.import_module("scripts.build_phase2_facts")
    validator = importlib.import_module("scripts.validate_phase2_facts")
    output_dir = tmp_path / "phase2_facts"
    builder.build_facts_directory(REPO_ROOT, output_dir)
    path = output_dir / "000403SZ_facts.json"
    facts = _load_json(path)
    facts["indicators"]["ma5"]["value"] = 0.0
    path.write_text(json.dumps(facts, ensure_ascii=False), encoding="utf-8")

    result = validator.validate_facts_directory(REPO_ROOT, output_dir)

    assert result["status"] == "FACTS_INVALID"
    assert result["symbols"]["000403.SZ"] == "INVALID"
    assert "indicator_value_mismatch:ma5" in result["errors"]["000403.SZ"]


def test_validate_facts_directory_rejects_forged_aggregate_source_manifest(
    tmp_path: Path,
) -> None:
    builder = importlib.import_module("scripts.build_phase2_facts")
    validator = importlib.import_module("scripts.validate_phase2_facts")
    output_dir = tmp_path / "phase2_facts"
    builder.build_facts_directory(REPO_ROOT, output_dir)
    manifest_path = output_dir / "build_manifest.json"
    manifest = _load_json(manifest_path)
    manifest["source_files"] = []
    manifest["source_set_sha256"] = hashlib.sha256(b"[]").hexdigest()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    result = validator.validate_facts_directory(REPO_ROOT, output_dir)

    assert result["status"] == "FACTS_INVALID"
    assert "manifest_source_files_mismatch" in result["errors"]["directory"]


def test_validate_facts_directory_rejects_symlinked_directory(tmp_path: Path) -> None:
    builder = importlib.import_module("scripts.build_phase2_facts")
    validator = importlib.import_module("scripts.validate_phase2_facts")
    real_dir = tmp_path / "real-facts"
    link_dir = tmp_path / "linked-facts"
    builder.build_facts_directory(REPO_ROOT, real_dir)
    link_dir.symlink_to(real_dir, target_is_directory=True)

    result = validator.validate_facts_directory(REPO_ROOT, link_dir)

    assert result["status"] == "FACTS_INVALID"
    assert "facts_directory_is_symlink" in result["errors"]["directory"]


def test_source_evidence_is_unchanged_by_repeated_facts_builds(tmp_path: Path) -> None:
    builder = importlib.import_module("scripts.build_phase2_facts")
    protected = {
        path.relative_to(REPO_ROOT).as_posix(): _sha256(path)
        for root in (
            REPO_ROOT / "reports/phase1_observation",
            REPO_ROOT / "reports/phase2_source_snapshot/2026-07-31",
        )
        for path in root.rglob("*")
        if path.is_file()
    }
    protected["reports/phase1_tickflow_request_audit.json"] = _sha256(
        REPO_ROOT / "reports/phase1_tickflow_request_audit.json"
    )

    builder.build_facts_directory(REPO_ROOT, tmp_path / "facts")
    builder.build_facts_directory(REPO_ROOT, tmp_path / "facts")

    assert {
        relative: _sha256(REPO_ROOT / relative) for relative in protected
    } == protected


def test_builder_rejects_symbols_outside_the_fixed_scope() -> None:
    module = importlib.import_module("app.services.phase2_facts")
    with pytest.raises(module.Phase2FactsError, match="outside the fixed"):
        module.build_symbol_facts(REPO_ROOT, "600519.SH")


def test_builder_rejects_symlinked_source_evidence(tmp_path: Path) -> None:
    module = importlib.import_module("app.services.phase2_facts")
    fixture_root = tmp_path / "repo"
    _copy_all_facts_inputs(fixture_root)
    source = fixture_root / "reports/phase1_observation/observation_index.json"
    outside = tmp_path / "outside.json"
    shutil.copy2(source, outside)
    source.unlink()
    source.symlink_to(outside)

    with pytest.raises(module.Phase2FactsError, match="symlink"):
        module.build_symbol_facts(fixture_root, "000403.SZ")


def test_builder_rejects_symlinked_snapshot_document(tmp_path: Path) -> None:
    module = importlib.import_module("app.services.phase2_facts")
    fixture_root = tmp_path / "repo"
    _copy_all_facts_inputs(fixture_root)
    source = (
        fixture_root
        / "reports/phase2_source_snapshot/2026-07-31/000403SZ_raw_daily.json"
    )
    outside = tmp_path / "outside.json"
    shutil.copy2(source, outside)
    source.unlink()
    source.symlink_to(outside)

    with pytest.raises(module.Phase2FactsError, match="snapshot"):
        module.build_symbol_facts(fixture_root, "000403.SZ")


def test_builder_rejects_symlinked_output_directory(tmp_path: Path) -> None:
    builder = importlib.import_module("scripts.build_phase2_facts")
    module = importlib.import_module("app.services.phase2_facts")
    outside = tmp_path / "outside"
    outside.mkdir()
    output_link = tmp_path / "phase2_facts"
    output_link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(module.Phase2FactsError, match="symlink"):
        builder.build_facts_directory(REPO_ROOT, output_link)

    assert not list(outside.iterdir())


def test_cli_entrypoints_build_and_validate_without_network(tmp_path: Path) -> None:
    output_dir = tmp_path / "facts"
    build = subprocess.run(
        [
            sys.executable,
            "scripts/build_phase2_facts.py",
            "--repo-root",
            str(REPO_ROOT),
            "--output-dir",
            str(output_dir),
        ],
        cwd=REPO_ROOT / "backend",
        check=False,
        capture_output=True,
        text=True,
    )
    validate = subprocess.run(
        [
            sys.executable,
            "scripts/validate_phase2_facts.py",
            "--repo-root",
            str(REPO_ROOT),
            "--facts-dir",
            str(output_dir),
        ],
        cwd=REPO_ROOT / "backend",
        check=False,
        capture_output=True,
        text=True,
    )

    assert build.returncode == 0, build.stderr
    assert validate.returncode == 0, validate.stderr
    assert json.loads(build.stdout)["status"] == "FACTS_VALID"
    assert json.loads(validate.stdout)["status"] == "FACTS_VALID"


def test_phase2a_modules_do_not_import_network_ai_or_provider_clients() -> None:
    forbidden_imports = {
        "aiohttp",
        "anthropic",
        "httpx",
        "openai",
        "requests",
        "tickflow",
    }
    paths = [
        REPO_ROOT / "backend/app/services/phase2_facts.py",
        REPO_ROOT / "backend/scripts/build_phase2_facts.py",
        REPO_ROOT / "backend/scripts/validate_phase2_facts.py",
    ]
    imported: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])

    assert imported.isdisjoint(forbidden_imports)
