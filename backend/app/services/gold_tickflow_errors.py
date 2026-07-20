"""Sanitize TickFlow failures for Gold Stage A (no secrets in logs/payloads)."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_BEARER = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)\S+")
_API_KEY = re.compile(r"(?i)((?:api[_-]?key|token)\s*[=:]\s*)\S+")


def _redact(text: str) -> str:
    text = _BEARER.sub(r"\1***", text)
    text = _API_KEY.sub(r"\1***", text)
    return text[:240]


@dataclass
class TickFlowFailure:
    code: str
    retry_after_seconds: float | None = None
    status_code: int | None = None


def classify_tickflow_exception(exc: BaseException) -> TickFlowFailure:
    """Map SDK/HTTP exceptions to stable Gold error codes (never embed secrets)."""
    status = _extract_status(exc)
    if status == 429:
        return TickFlowFailure(
            "tickflow_rate_limited",
            retry_after_seconds=_extract_retry_after(exc),
            status_code=429,
        )
    if status in (401, 403):
        return TickFlowFailure("tickflow_auth_failed", status_code=status)
    name = type(exc).__name__.lower()
    msg = _redact(str(exc)).lower()
    if any(k in name or k in msg for k in ("timeout", "timed out", "connection", "network")):
        return TickFlowFailure("tickflow_network_failed", status_code=status)
    if "contract" in msg or "schema" in msg:
        return TickFlowFailure("tickflow_contract_failed", status_code=status)
    return TickFlowFailure("tickflow_request_failed", status_code=status)


def _extract_status(exc: BaseException) -> int | None:
    for attr in ("status_code", "status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        value = getattr(response, "status_code", None)
        if isinstance(value, int):
            return value
    return None


def _extract_retry_after(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if headers is None:
        return None
    raw = None
    try:
        raw = headers.get("Retry-After") or headers.get("retry-after")
    except Exception:
        raw = None
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


@dataclass
class RateLimitCircuit:
    """Stage A: stop pipeline on first 429; trip after second consecutive 429."""

    consecutive_429: int = 0
    tripped: bool = False
    last_event: dict[str, Any] | None = None

    def note_success(self) -> None:
        self.consecutive_429 = 0

    def note_rate_limited(
        self,
        *,
        pipeline: str,
        capability: str,
        symbol_count: int,
        retry_after_seconds: float | None,
        wait_seconds: float | None = None,
        retries: int = 0,
    ) -> dict[str, Any]:
        self.consecutive_429 += 1
        event = {
            "schema_version": 1,
            "observed_at": datetime.now(UTC).isoformat(),
            "code": "tickflow_rate_limited",
            "pipeline": pipeline,
            "capability": capability,
            "symbol_count": symbol_count,
            "retry_after_seconds": retry_after_seconds,
            "wait_seconds": wait_seconds,
            "retries": retries,
            "consecutive_429": self.consecutive_429,
            "task_status": "pipeline_halted",
        }
        if self.consecutive_429 >= 2:
            self.tripped = True
            event["task_status"] = "circuit_open"
        self.last_event = event
        logger.warning(
            "gold_tickflow_rate_limited pipeline=%s capability=%s symbols=%s consecutive=%s tripped=%s",
            pipeline,
            capability,
            symbol_count,
            self.consecutive_429,
            self.tripped,
        )
        return event
