"""Non-activating eligibility gate for the isolated Gold observation period."""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any, Protocol

from app.services.gold_shadow_store import GoldShadowStorageReadError, GoldShadowStore

VALID_STATUSES = frozenset({"collecting", "failed", "review_eligible"})
_REQUIRED_COMPLETE_TRADING_DAYS = 10
_ALL_RUNS_LIMIT = 2**31 - 1


class _AttemptCounter(Protocol):
    def attempt_count(self) -> int:
        ...


class GoldObservationService:
    """Derive review eligibility without exposing any notification activation path."""

    def __init__(self, store: GoldShadowStore, notifier: _AttemptCounter) -> None:
        self.store = store
        self.notifier = notifier

    def review_day(
        self,
        market_date: date,
        run_id: str,
        verified: bool,
        note: str,
    ) -> None:
        market_date_text = self._market_date_text(market_date)
        clean_note = self._validated_note(verified, note)
        try:
            holidays = {date.fromisoformat(day) for day in self.store.read_holidays()}
        except GoldShadowStorageReadError as exc:
            raise ValueError("trading calendar is unavailable") from exc
        if not _is_trading_day(market_date, holidays):
            raise ValueError("market_date must be a configured trading day")
        try:
            runs = self.store.list_comparison_runs(limit=_ALL_RUNS_LIMIT)
        except GoldShadowStorageReadError as exc:
            raise ValueError("comparison state is invalid") from exc
        canonical_runs, chain_reasons = _canonical_runs(runs)
        if chain_reasons:
            raise ValueError("comparison chain is unresolved")
        canonical = canonical_runs.get(market_date_text)
        if canonical is None or canonical["run_id"] != run_id:
            raise ValueError("review must reference the existing canonical comparison run")
        self.store.append_observation_review(
            {
                "schema_version": 1,
                "review_type": "day",
                "market_date": market_date_text,
                "run_id": run_id,
                "verified": verified,
                "note": clean_note,
                "reviewed_at": datetime.now(UTC).isoformat(),
            }
        )

    def review_restart(self, verified: bool, note: str) -> None:
        self.store.append_observation_review(
            {
                "schema_version": 1,
                "review_type": "restart",
                "verified": verified,
                "note": self._validated_note(verified, note),
                "reviewed_at": datetime.now(UTC).isoformat(),
            }
        )

    def status(self) -> dict[str, Any]:
        reasons: list[str] = []
        external_send_count: int | None
        try:
            external_send_count = self.notifier.attempt_count()
        except (OSError, UnicodeError, ValueError):
            external_send_count = None
            reasons.append("external_send_state_corrupt")
        else:
            if external_send_count > 0:
                reasons.append("external_send_attempted")

        canonical_runs: dict[str, dict[str, Any]] = {}
        try:
            runs = self.store.list_comparison_runs(limit=_ALL_RUNS_LIMIT)
        except GoldShadowStorageReadError:
            reasons.append("comparison_state_corrupt")
        else:
            canonical_runs, chain_reasons = _canonical_runs(runs)
            reasons.extend(chain_reasons)

        holidays: set[date] = set()
        calendar_available = False
        try:
            holidays = {date.fromisoformat(day) for day in self.store.read_holidays()}
        except GoldShadowStorageReadError:
            reasons.append("trading_calendar_unavailable")
        else:
            calendar_available = True

        reviews: list[dict[str, Any]] = []
        reviews_available = True
        try:
            reviews = self.store.list_observation_reviews()
        except GoldShadowStorageReadError:
            reviews_available = False
            reasons.append("observation_review_state_corrupt")

        passing_runs: dict[str, str] = {}
        automatic_states: dict[str, bool | None] = {}
        for market_date_text, metadata in canonical_runs.items():
            automatic_passed: bool | None = None
            run: dict[str, Any] | None = None
            run_read_failed = False
            try:
                run = self.store.get_comparison_run(metadata["run_id"])
            except GoldShadowStorageReadError:
                run_read_failed = True
                reasons.append(f"comparison_run_corrupt:{metadata['run_id']}")
            thresholds_pass = _thresholds_pass(run) if run is not None else None
            if not run_read_failed:
                if run is None:
                    reasons.append(f"comparison_run_missing:{metadata['run_id']}")
                elif thresholds_pass is None:
                    reasons.append(f"automatic_tolerance_state_corrupt:{metadata['run_id']}")
                elif not thresholds_pass:
                    automatic_passed = False
                    reasons.append(f"automatic_tolerance_failed:{metadata['run_id']}")
                elif calendar_available:
                    automatic_passed = _is_trading_day(
                        date.fromisoformat(market_date_text), holidays
                    )
                    if automatic_passed:
                        passing_runs[market_date_text] = metadata["run_id"]
            automatic_states[market_date_text] = automatic_passed

        latest_day_reviews: dict[str, dict[str, Any]] = {}
        latest_restart_review: dict[str, Any] | None = None
        for review in reviews:
            if review["review_type"] == "day":
                latest_day_reviews[review["market_date"]] = review
            else:
                latest_restart_review = review

        complete_days = sum(
            1
            for market_date_text, run_id in passing_runs.items()
            if (
                (review := latest_day_reviews.get(market_date_text)) is not None
                and review["run_id"] == run_id
                and review["verified"] is True
            )
        )
        restart_verified = (
            latest_restart_review is not None and latest_restart_review["verified"] is True
        )
        canonical_days = []
        for market_date_text, metadata in sorted(canonical_runs.items(), reverse=True):
            review = latest_day_reviews.get(market_date_text) if reviews_available else None
            review_matches = review is not None and review["run_id"] == metadata["run_id"]
            canonical_days.append(
                {
                    "market_date": market_date_text,
                    "run_id": metadata["run_id"],
                    "automatic_passed": automatic_states.get(market_date_text),
                    "review_recorded": review_matches if reviews_available else None,
                    "review_verified": review["verified"] if review_matches else None,
                }
            )
        restart_review = {
            "recorded": latest_restart_review is not None if reviews_available else None,
            "verified": (
                latest_restart_review["verified"] if latest_restart_review is not None else None
            ),
        }

        if reasons:
            state = "failed"
        elif complete_days < _REQUIRED_COMPLETE_TRADING_DAYS or not restart_verified:
            state = "collecting"
        else:
            state = "review_eligible"
        return {
            "status": state,
            "required_complete_trading_days": _REQUIRED_COMPLETE_TRADING_DAYS,
            "complete_trading_days": complete_days,
            "external_send_count": external_send_count,
            "reasons": reasons,
            "canonical_days": canonical_days,
            "restart_review": restart_review,
        }

    @staticmethod
    def _market_date_text(value: date) -> str:
        if not isinstance(value, date) or isinstance(value, datetime):
            raise ValueError("market_date must be a date")
        return value.isoformat()

    @staticmethod
    def _validated_note(verified: bool, note: str) -> str:
        if type(verified) is not bool:
            raise ValueError("verified must be a boolean")
        if not isinstance(note, str) or not note.strip():
            raise ValueError("review note must not be empty")
        return note.strip()


def _canonical_runs(
    runs: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    run_dates: dict[str, set[str]] = defaultdict(set)
    for run in runs:
        by_date[run["market_date"]].append(run)
        run_dates[run["run_id"]].add(run["market_date"])

    reasons = [
        f"comparison_run_id_reused:{run_id}"
        for run_id, dates in run_dates.items()
        if len(dates) > 1
    ]
    canonical: dict[str, dict[str, Any]] = {}
    for market_date_text, day_runs in by_date.items():
        by_id = {run["run_id"]: run for run in day_runs}
        if len(by_id) != len(day_runs):
            reasons.append(f"comparison_chain_unresolved:{market_date_text}")
            continue

        children: dict[str, str] = {}
        roots: list[str] = []
        unresolved = False
        for run in day_runs:
            parent = run["supersedes_run_id"]
            if parent is None:
                roots.append(run["run_id"])
            elif parent not in by_id or parent in children:
                unresolved = True
            else:
                children[parent] = run["run_id"]
        if unresolved or len(roots) != 1:
            reasons.append(f"comparison_chain_unresolved:{market_date_text}")
            continue

        visited: set[str] = set()
        current = roots[0]
        while current not in visited:
            visited.add(current)
            successor = children.get(current)
            if successor is None:
                break
            current = successor
        if len(visited) != len(day_runs):
            reasons.append(f"comparison_chain_unresolved:{market_date_text}")
            continue
        canonical[market_date_text] = by_id[current]
    return canonical, reasons


def _thresholds_pass(run: dict[str, Any]) -> bool | None:
    rows = run.get("rows")
    if not isinstance(rows, list) or not rows:
        return None
    stage_a = rows[-1].get("stage_a")
    if not isinstance(stage_a, dict) or type(stage_a.get("thresholds_pass")) is not bool:
        return None
    return stage_a["thresholds_pass"]


def _is_trading_day(market_date: date, holidays: set[date]) -> bool:
    return market_date.weekday() < 5 and market_date not in holidays
