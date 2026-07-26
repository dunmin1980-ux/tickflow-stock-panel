"""Incremental indicator history loading regression tests."""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from app.indicators import pipeline


def _write_incremental_fixture(data_dir, history_date: date, new_date: date) -> None:
    history_path = (
        data_dir / "kline_daily_enriched" / f"date={history_date.isoformat()}" / "part.parquet"
    )
    history_path.parent.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["000403.SZ"],
            "date": [history_date],
            "open": [50.0],
            "high": [50.0],
            "low": [50.0],
            "close": [50.0],
            "volume": [1_000_000.0],
            "amount": [50_000_000.0],
            "raw_close": [100.0],
            "raw_high": [100.0],
            "raw_low": [100.0],
            "turnover_rate": [1.0],
            "consecutive_limit_ups": [0],
            "consecutive_limit_downs": [0],
            "quote_ts": [0],
        },
        schema_overrides={
            "consecutive_limit_ups": pl.UInt32,
            "consecutive_limit_downs": pl.UInt32,
        },
    ).write_parquet(history_path)

    daily_path = data_dir / "kline_daily" / f"date={new_date.isoformat()}" / "part.parquet"
    daily_path.parent.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["000403.SZ"],
            "date": [new_date],
            "open": [60.0],
            "high": [60.0],
            "low": [60.0],
            "close": [60.0],
            "volume": [1_000_000.0],
            "amount": [60_000_000.0],
            "quote_ts": [0],
        }
    ).write_parquet(daily_path)

    factor_path = data_dir / "adj_factor" / "all.parquet"
    factor_path.parent.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["000403.SZ"],
            "trade_date": [new_date],
            "ex_factor": [2.0],
        }
    ).write_parquet(factor_path)


def test_load_recent_history_reads_existing_enriched_rows(tmp_path):
    trade_date = date.today() - timedelta(days=1)
    partition = tmp_path / f"date={trade_date.isoformat()}"
    partition.mkdir()
    pl.DataFrame(
        {
            "symbol": ["000403.SZ"],
            "date": [trade_date],
            "open": [20.0],
            "high": [21.0],
            "low": [19.5],
            "close": [20.5],
            "volume": [1_000_000.0],
            "amount": [20_500_000.0],
            "raw_close": [20.5],
            "raw_high": [21.0],
            "raw_low": [19.5],
        }
    ).write_parquet(partition / "part.parquet")

    history = pipeline._load_recent_history(tmp_path, ["000403.SZ"], days=60)

    assert history.height == 1
    assert history["symbol"].to_list() == ["000403.SZ"]
    assert history["close"].to_list() == [20.5]


def test_load_recent_history_does_not_hide_programming_errors(monkeypatch, tmp_path):
    def broken_scan(*args, **kwargs):
        raise NameError("programming defect")

    monkeypatch.setattr(pipeline, "scan_enriched_parquet", broken_scan)

    with pytest.raises(NameError, match="programming defect"):
        pipeline._load_recent_history(tmp_path, ["000403.SZ"], days=60)


def test_load_recent_history_does_not_hide_polars_query_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pipeline,
        "scan_enriched_parquet",
        lambda *_args, **_kwargs: pl.DataFrame({"date": [date.today()]}).lazy(),
    )

    with pytest.raises(pl.exceptions.ColumnNotFoundError):
        pipeline._load_recent_history(tmp_path, ["000403.SZ"], days=60)


def test_load_recent_history_treats_corrupt_parquet_as_missing(tmp_path):
    partition = tmp_path / f"date={date.today().isoformat()}"
    partition.mkdir()
    (partition / "part.parquet").write_bytes(b"not a parquet file")

    history = pipeline._load_recent_history(tmp_path, ["000403.SZ"], days=60)

    assert history.is_empty()


def test_incremental_pipeline_does_not_readjust_enriched_history(tmp_path, monkeypatch):
    history_date = date.today() - timedelta(days=1)
    new_date = date.today()
    _write_incremental_fixture(tmp_path, history_date, new_date)
    computed_inputs: list[pl.DataFrame] = []

    def capture_compute_all(df, **_kwargs):
        computed_inputs.append(df.clone())
        return df

    monkeypatch.setattr(pipeline, "compute_all", capture_compute_all)

    written = pipeline.run_pipeline(data_dir=tmp_path, new_dates_only=True)

    assert written == 1
    assert len(computed_inputs) == 1
    incremental_input = computed_inputs[0].sort("date")
    assert incremental_input["close"].to_list() == [50.0, 60.0]
    assert incremental_input["raw_close"].to_list() == [100.0, 60.0]
