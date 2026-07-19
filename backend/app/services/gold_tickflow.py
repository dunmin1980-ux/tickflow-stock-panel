"""Fixed-symbol TickFlow Pro gateway for the isolated Gold monitor."""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from app.market_time import cn_now
from app.services.gold_calendar import GoldCalendarAuthority, GoldTradingCalendar
from app.services.gold_errors import GoldDataError
from app.services.gold_shadow_store import GoldShadowStorageReadError, GoldShadowStore
from app.tickflow.capabilities import Cap, CapabilitySet
from app.tickflow.client import get_paid_realtime_client
from app.tickflow.rate_limits import resolve_limit, sleep_between_batches

GOLD_SYMBOL = "600489.SH"
_CACHE_SCHEMA_VERSION = 1


class GoldTickFlowGateway:
    def __init__(
        self,
        store: GoldShadowStore,
        capset_provider: Callable[[], CapabilitySet],
        client_factory: Callable[[], Any | None] = get_paid_realtime_client,
        clock: Callable[[], datetime] = cn_now,
        calendar_authority: GoldCalendarAuthority | None = None,
    ) -> None:
        self.store = store
        self.capset_provider = capset_provider
        self.client_factory = client_factory
        self.clock = clock
        self.calendar_authority = calendar_authority or GoldCalendarAuthority(store)

    def get_quote(self) -> dict[str, object]:
        client = self._paid_client(Cap.QUOTE_BATCH)
        try:
            self._pace(Cap.QUOTE_BATCH)
            rows = client.quotes.get(symbols=[GOLD_SYMBOL], as_dataframe=False) or []
        except GoldDataError:
            raise
        except Exception:
            raise GoldDataError("tickflow_request_failed") from None
        if (
            not isinstance(rows, list)
            or len(rows) != 1
            or not isinstance(rows[0], dict)
            or rows[0].get("symbol") != GOLD_SYMBOL
        ):
            raise GoldDataError("quote_contract_invalid")
        return {**rows[0], "quote_source": "tickflow"}

    def get_completed_closes(
        self,
        expected_date: date,
        *,
        calendar: GoldTradingCalendar | None = None,
    ) -> list[float]:
        calendar = calendar or self.calendar_authority.load(expected_date)
        expected_previous_date = calendar.previous_trading_day(expected_date)
        try:
            cached = self.store.read_daily_history()
        except (GoldShadowStorageReadError, OSError):
            raise GoldDataError("gold_storage_read_failed") from None
        if cached is not None:
            cached_date, rows = self._validated_cache(
                cached, expected_date, expected_previous_date
            )
            if cached_date == expected_date:
                return self._latest_closes(rows)

        client = self._paid_client(Cap.KLINE_DAILY_BATCH)
        try:
            self._pace(Cap.KLINE_DAILY_BATCH)
            raw = client.klines.batch(
                [GOLD_SYMBOL],
                period="1d",
                count=90,
                adjust="none",
                as_dataframe=False,
            )
        except GoldDataError:
            raise
        except Exception:
            raise GoldDataError("tickflow_request_failed") from None
        rows = self._normalize_history_response(
            raw, expected_date, expected_previous_date
        )
        closes = self._latest_closes(rows)
        try:
            self.store.write_daily_history(
                {
                    "schema_version": _CACHE_SCHEMA_VERSION,
                    "source": "tickflow",
                    "expected_market_date": expected_date.isoformat(),
                    "fetched_at": self.clock().isoformat(),
                    "sha256": self._rows_digest(rows),
                    "rows": rows,
                }
            )
        except (GoldShadowStorageReadError, OSError):
            raise GoldDataError("gold_storage_write_failed") from None
        return closes

    def _paid_client(self, cap: Cap) -> Any:
        try:
            capset = self.capset_provider()
            capability_available = capset is not None and capset.has(cap)
        except Exception:
            raise GoldDataError("tickflow_capability_unavailable") from None
        if not capability_available:
            raise GoldDataError("tickflow_capability_unavailable")
        try:
            client = self.client_factory()
        except Exception:
            raise GoldDataError("tickflow_paid_client_unavailable") from None
        if client is None:
            raise GoldDataError("tickflow_paid_client_unavailable")
        return client

    def _pace(self, cap: Cap) -> None:
        try:
            limit = resolve_limit(self.capset_provider(), cap)
        except Exception:
            raise GoldDataError("tickflow_capability_unavailable") from None
        try:
            sleep_between_batches(1, limit.rpm)
        except Exception:
            raise GoldDataError("tickflow_request_failed") from None

    def _validated_cache(
        self,
        payload: dict[str, Any],
        expected_date: date,
        expected_previous_date: date,
    ) -> tuple[date, list[dict[str, object]]]:
        if payload.get("source") != "tickflow":
            raise GoldDataError("daily_cache_invalid")
        if payload.get("schema_version") != _CACHE_SCHEMA_VERSION:
            raise GoldDataError("daily_cache_invalid")
        market_date = self._parse_date(payload.get("expected_market_date"))
        if market_date is None or not isinstance(payload.get("fetched_at"), str):
            raise GoldDataError("daily_cache_invalid")
        rows = self._normalize_rows(
            payload.get("rows"),
            expected_date,
            expected_previous_date,
            require_normalized_shape=True,
            reject_non_completed=True,
            error_code="daily_cache_invalid",
        )
        sha256 = payload.get("sha256")
        if not isinstance(sha256, str) or sha256 != self._rows_digest(rows):
            raise GoldDataError("daily_cache_invalid")
        return market_date, rows

    def _normalize_history_response(
        self,
        raw: Any,
        expected_date: date,
        expected_previous_date: date,
    ) -> list[dict[str, object]]:
        if not isinstance(raw, dict) or set(raw) != {GOLD_SYMBOL}:
            raise GoldDataError("history_contract_invalid")
        return self._normalize_rows(
            raw[GOLD_SYMBOL],
            expected_date,
            expected_previous_date,
            require_normalized_shape=False,
            reject_non_completed=False,
            error_code="history_contract_invalid",
        )

    def _normalize_rows(
        self,
        rows: Any,
        expected_date: date,
        expected_previous_date: date,
        *,
        require_normalized_shape: bool,
        reject_non_completed: bool,
        error_code: str,
    ) -> list[dict[str, object]]:
        if not isinstance(rows, list):
            raise GoldDataError(error_code)
        normalized: list[dict[str, object]] = []
        seen_dates: set[date] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise GoldDataError(error_code)
            if require_normalized_shape and set(row) != {"date", "close"}:
                raise GoldDataError(error_code)
            symbol = row.get("symbol")
            if symbol is not None and symbol != GOLD_SYMBOL:
                raise GoldDataError(error_code)
            row_date = self._parse_date(row.get("date"))
            close = row.get("close")
            if (
                row_date is None
                or row_date in seen_dates
                or isinstance(close, bool)
                or not isinstance(close, (int, float))
                or not math.isfinite(close)
                or close <= 0
            ):
                raise GoldDataError(error_code)
            seen_dates.add(row_date)
            if reject_non_completed and row_date >= expected_date:
                raise GoldDataError(error_code)
            if row_date < expected_date:
                normalized.append({"date": row_date.isoformat(), "close": float(close)})
        normalized.sort(key=lambda row: str(row["date"]))
        if (
            not normalized
            or normalized[-1]["date"] != expected_previous_date.isoformat()
        ):
            raise GoldDataError("history_preceding_session_mismatch")
        return normalized

    @staticmethod
    def _parse_date(value: Any) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if not isinstance(value, str):
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _rows_digest(rows: list[dict[str, object]]) -> str:
        encoded = json.dumps(rows, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _latest_closes(rows: list[dict[str, object]]) -> list[float]:
        if len(rows) < 60:
            raise GoldDataError("history_insufficient")
        return [float(row["close"]) for row in rows[-60:]]
