from __future__ import annotations

import ast
import copy
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


def test_phase2_facts_service_exposes_required_contract() -> None:
    spec = importlib.util.find_spec("app.services.phase2_facts")
    assert spec is not None, "Phase 2A Facts service is missing"

    module = importlib.import_module("app.services.phase2_facts")
    assert callable(module.build_symbol_facts)
    assert callable(module.validate_facts_document)
    assert callable(module.canonical_json_bytes)


def test_build_symbol_facts_uses_only_retained_phase1_contracts() -> None:
    module = importlib.import_module("app.services.phase2_facts")

    for symbol, name in SYMBOLS.items():
        facts = module.build_symbol_facts(REPO_ROOT, symbol)

        assert facts["facts_schema_version"] == 1
        assert facts["source_system"] == "tickflow-stock-panel"
        assert facts["symbol"] == symbol
        assert facts["name"] == name
        assert facts["trade_date"] == "2026-07-31"
        assert facts["timezone"] == "Asia/Shanghai"
        assert facts["data_freshness"] == "fresh"
        assert facts["content_readiness"] == "BLOCKED_SOURCE_EVIDENCE"
        assert facts["missing_source_inputs"] == [
            "retained_raw_daily_ohlcv_values",
            "retained_qfq_daily_ohlcv_series",
        ]
        assert facts["source_evidence"]["phase1_status"] == "PHASE1_OBSERVATION_PASSED"
        assert facts["source_evidence"]["observation_days"] == 5
        assert len(facts["source_evidence"]["source_files"]) == 7
        assert all(
            len(item["sha256"]) == 64
            for item in facts["source_evidence"]["source_files"]
        )

        assert facts["price_basis"] == {
            "daily_ohlcv": "none",
            "indicator_prices": "qfq",
            "indicator_volume": "none",
            "minute": "none",
        }
        assert facts["daily"]["status"] == "UNAVAILABLE_SOURCE_EVIDENCE"
        assert facts["daily"]["contract_status"] == "PASSED"
        assert facts["daily"]["latest_raw_trade_date"] == "2026-07-31"
        assert facts["daily"]["latest_qfq_trade_date"] == "2026-07-31"
        assert all(
            facts["daily"][field] is None
            for field in ("open", "high", "low", "close", "volume", "amount")
        )

        assert facts["minute_1m"]["bar_count"] == 241
        assert facts["minute_1m"]["material_ohlc_anomaly_count"] == 0
        assert facts["minute_1m"]["material_negative_amount_count"] == 0
        assert facts["minute_30m"]["bar_count"] == 8
        assert facts["minute_30m"]["ohlc_mismatch_count"] == 0
        assert facts["adjustment_factors"]["factor_count"] > 0
        assert facts["adjustment_factors"]["qfq_relation_match_rate"] == 1.0

        assert set(facts["indicators"]) == INDICATORS
        for indicator, item in facts["indicators"].items():
            assert item["status"] == "NOT_IMPLEMENTED"
            assert item["value"] is None
            expected_basis = "none" if indicator.startswith("volume_") else "qfq"
            assert item["price_basis"] == expected_basis
        assert facts["scope"] == {
            "market_scope": "incomplete",
            "financial_scope": "unavailable",
            "news_scope": "unavailable",
            "industry_scope": "manual_verification_required",
        }
        assert facts["vendor_pending"] == VENDOR_PENDING
        assert facts["trading"] is False
        assert facts["generation"]["new_tickflow_api_request_count"] == 0

        numeric_paths = set(facts["numeric_provenance"])
        assert "/facts_schema_version" in numeric_paths
        assert "/source_evidence/observation_days" in numeric_paths
        assert "/minute_1m/bar_count" in numeric_paths
        assert "/minute_30m/bar_count" in numeric_paths
        assert "/generation/new_tickflow_api_request_count" in numeric_paths
        for provenance in facts["numeric_provenance"].values():
            assert provenance["calculation_function"] in {
                "identity",
                "schema_constant",
            }
            assert "price_basis" in provenance
            if provenance["calculation_function"] == "identity":
                assert provenance["source_file"]
                assert provenance["source_json_path"]
                assert provenance["source_sha256"]
            else:
                assert provenance["constant"]


def test_validate_facts_accepts_the_builder_output() -> None:
    module = importlib.import_module("app.services.phase2_facts")

    for symbol in SYMBOLS:
        facts = module.build_symbol_facts(REPO_ROOT, symbol)
        assert module.validate_facts_document(REPO_ROOT, facts) == []


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        (
            lambda facts: facts["source_evidence"]["source_files"][0].__setitem__(
                "sha256", "0" * 64
            ),
            "source_hash_mismatch",
        ),
        (
            lambda facts: facts["numeric_provenance"].pop("/minute_1m/bar_count"),
            "numeric_provenance_missing:/minute_1m/bar_count",
        ),
        (
            lambda facts: facts["numeric_provenance"]["/minute_1m/bar_count"].pop(
                "calculation_function"
            ),
            "numeric_provenance_calculation_invalid:/minute_1m/bar_count",
        ),
        (
            lambda facts: facts["minute_1m"].__setitem__("bar_count", 240),
            "numeric_provenance_value_mismatch:/minute_1m/bar_count",
        ),
        (
            lambda facts: facts["daily"].__setitem__("close", 10.25),
            "daily_value_without_retained_source:/daily/close",
        ),
        (
            lambda facts: facts["indicators"]["ma5"].__setitem__("value", 10.2),
            "indicator_value_without_retained_source:ma5",
        ),
        (
            lambda facts: facts["minute_1m"].__setitem__("bar_count", math.nan),
            "non_finite_number:/minute_1m/bar_count",
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
                    "indicator_prices": "none",
                    "indicator_volume": "qfq",
                    "minute": "none",
                },
            ),
            "raw_qfq_basis_invalid",
        ),
        (
            lambda facts: facts["vendor_pending"].remove("amount_unit"),
            "vendor_pending_invalid",
        ),
        (
            lambda facts: facts["scope"].__setitem__("financial_scope", "available"),
            "scope_invalid",
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


def test_canonical_json_rejects_nan_and_is_byte_stable() -> None:
    module = importlib.import_module("app.services.phase2_facts")
    facts = module.build_symbol_facts(REPO_ROOT, "000403.SZ")

    assert module.canonical_json_bytes(facts) == module.canonical_json_bytes(facts)
    with pytest.raises(ValueError):
        module.canonical_json_bytes({"invalid": math.inf})


def _copy_phase1_sources(destination: Path) -> None:
    module = importlib.import_module("app.services.phase2_facts")
    seen: set[str] = set()
    for symbol in SYMBOLS:
        facts = module.build_symbol_facts(REPO_ROOT, symbol)
        for item in facts["source_evidence"]["source_files"]:
            relative = item["path"]
            if relative in seen:
                continue
            seen.add(relative)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO_ROOT / relative, target)


def _directory_bytes(path: Path) -> dict[str, bytes]:
    return {
        item.relative_to(path).as_posix(): item.read_bytes()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def test_build_facts_directory_is_idempotent_and_records_zero_requests(tmp_path: Path) -> None:
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
    assert first["ai_calls"] == 0
    assert first["content_readiness"] == "BLOCKED_SOURCE_EVIDENCE"
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
    _copy_phase1_sources(fixture_root)
    output_dir = fixture_root / "reports" / "phase2_facts"
    builder.build_facts_directory(fixture_root, output_dir)
    before = _directory_bytes(output_dir)

    summary_path = fixture_root / "reports/phase1_observation/2026-07-31/daily_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
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
    facts = json.loads(path.read_text(encoding="utf-8"))
    facts["minute_1m"]["bar_count"] = 240
    path.write_text(json.dumps(facts, ensure_ascii=False), encoding="utf-8")

    result = validator.validate_facts_directory(REPO_ROOT, output_dir)

    assert result["status"] == "FACTS_INVALID"
    assert result["symbols"]["000403.SZ"] == "INVALID"
    assert "numeric_provenance_value_mismatch:/minute_1m/bar_count" in result["errors"][
        "000403.SZ"
    ]


def test_builder_rejects_symbols_outside_the_fixed_scope() -> None:
    module = importlib.import_module("app.services.phase2_facts")
    with pytest.raises(module.Phase2FactsError, match="outside the fixed"):
        module.build_symbol_facts(REPO_ROOT, "600519.SH")


def test_builder_rejects_symlinked_source_evidence(tmp_path: Path) -> None:
    module = importlib.import_module("app.services.phase2_facts")
    fixture_root = tmp_path / "repo"
    _copy_phase1_sources(fixture_root)
    source = fixture_root / "reports/phase1_observation/observation_index.json"
    outside = tmp_path / "outside.json"
    shutil.copy2(source, outside)
    source.unlink()
    source.symlink_to(outside)

    with pytest.raises(module.Phase2FactsError, match="symlink"):
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
    forbidden_imports = {"httpx", "openai", "requests", "tickflow"}
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
