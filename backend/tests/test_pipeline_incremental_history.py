"""Incremental indicator history loading regression tests."""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from app.indicators import pipeline


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
