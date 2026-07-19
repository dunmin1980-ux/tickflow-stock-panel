"""Safe machine-readable failures for the Gold data pipeline."""
from __future__ import annotations

from typing import Final

SAFE_GOLD_ERROR_MESSAGES: Final[dict[str, str]] = {
    "tickflow_capability_unavailable": "TickFlow capability is unavailable",
    "tickflow_paid_client_unavailable": "TickFlow paid client is unavailable",
    "tickflow_request_failed": "TickFlow request failed",
    "quote_contract_invalid": "TickFlow quote contract is invalid",
    "quote_symbol_mismatch": "TickFlow quote symbol does not match Gold",
    "quote_source_mismatch": "Gold quote source must be TickFlow",
    "quote_timestamp_missing": "TickFlow quote timestamp is invalid",
    "quote_timestamp_stale": "TickFlow quote timestamp is stale",
    "quote_timestamp_future": "TickFlow quote timestamp is in the future",
    "quote_timestamp_out_of_session": "TickFlow quote is outside session",
    "stale_market_date": "TickFlow quote market date is stale",
    "previous_close_missing": "TickFlow previous close is invalid",
    "history_contract_invalid": "TickFlow history contract is invalid",
    "history_preceding_session_mismatch": (
        "TickFlow history does not end on the expected trading session"
    ),
    "history_insufficient": "TickFlow history is insufficient",
    "daily_cache_invalid": "Gold daily history cache is invalid",
    "calendar_unconfigured": "Gold trading calendar is unavailable",
    "evaluation_failed": "Gold evaluation failed",
    "gold_storage_read_failed": "Gold storage read failed",
    "gold_storage_write_failed": "Gold storage write failed",
}
SAFE_GOLD_ERROR_CODES: Final[frozenset[str]] = frozenset(SAFE_GOLD_ERROR_MESSAGES)


class GoldDataError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def safe_gold_error(code: str) -> tuple[str, str] | None:
    message = SAFE_GOLD_ERROR_MESSAGES.get(code)
    return (code, message) if message is not None else None
