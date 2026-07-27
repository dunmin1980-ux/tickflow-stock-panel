# ruff: noqa: RUF001

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

SYMBOLS = ("000403.SZ", "600489.SH", "300059.SZ")
COMPACT = {
    "000403.SZ": "000403SZ",
    "600489.SH": "600489SH",
    "300059.SZ": "300059SZ",
}
TRADE_DATE = "2026-07-27"


def _load_observer():
    script = Path(__file__).parents[2] / "scripts" / "validate_phase1_observation.py"
    spec = importlib.util.spec_from_file_location("phase1_observation", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _validation(*, amount_warning: bool = False) -> dict:
    return {
        "passed": True,
        "bar_count": 241,
        "latest_timestamp": f"{TRADE_DATE}T15:00:00+08:00",
        "checks": {
            "future_timestamp_rows": 0,
            "invalid_numeric_rows": 0,
            "invalid_ohlc_rows": 0,
            "lunch_break_rows": 0,
            "negative_amount_rows": 0,
            "negative_volume_rows": 0,
            "off_continuous_session_rows": 0,
            "ohlc_abs_epsilon": 1e-10,
            "amount_zero_epsilon": 1e-6,
            "timestamps_strictly_increasing": True,
            "timestamps_unique": True,
        },
        "ohlc_violation_distribution": {
            "contract_passed": True,
            "threshold_counts": {"gt_1e_10": 0},
            "row_violation_stats": {"count": 0, "max": None},
            "raw_values_modified": False,
        },
        "amount_tolerance": {
            "contract_passed": True,
            "failed_count": 0,
            "epsilon_warning_count": 1 if amount_warning else 0,
            "minimum_amount": -4.773028194904327e-09 if amount_warning else None,
            "raw_values_modified": False,
        },
    }


def _minute_symbol(symbol: str) -> dict:
    one = _validation(amount_warning=symbol == "300059.SZ")
    thirty = _validation()
    thirty["bar_count"] = 8
    field_matches = {
        "open": {"matched": 7 if symbol == "300059.SZ" else 8, "compared": 8},
        "high": {"matched": 8, "compared": 8},
        "low": {"matched": 8, "compared": 8},
        "close": {"matched": 8, "compared": 8},
        "volume": {"matched": 7, "compared": 8},
        "amount": {"matched": 7, "compared": 8},
    }
    candidate = {
        "minute_count": 31,
        "open_match": True,
        "high_match": True,
        "low_match": True,
        "close_match": True,
        "ohlc_match": True,
        "volume_match": True,
        "amount_match": True,
        "all_fields_match": True,
    }
    return {
        "symbol": symbol,
        "one_minute": {
            "passed": True,
            "freshness": {
                "passed": True,
                "same_trade_date": True,
                "time_fresh": True,
                "expected_trade_date": TRADE_DATE,
                "latest_timestamp": f"{TRADE_DATE}T15:00:00+08:00",
            },
            "validation": one,
        },
        "direct_30m": {
            "passed": True,
            "freshness": {
                "passed": True,
                "same_trade_date": True,
                "time_fresh": True,
                "expected_trade_date": TRADE_DATE,
                "latest_timestamp": f"{TRADE_DATE}T15:00:00+08:00",
            },
            "validation": thirty,
        },
        "local_1m_to_30m": {
            "passed": True,
            "ohlc_contract": "PASSED",
            "volume_amount_first_bucket": "VENDOR_CONVENTION_PENDING",
            "selected_comparison": {
                "pair_count": 8,
                "direct_bar_count": 8,
                "aggregated_bar_count": 8,
                "complete_bucket_count": 8,
                "fields": field_matches,
            },
            "dual_first_bucket": {
                "trade_date": TRADE_DATE,
                "verdict": "FIRST_BUCKET_MATCHES_WITH_09_30_INCLUDED",
                "vendor_contract_confirmed": False,
                "raw_values_modified": False,
                "timestamps_shifted": False,
                "symbols": {
                    symbol: {
                        "vendor_candidate_09_30_to_10_00": candidate,
                        "raw_values_modified": False,
                    }
                },
            },
        },
    }


def _daily_contract(symbol: str) -> dict:
    checks = {
        "expected_recent_trade_date": TRADE_DATE,
        "latest_date_not_behind_recent_trade_date": True,
        "latest_qfq_trade_date": TRADE_DATE,
        "latest_raw_trade_date": TRADE_DATE,
        "qfq_repeat_stable": True,
        "symbol_matches_request": True,
        "trade_dates_align_between_raw_and_qfq": True,
    }
    return {
        "symbol": symbol,
        "passed": True,
        "checks": checks,
        "raw": {"latest_timestamp": f"{TRADE_DATE}T00:00:00+08:00"},
        "qfq": {"latest_timestamp": f"{TRADE_DATE}T00:00:00+08:00"},
    }


def _factor_contract(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "passed": True,
        "factor_count": 14,
        "checks": {
            "factor_timestamps_valid": True,
            "factor_dates_unique": True,
            "factor_dates_sorted": True,
            "factor_values_positive": True,
            "repeat_result_stable": True,
            "qfq_repeat_result_stable": True,
            "qfq_relation_passed": True,
            "qfq_relation": {"all_price_fields_match_rate": 1.0},
            "no_second_adjustment_evidence": "stable",
        },
    }


def _valid_phase11_repo(root: Path) -> Path:
    reports = root / "reports"
    data_contracts = reports / "phase1_data_contracts"
    numeric_contracts = reports / "phase1_numeric_contracts"
    reports.mkdir(parents=True)
    data_contracts.mkdir()
    numeric_contracts.mkdir()
    (reports / "tickflow_phase1_numeric_contract_closeout.md").write_text(
        "# Phase 1.1\nPHASE1_READY_FOR_OBSERVATION_WITH_VENDOR_PENDING\n",
        encoding="utf-8",
    )

    minute = {
        "contract": "tickflow_phase1_1_minute_observation_v1",
        "passed": True,
        "timezone": "Asia/Shanghai",
        "intraday_batch_dependency": False,
        "intraday_batch_status": "ENTITLEMENT_PENDING",
        "symbols": {symbol: _minute_symbol(symbol) for symbol in SYMBOLS},
        "summary_by_symbol": {
            symbol: {
                "one_minute_passed": True,
                "direct_30m_passed": True,
                "aggregation_ohlc_passed": True,
                "first_bucket_candidate_passed": True,
                "vendor_convention_pending": True,
            }
            for symbol in SYMBOLS
        },
    }
    _write_json(data_contracts / "minute_to_30m_validation.json", minute)
    for symbol in SYMBOLS:
        compact = COMPACT[symbol]
        _write_json(
            data_contracts / f"{compact}_daily_contract.json",
            _daily_contract(symbol),
        )
        _write_json(
            data_contracts / f"{compact}_ex_factors_contract.json",
            _factor_contract(symbol),
        )

    _write_json(
        numeric_contracts / "verification_summary.json",
        {
            "final_status": "PHASE1_READY_FOR_OBSERVATION_WITH_VENDOR_PENDING",
            "verification": {
                "report_sensitive_shape_scan": {
                    "status": "PASSED",
                    "credential_shape_hits": 0,
                }
            },
            "cloud_read_only_recheck": {
                "redeployed": False,
                "integrated_gold_enabled": False,
                "integrated_gold_external_send_count": 0,
            },
            "boundaries": {
                "raw_market_values_modified": False,
                "timestamps_shifted": False,
                "missing_minutes_filled": False,
                "ai_configured": False,
                "paper_trading_started": False,
                "real_key_exposed": False,
            },
        },
    )
    _write_json(
        numeric_contracts / "minute_30m_dual_convention_validation.json",
        {
            "trade_date": TRADE_DATE,
            "verdict": "FIRST_BUCKET_MATCHES_WITH_09_30_INCLUDED",
            "candidate_match_symbol_count": 3,
            "symbol_count": 3,
            "vendor_contract_confirmed": False,
        },
    )
    _write_json(
        numeric_contracts / "000403SZ_ohlc_violation_distribution.json",
        {
            "one_minute": {
                "contract_passed": True,
                "threshold_counts": {"gt_1e_10": 0},
            },
            "direct_30m": {
                "contract_passed": True,
                "threshold_counts": {"gt_1e_10": 0},
            },
        },
    )
    _write_json(
        numeric_contracts / "300059SZ_amount_tolerance.json",
        {"one_minute": {"contract_passed": True, "failed_count": 0}},
    )
    _write_json(
        numeric_contracts / "000403SZ_ex_factor_final.json",
        {
            "EX_FACTOR_CONTRACT": "PASSED",
            "checks": {
                "qfq_relation_passed": True,
                "qfq_relation": {"all_price_fields_match_rate": 1.0},
            },
        },
    )

    records = [
        {
            "pipeline": "phase1_1_minute_contract_closeout",
            "capability": f"contract.{index}",
            "symbol_count": 3,
            "duration_ms": index + 1,
            "status": "PASSED",
            "retry_count": 0,
            "rate_limit_wait_ms": 0,
        }
        for index in range(14)
    ]
    _write_json(
        reports / "phase1_tickflow_request_audit.json",
        {
            "final_status": "PHASE1_READY_FOR_OBSERVATION_WITH_VENDOR_PENDING",
            "phase1_1_request_count": 14,
            "http_429_detected": False,
            "retry_count": 0,
            "cloud_redeployed": False,
            "integrated_gold_enabled": False,
            "integrated_gold_external_send_count": 0,
            "real_key_exposed": False,
            "credential_values_recorded": False,
            "gates": {
                "passed": True,
                "recent_log_credential_shape_scan_passed": True,
                "credential_values_recorded": False,
            },
            "phase1_1": {
                "pipeline": "phase1_1_minute_contract_closeout",
                "request_count": 14,
                "http_429_detected": False,
                "credential_values_recorded": False,
                "records": records,
            },
        },
    )
    return root


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _replace_date(value, old: str, new: str):
    if isinstance(value, dict):
        return {key: _replace_date(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_date(item, old, new) for item in value]
    if isinstance(value, str):
        return value.replace(old, new)
    return value


def _valid_live_result(repo: Path, observation_date: str) -> dict:
    reports = repo / "reports"
    audit = _read(reports / "phase1_tickflow_request_audit.json")
    daily = {}
    factors = {}
    for symbol in SYMBOLS:
        compact = COMPACT[symbol]
        daily[symbol] = _read(
            reports / "phase1_data_contracts" / f"{compact}_daily_contract.json"
        )
        factors[symbol] = _read(
            reports
            / "phase1_data_contracts"
            / f"{compact}_ex_factors_contract.json"
        )
    minute = _read(
        reports / "phase1_data_contracts" / "minute_to_30m_validation.json"
    )
    result = {
        "final_status": "PHASE1_READY_FOR_OBSERVATION_WITH_VENDOR_PENDING",
        "generated_at": f"{observation_date}T16:30:00+08:00",
        "security_gate": {
            "passed": True,
            "authentication_configured": True,
            "current_https_session_valid": True,
            "has_tickflow_key": True,
            "masked_tickflow_key_present": True,
            "has_ai_key": False,
            "recent_log_credential_shape_scan_passed": True,
            "credential_values_recorded": False,
            "integrated_gold_enabled": False,
            "integrated_gold_external_send_count": 0,
        },
        "contracts": {
            "daily": daily,
            "ex_factors": factors,
            "minute_to_30m": minute,
        },
        "request_audit": audit["phase1_1"]["records"],
        "tickflow_request_count": 14,
        "http_429_detected": False,
        "boundaries": {
            "cloud_redeployed": False,
            "real_key_exposed": False,
            "raw_market_values_modified": False,
            "timestamps_shifted": False,
            "missing_minutes_filled": False,
            "ai_configured": False,
            "paper_trading_started": False,
            "integrated_gold_enabled": False,
            "integrated_gold_external_send_count": 0,
        },
    }
    return _replace_date(result, TRADE_DATE, observation_date)


def _rewrite(path: Path, mutate) -> None:
    value = _read(path)
    mutate(value)
    _write_json(path, value)


def test_build_reused_day_validates_day1_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer = _load_observer()
    repo = _valid_phase11_repo(tmp_path)

    def fail_network(*_args, **_kwargs):
        raise AssertionError("offline reuse attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    day = observer.build_reused_day(repo, TRADE_DATE)

    assert day["status"] == "DAY_PASSED"
    assert day["observation_date"] == TRADE_DATE
    assert day["observation_day"] == 1
    assert day["evidence_origin"] == "phase1_1_reuse"
    assert day["live_request_reexecuted"] is False
    assert day["source_request_count"] == 14
    assert day["new_api_request_count"] == 0
    assert day["source_data_hashes_verified"] is True
    assert day["source_report"] == (
        "reports/tickflow_phase1_numeric_contract_closeout.md"
    )
    assert day["source_request_audit"] == (
        "reports/phase1_tickflow_request_audit.json"
    )
    assert day["source_evidence_dir"] == "reports/phase1_numeric_contracts/"
    assert day["vendor_pending"] == [
        "intraday_batch_entitlement",
        "first_30m_bucket_includes_09_30",
        "volume_unit",
        "amount_unit",
    ]
    assert day["metrics"]["material_ohlc_anomalies"] == 0
    assert day["metrics"]["material_negative_amount"] == 0
    assert day["metrics"]["thirty_minute_ohlc_mismatches"] == 0
    assert day["metrics"]["non_first_bucket_volume_amount_mismatches"] == 0
    assert day["metrics"]["first_bucket_matches"] == 3
    assert all(
        {
            "pipeline",
            "capability",
            "symbol_count",
            "duration_ms",
            "status",
            "retry_count",
            "rate_limit_wait_ms",
        }
        <= set(record)
        for record in day["request_audit"]["records"]
    )


@pytest.mark.parametrize(
    ("filename", "mutate", "reason"),
    [
        (
            "000403SZ_daily_contract.json",
            lambda value: value["checks"].update(latest_raw_trade_date="2026-07-24"),
            "DAILY_STALE",
        ),
        (
            "minute_to_30m_validation.json",
            lambda value: value["symbols"]["000403.SZ"]["one_minute"][
                "freshness"
            ].update(latest_timestamp=f"{TRADE_DATE}T14:59:00+08:00"),
            "ONE_MINUTE_STALE",
        ),
        (
            "minute_to_30m_validation.json",
            lambda value: value["symbols"]["000403.SZ"]["one_minute"][
                "validation"
            ]["ohlc_violation_distribution"]["threshold_counts"].update(
                gt_1e_10=1
            ),
            "MATERIAL_OHLC_ANOMALY",
        ),
        (
            "minute_to_30m_validation.json",
            lambda value: value["symbols"]["300059.SZ"]["one_minute"][
                "validation"
            ]["amount_tolerance"].update(failed_count=1, minimum_amount=-1e-5),
            "MATERIAL_NEGATIVE_AMOUNT",
        ),
        (
            "minute_to_30m_validation.json",
            lambda value: value["symbols"]["600489.SH"]["one_minute"][
                "validation"
            ]["checks"].update(timestamps_unique=False),
            "DUPLICATE_TIMESTAMP",
        ),
        (
            "minute_to_30m_validation.json",
            lambda value: value["symbols"]["600489.SH"]["one_minute"][
                "validation"
            ]["checks"].update(lunch_break_rows=1),
            "LUNCH_BREAK_BAR",
        ),
        (
            "minute_to_30m_validation.json",
            lambda value: value["symbols"]["000403.SZ"]["local_1m_to_30m"][
                "dual_first_bucket"
            ]["symbols"]["000403.SZ"]["vendor_candidate_09_30_to_10_00"].update(
                all_fields_match=False,
                volume_match=False,
            ),
            "FIRST_BUCKET_UNEXPLAINED",
        ),
    ],
)
def test_build_reused_day_rejects_bad_market_contract(
    tmp_path: Path,
    filename: str,
    mutate,
    reason: str,
) -> None:
    observer = _load_observer()
    repo = _valid_phase11_repo(tmp_path)
    path = repo / "reports" / "phase1_data_contracts" / filename
    _rewrite(path, mutate)

    with pytest.raises(observer.EvidenceError, match=reason):
        observer.build_reused_day(repo, TRADE_DATE)


@pytest.mark.parametrize(
    ("target", "mutate", "reason"),
    [
        (
            "factor",
            lambda value: value.update(passed=False),
            "EX_FACTOR_CONTRACT_FAILED",
        ),
        (
            "audit",
            lambda value: value.update(http_429_detected=True),
            "HTTP_429_DETECTED",
        ),
        (
            "verification",
            lambda value: value["verification"][
                "report_sensitive_shape_scan"
            ].update(status="FAILED", credential_shape_hits=1),
            "SENSITIVE_SHAPE_DETECTED",
        ),
    ],
)
def test_build_reused_day_rejects_factor_rate_limit_or_secret_failure(
    tmp_path: Path,
    target: str,
    mutate,
    reason: str,
) -> None:
    observer = _load_observer()
    repo = _valid_phase11_repo(tmp_path)
    paths = {
        "factor": (
            repo
            / "reports"
            / "phase1_data_contracts"
            / "000403SZ_ex_factors_contract.json"
        ),
        "audit": repo / "reports" / "phase1_tickflow_request_audit.json",
        "verification": (
            repo
            / "reports"
            / "phase1_numeric_contracts"
            / "verification_summary.json"
        ),
    }
    _rewrite(paths[target], mutate)

    with pytest.raises(observer.EvidenceError, match=reason):
        observer.build_reused_day(repo, TRADE_DATE)


def test_materialize_day_writes_six_files_without_touching_sources(
    tmp_path: Path,
) -> None:
    observer = _load_observer()
    repo = _valid_phase11_repo(tmp_path)
    before = observer.hash_evidence(observer.source_evidence_paths(repo), repo)
    day = observer.build_reused_day(repo, TRADE_DATE)

    result = observer.materialize_day(day, repo / "reports")
    output = repo / "reports" / "phase1_observation" / TRADE_DATE

    assert sorted(path.name for path in output.iterdir()) == [
        "000403SZ_contract.json",
        "300059SZ_contract.json",
        "600489SH_contract.json",
        "daily_summary.json",
        "minute_30m_comparison.json",
        "request_audit.json",
    ]
    assert observer.hash_evidence(observer.source_evidence_paths(repo), repo) == before
    assert result["status"] == "DAY_PASSED"
    summary = _read(output / "daily_summary.json")
    assert summary["source_evidence"] == day["source_evidence"]
    assert "raw_rows" not in json.dumps(summary)


def test_materialize_day_is_idempotent_and_refuses_conflicting_hashes(
    tmp_path: Path,
) -> None:
    observer = _load_observer()
    repo = _valid_phase11_repo(tmp_path)
    day = observer.build_reused_day(repo, TRADE_DATE)

    first = observer.materialize_day(day, repo / "reports")
    second = observer.materialize_day(day, repo / "reports")
    assert second == first

    changed = copy.deepcopy(day)
    changed["source_evidence"][0]["sha256"] = "0" * 64
    with pytest.raises(
        observer.EvidenceConflictError,
        match="OBSERVATION_EVIDENCE_CONFLICT",
    ):
        observer.materialize_day(changed, repo / "reports")


def _write_index_day(
    reports: Path,
    observation_date: str,
    status: str,
    *,
    day_number: int | None = None,
) -> None:
    day_dir = reports / "phase1_observation" / observation_date
    source_path = reports / "phase1-source-evidence.txt"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("immutable phase1 source\n", encoding="utf-8")
    source_bytes = source_path.read_bytes()
    source_evidence = [
        {
            "path": "reports/phase1-source-evidence.txt",
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
            "size": len(source_bytes),
        }
    ]
    source_digest = hashlib.sha256(
        json.dumps(
            source_evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    _write_json(
        day_dir / "daily_summary.json",
        {
            "observation_date": observation_date,
            "observation_day": day_number,
            "status": status,
            "idempotency_key": (
                "phase1_observation:"
                f"{observation_date}:"
                "f5f48819c7db5ecac1e09e7a0ec634a09aa5675480a9bd0f6d2cc9eea8bf8dd8:"
                "tickflow_phase1_2_observation_v1"
            ),
            "evidence_origin": (
                "phase1_1_reuse" if observation_date == TRADE_DATE else "phase1_2_live"
            ),
            "live_request_reexecuted": observation_date != TRADE_DATE,
            "source_request_count": 14,
            "new_api_request_count": (
                0 if observation_date == TRADE_DATE else 14
            ),
            "source_data_hashes_verified": True,
            "source_evidence_digest": source_digest,
            "source_evidence": source_evidence,
            "vendor_pending": [
                "intraday_batch_entitlement",
                "first_30m_bucket_includes_09_30",
                "volume_unit",
                "amount_unit",
            ],
            "symbols": {symbol: status for symbol in SYMBOLS},
            "metrics": {
                "stale_data": 0,
                "material_ohlc_anomalies": 0,
                "material_negative_amount": 0,
                "duplicate_timestamps": 0,
                "lunch_break_bars": 0,
                "thirty_minute_ohlc_mismatches": 0,
                "non_first_bucket_volume_amount_mismatches": 0,
                "first_bucket_matches": 3 if status == "DAY_PASSED" else 0,
                "first_bucket_checks": 3 if status == "DAY_PASSED" else 0,
                "ex_factor_passes": 3 if status == "DAY_PASSED" else 0,
                "ex_factor_checks": 3 if status == "DAY_PASSED" else 0,
                "http_429": 0,
                "sensitive_shape_hits": 0,
            },
            "boundaries": {
                "ai": "NOT_CONFIGURED",
                "paper_trading": "NOT_STARTED",
                "cloud_redeployed": False,
                "integrated_gold_enabled": False,
                "integrated_gold_external_send_count": 0,
            },
        },
    )


def test_index_requires_five_real_passed_days(tmp_path: Path) -> None:
    observer = _load_observer()
    reports = tmp_path / "reports"
    for index in range(4):
        _write_index_day(
            reports,
            f"2026-07-{27 + index:02d}",
            "DAY_PASSED",
            day_number=index + 1,
        )

    index = observer.rebuild_index(reports)
    assert index["final_status"] == "PHASE1_OBSERVATION_IN_PROGRESS"
    assert index["valid_trading_days"] == 4
    assert (reports / "tickflow_phase1_observation_status.md").is_file()
    assert not (reports / "tickflow_phase1_observation_final.md").exists()

    _write_index_day(
        reports,
        "2026-07-31",
        "DAY_PASSED",
        day_number=5,
    )
    index = observer.rebuild_index(reports)
    assert index["final_status"] == "PHASE1_OBSERVATION_PASSED"
    assert index["valid_trading_days"] == 5
    assert index["metrics"]["first_bucket_stable_rate"] == 1.0
    assert index["metrics"]["ex_factor_pass_rate"] == 1.0
    assert not (reports / "tickflow_phase1_observation_status.md").exists()
    assert (reports / "tickflow_phase1_observation_final.md").is_file()


def test_non_trading_day_is_not_counted_and_blocked_day_stops_observation(
    tmp_path: Path,
) -> None:
    observer = _load_observer()
    reports = tmp_path / "reports"
    _write_index_day(reports, "2026-07-28", "NON_TRADING_DAY")
    index = observer.rebuild_index(reports)
    assert index["valid_trading_days"] == 0
    assert index["final_status"] == "PHASE1_OBSERVATION_IN_PROGRESS"

    _write_index_day(reports, "2026-07-29", "DAY_BLOCKED")
    index = observer.rebuild_index(reports)
    assert index["valid_trading_days"] == 0
    assert index["final_status"] == "PHASE1_OBSERVATION_BLOCKED"


def test_render_observation_report_carries_required_boundaries(
    tmp_path: Path,
) -> None:
    observer = _load_observer()
    reports = tmp_path / "reports"
    _write_index_day(reports, TRADE_DATE, "DAY_PASSED", day_number=1)
    index = observer.rebuild_index(reports)

    markdown = observer.render_observation_report(index)

    assert "PHASE1_OBSERVATION_IN_PROGRESS" in markdown
    assert "有效交易日：1/5，剩余 4 日" in markdown
    assert "AI：NOT_CONFIGURED" in markdown
    assert "Paper Trading：NOT_STARTED" in markdown
    assert "云端重新部署：NO" in markdown
    assert "Integrated Gold：DISABLED / external_send_count=0" in markdown
    assert "phase1_1_reuse" in markdown
    assert "新增 API 请求：0" in markdown
    assert index["days"][0]["source_request_count"] == 14
    assert index["days"][0]["source_data_hashes_verified"] is True
    assert index["reused_observation_days"] == 1
    assert index["phase1_2_live_observation_days"] == 0
    assert index["valid_observation_days"] == 1
    assert index["remaining_observation_days"] == 4
    assert len(index["index_hash_before"]) == 64
    assert len(index["index_hash_after"]) == 64


def test_reuse_cli_materializes_day1_and_reports_in_progress(
    tmp_path: Path,
) -> None:
    repo = _valid_phase11_repo(tmp_path)
    script = Path(__file__).parents[2] / "scripts" / "validate_phase1_observation.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--reuse-phase11-day1",
            "--repo-root",
            str(repo),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["day_status"] == "DAY_PASSED"
    assert result["final_status"] == "PHASE1_OBSERVATION_IN_PROGRESS"
    assert result["valid_trading_days"] == 1
    assert result["new_api_request_count"] == 0


def test_build_live_day_validates_sanitized_result(tmp_path: Path) -> None:
    observer = _load_observer()
    repo = _valid_phase11_repo(tmp_path)
    observation_date = "2026-07-28"
    result_path = repo / "phase1-live.json"
    _write_json(result_path, _valid_live_result(repo, observation_date))

    day = observer.build_live_day(result_path, repo, observation_date)

    assert day["status"] == "DAY_PASSED"
    assert day["evidence_origin"] == "phase1_2_live"
    assert day["live_request_reexecuted"] is True
    assert day["source_request_count"] == 14
    assert day["new_api_request_count"] == 14
    assert day["source_evidence"][0]["sha256"] == observer.sha256_file(result_path)
    assert day["source_evidence"][0]["retained_after_materialization"] is False


def test_build_live_day_records_first_429_as_blocked(tmp_path: Path) -> None:
    observer = _load_observer()
    repo = _valid_phase11_repo(tmp_path)
    observation_date = "2026-07-28"
    result = _valid_live_result(repo, observation_date)
    result["final_status"] = "PHASE1_RATE_LIMIT_BLOCKED"
    result["http_429_detected"] = True
    result["request_audit"] = result["request_audit"][:5]
    result["request_audit"][-1] = {
        **result["request_audit"][-1],
        "status": "FAILED",
        "http_status": 429,
    }
    result["tickflow_request_count"] = 5
    result_path = repo / "phase1-live-429.json"
    _write_json(result_path, result)

    day = observer.build_live_day(result_path, repo, observation_date)

    assert day["status"] == "DAY_BLOCKED"
    assert day["metrics"]["http_429"] == 1
    assert day["failure_reasons"] == ["HTTP_429_DETECTED"]
    assert day["request_audit"]["retry_count"] == 0


def test_build_live_day_rejects_nested_sensitive_value(tmp_path: Path) -> None:
    observer = _load_observer()
    repo = _valid_phase11_repo(tmp_path)
    observation_date = "2026-07-28"
    result = _valid_live_result(repo, observation_date)
    result["api_key"] = {"masked": False, "value": "must-not-survive"}
    result_path = repo / "phase1-live-sensitive.json"
    _write_json(result_path, result)

    day = observer.build_live_day(result_path, repo, observation_date)

    assert day["status"] == "DAY_BLOCKED"
    assert day["failure_reasons"] == ["SENSITIVE_SHAPE_DETECTED"]
    assert "must-not-survive" not in json.dumps(day)


def test_build_live_day_records_stale_contract_as_blocked(tmp_path: Path) -> None:
    observer = _load_observer()
    repo = _valid_phase11_repo(tmp_path)
    observation_date = "2026-07-28"
    result = _valid_live_result(repo, observation_date)
    result["contracts"]["daily"]["000403.SZ"]["checks"][
        "latest_raw_trade_date"
    ] = "2026-07-27"
    result_path = repo / "phase1-live-stale.json"
    _write_json(result_path, result)

    day = observer.build_live_day(result_path, repo, observation_date)

    assert day["status"] == "DAY_BLOCKED"
    assert day["failure_reasons"] == ["DAILY_STALE"]
    assert day["metrics"]["stale_data"] == 1
    assert day["symbol_contracts"]["000403.SZ"]["status"] == "BLOCKED"

def test_materialize_live_cli_rejects_invalid_json_without_output(
    tmp_path: Path,
) -> None:
    repo = _valid_phase11_repo(tmp_path)
    observer = _load_observer()
    observer.materialize_day(
        observer.build_reused_day(repo, TRADE_DATE),
        repo / "reports",
    )
    result_path = repo / "invalid-live.json"
    result_path.write_text("{not-json", encoding="utf-8")
    script = Path(__file__).parents[2] / "scripts" / "validate_phase1_observation.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--materialize-live",
            str(result_path),
            "--observation-date",
            "2026-07-28",
            "--now",
            "2026-07-28T16:30:00+08:00",
            "--symbol-set-hash",
            "f5f48819c7db5ecac1e09e7a0ec634a09aa5675480a9bd0f6d2cc9eea8bf8dd8",
            "--repo-root",
            str(repo),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "SOURCE_JSON_INVALID" in completed.stderr
    assert not (
        repo / "reports" / "phase1_observation" / "2026-07-28"
    ).exists()


@pytest.mark.parametrize(
    ("observation_date", "now", "reason"),
    [
        ("2026-07-28", "2026-07-28T16:09:00+08:00", "BEFORE_SAFE_WINDOW"),
        ("2026-07-29", "2026-07-28T16:30:00+08:00", "FUTURE_DATE_FORBIDDEN"),
        ("2026-07-27", "2026-07-28T16:30:00+08:00", "HISTORICAL_LIVE_FORBIDDEN"),
        ("2027-01-04", "2027-01-04T16:30:00+08:00", "CALENDAR_YEAR_UNSUPPORTED"),
    ],
)
def test_live_run_gate_rejects_unsafe_dates_before_network(
    tmp_path: Path,
    observation_date: str,
    now: str,
    reason: str,
) -> None:
    observer = _load_observer()

    with pytest.raises(observer.EvidenceError, match=reason):
        observer.evaluate_live_run_gate(
            tmp_path / "reports",
            observation_date=observation_date,
            now_iso=now,
        )


def test_live_run_gate_allows_next_real_trade_date(tmp_path: Path) -> None:
    observer = _load_observer()
    reports = tmp_path / "reports"
    _write_index_day(reports, TRADE_DATE, "DAY_PASSED", day_number=1)

    decision = observer.evaluate_live_run_gate(
        reports,
        observation_date="2026-07-28",
        now_iso="2026-07-28T16:30:00+08:00",
    )

    assert decision["decision"] == "LIVE_ALLOWED"
    assert decision["observation_date"] == "2026-07-28"
    assert decision["symbol_set_hash"] == (
        "f5f48819c7db5ecac1e09e7a0ec634a09aa5675480a9bd0f6d2cc9eea8bf8dd8"
    )
    assert decision["idempotency_key"].startswith(
        "phase1_observation:2026-07-28:"
    )


def test_live_run_gate_classifies_exchange_holiday_without_network(
    tmp_path: Path,
) -> None:
    observer = _load_observer()

    weekend = observer.evaluate_live_run_gate(
        tmp_path / "reports",
        observation_date="2026-08-02",
        now_iso="2026-08-02T16:30:00+08:00",
    )
    official_holiday = observer.evaluate_live_run_gate(
        tmp_path / "reports",
        observation_date="2026-10-01",
        now_iso="2026-10-01T16:30:00+08:00",
    )

    assert weekend["decision"] == "NON_TRADING_DAY"
    assert official_holiday["decision"] == "NON_TRADING_DAY"
    assert official_holiday["calendar_source"] == "SSE_2026_OFFICIAL_CLOSURES"


def test_live_run_gate_rejects_existing_pass_or_successful_audit(
    tmp_path: Path,
) -> None:
    observer = _load_observer()
    reports = tmp_path / "reports"
    _write_index_day(reports, "2026-07-28", "DAY_PASSED", day_number=2)

    with pytest.raises(observer.EvidenceError, match="OBSERVATION_DATE_ALREADY_PASSED"):
        observer.evaluate_live_run_gate(
            reports,
            observation_date="2026-07-28",
            now_iso="2026-07-28T16:30:00+08:00",
        )

    day_dir = reports / "phase1_observation" / "2026-07-28"
    (day_dir / "daily_summary.json").unlink()
    _write_json(
        day_dir / "request_audit.json",
        {
            "pipeline": "phase1_1_minute_contract_closeout",
            "records": [{"status": "PASSED"}],
        },
    )
    with pytest.raises(
        observer.EvidenceError,
        match="OBSERVATION_REQUEST_ALREADY_RECORDED",
    ):
        observer.evaluate_live_run_gate(
            reports,
            observation_date="2026-07-28",
            now_iso="2026-07-28T16:30:00+08:00",
        )


def test_non_trading_day_is_materialized_without_requests(tmp_path: Path) -> None:
    observer = _load_observer()
    reports = tmp_path / "reports"
    day = observer.build_non_trading_day("2026-08-02")

    observer.materialize_day(day, reports)
    index = observer.rebuild_index(reports)

    assert day["status"] == "NON_TRADING_DAY"
    assert day["new_api_request_count"] == 0
    assert day["live_request_reexecuted"] is False
    assert index["valid_observation_days"] == 0
    assert index["remaining_observation_days"] == 5


def test_rebuild_index_rejects_noncontinuous_days_or_replaced_day1(
    tmp_path: Path,
) -> None:
    observer = _load_observer()
    reports = tmp_path / "reports"
    _write_index_day(reports, TRADE_DATE, "DAY_PASSED", day_number=1)
    _write_index_day(reports, "2026-07-28", "DAY_PASSED", day_number=3)

    with pytest.raises(observer.EvidenceError, match="OBSERVATION_DAY_SEQUENCE_INVALID"):
        observer.rebuild_index(reports)

    summary_path = (
        reports / "phase1_observation" / TRADE_DATE / "daily_summary.json"
    )
    summary = _read(summary_path)
    summary["observation_day"] = 1
    summary["evidence_origin"] = "phase1_2_live"
    _write_json(summary_path, summary)
    second_day = reports / "phase1_observation" / "2026-07-28"
    for path in second_day.iterdir():
        path.unlink()
    second_day.rmdir()

    with pytest.raises(observer.EvidenceError, match="DAY1_SOURCE_IMMUTABLE"):
        observer.rebuild_index(reports)


def test_atomic_index_failure_preserves_previous_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observer = _load_observer()
    reports = tmp_path / "reports"
    _write_index_day(reports, TRADE_DATE, "DAY_PASSED", day_number=1)
    observer.rebuild_index(reports)
    index_path = reports / "phase1_observation" / "observation_index.json"
    before = index_path.read_bytes()

    _write_index_day(reports, "2026-07-28", "DAY_PASSED", day_number=2)
    original_replace = observer.os.replace

    def fail_index_replace(source, target):
        if Path(target) == index_path:
            raise OSError("simulated index rename failure")
        return original_replace(source, target)

    monkeypatch.setattr(observer.os, "replace", fail_index_replace)

    with pytest.raises(OSError, match="simulated index rename failure"):
        observer.rebuild_index(reports)

    assert index_path.read_bytes() == before


def test_rebuild_index_stops_when_reused_source_hash_changes(
    tmp_path: Path,
) -> None:
    observer = _load_observer()
    reports = tmp_path / "reports"
    _write_index_day(reports, TRADE_DATE, "DAY_PASSED", day_number=1)
    observer.rebuild_index(reports)

    (reports / "phase1-source-evidence.txt").write_text(
        "mutated phase1 source\n",
        encoding="utf-8",
    )

    with pytest.raises(observer.EvidenceError, match="SOURCE_EVIDENCE_HASH_MISMATCH"):
        observer.rebuild_index(reports)
