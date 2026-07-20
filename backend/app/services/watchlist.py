"""自选股服务(§6.1)。

存储:`data/user_data/watchlist.parquet`,字段 symbol + added_at + note。
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

import polars as pl

from app.config import settings
from app.tickflow.capabilities import Cap, CapabilitySet
from app.tickflow.client import get_client
from app.tickflow.rate_limits import chunked, resolve_limit
from app.workspace.locks import resource_lock
from app.workspace.models import ResourceName

logger = logging.getLogger(__name__)

_SCHEMA = {"symbol": pl.Utf8, "added_at": pl.Utf8, "note": pl.Utf8}


def _lock() -> RLock:
    return resource_lock(ResourceName.WATCHLIST)


def _path() -> Path:
    p = settings.data_dir / "user_data" / "watchlist.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def list_symbols() -> list[dict]:
    with _lock():
        p = _path()
        if not p.exists():
            return []
        df = pl.read_parquet(p)
        if df.is_empty():
            return []
        return df.to_dicts()


def _empty_frame() -> pl.DataFrame:
    return pl.DataFrame(schema=_SCHEMA)


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(df: pl.DataFrame) -> None:
    """Durably replace the watchlist without exposing a partial parquet file."""
    path = _path()
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
        df.write_parquet(temp_path)
        with temp_path.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
        _fsync_directory(path.parent)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def add(symbol: str, note: str = "") -> list[dict]:
    with _lock():
        path = _path()
        if path.exists():
            df = pl.read_parquet(path)
            # 已存在则先移除，后面重新插入到最前面
            if symbol in df["symbol"].to_list():
                df = df.filter(pl.col("symbol") != symbol)
        else:
            df = _empty_frame()

        new_row = pl.DataFrame({
            "symbol": [symbol],
            "added_at": [
                datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
            ],
            "note": [note],
        })
        out = pl.concat([new_row, df], how="diagonal_relaxed")
        _atomic_write(out)
        return out.to_dicts()


def remove(symbol: str) -> list[dict]:
    with _lock():
        path = _path()
        if not path.exists():
            return []
        df = pl.read_parquet(path).filter(pl.col("symbol") != symbol)
        _atomic_write(df)
        return df.to_dicts()


def move_to_top(symbol: str) -> list[dict]:
    with _lock():
        path = _path()
        if not path.exists():
            return []
        df = pl.read_parquet(path)
        if df.is_empty() or symbol not in df["symbol"].to_list():
            return df.to_dicts()
        target = df.filter(pl.col("symbol") == symbol)
        rest = df.filter(pl.col("symbol") != symbol)
        out = pl.concat([target, rest], how="diagonal_relaxed")
        _atomic_write(out)
        return out.to_dicts()


def clear() -> int:
    """清空自选列表。返回移除的数量。"""
    with _lock():
        path = _path()
        if not path.exists():
            return 0
        count = pl.read_parquet(path).height
        if count > 0:
            _atomic_write(_empty_frame())
        return count


def replace_all(rows: list[dict]) -> list[dict]:
    """Validate and atomically replace all watchlist rows in caller order."""
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("watchlist rows must be objects")
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            raise ValueError("watchlist symbol must not be empty")
        if symbol in seen:
            raise ValueError(f"duplicate watchlist symbol: {symbol}")
        seen.add(symbol)
        normalized.append({
            "symbol": symbol,
            "added_at": str(row.get("added_at") or now),
            "note": str(row.get("note") or ""),
        })

    frame = pl.DataFrame(normalized, schema=_SCHEMA) if normalized else _empty_frame()
    with _lock():
        _atomic_write(frame)
        return frame.to_dicts()


def fetch_quotes(symbols: list[str], capset: CapabilitySet, timeout_s: float = 8.0) -> list[dict]:
    """拉取实时行情。

    优先用 quote.batch;否则降级为 quote.by_symbol 单股请求。
    timeout_s: 单批次请求超时(秒)，防止 API 卡死阻塞整个请求。
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    if not symbols:
        return []

    tf = get_client()
    quotes: list[dict] = []

    # 走 batch
    if capset.has(Cap.QUOTE_BATCH):
        batch_size = resolve_limit(capset, Cap.QUOTE_BATCH, default_batch=50).batch
    elif capset.has(Cap.QUOTE_BY_SYMBOL):
        batch_size = resolve_limit(capset, Cap.QUOTE_BY_SYMBOL, default_batch=5).batch
    else:
        # 无任何实时行情能力(none/free 档走 free-api 服务器,不提供实时行情)
        # 提前返回空,避免发起注定失败的请求
        return []

    chunks = chunked(symbols, batch_size)

    # 用线程池为每个批次加超时保护
    pool = ThreadPoolExecutor(max_workers=1)
    for chunk in chunks:
        try:
            future = pool.submit(tf.quotes.get, symbols=chunk, as_dataframe=True)
            raw = future.result(timeout=timeout_s)
            if raw is None or len(raw) == 0:
                continue
            df = pl.from_pandas(raw)
            rename_map = {
                "last_price": "price",
                "ext.change_pct": "pct",
                "ext.name": "name",
            }
            df = df.rename({k: v for k, v in rename_map.items() if k in df.columns})
            quotes.extend(df.to_dicts())
        except FuturesTimeout:
            logger.warning("quote fetch timeout (%.1fs) for %d symbols", timeout_s, len(chunk))
            break  # 超时后不再尝试后续批次
        except Exception as e:  # noqa: BLE001
            logger.warning("quote fetch failed for %d symbols: %s", len(chunk), e)
    pool.shutdown(wait=False)

    return quotes
