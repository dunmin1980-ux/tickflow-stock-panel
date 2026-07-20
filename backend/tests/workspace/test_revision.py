from datetime import UTC, datetime
from threading import RLock
from typing import Any

import pytest
from pydantic import BaseModel

from app.workspace.locks import resource_lock
from app.workspace.models import ResourceName, ResourceSnapshot
from app.workspace.revision import canonical_bytes, etag_for, parse_if_match, revision_for


class CanonicalPayload(BaseModel):
    observed_at: datetime
    values: dict[str, Any]


def test_revision_is_order_independent_recursively():
    left = {"nested": {"b": 2, "a": 1}, "name": "派林生物"}
    right = {"name": "派林生物", "nested": {"a": 1, "b": 2}}

    assert revision_for(left) == revision_for(right)


def test_canonical_bytes_are_compact_sorted_utf8_json():
    assert canonical_bytes({"b": 2, "a": "中金黄金"}) == ('{"a":"中金黄金","b":2}'.encode())


def test_pydantic_and_datetime_values_are_stable():
    payload = CanonicalPayload(
        observed_at=datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
        values={"close": 21.5},
    )
    equivalent = {
        "values": {"close": 21.5},
        "observed_at": "2026-07-20T15:00:00Z",
    }

    assert revision_for(payload) == revision_for(equivalent)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_number_is_rejected_recursively(value: float):
    with pytest.raises(ValueError):
        revision_for({"outer": [{"close": value}]})


def test_resource_snapshot_accepts_workspace_model_values():
    snapshot = ResourceSnapshot(
        resource=ResourceName.WATCHLIST,
        revision="a" * 64,
        updated_at=datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
        data={"symbols": ["000403.SZ"]},
    )

    assert snapshot.resource is ResourceName.WATCHLIST
    assert snapshot.data == {"symbols": ["000403.SZ"]}


def test_etag_round_trip():
    revision = "a" * 64

    assert etag_for(revision) == f'"{revision}"'
    assert parse_if_match(f'  "{revision}"  ') == revision
    assert parse_if_match(None) is None


@pytest.mark.parametrize(
    "revision",
    [
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "g" * 64,
        f"{'a' * 63} ",
    ],
)
def test_etag_for_rejects_invalid_revisions(revision: str):
    with pytest.raises(ValueError):
        etag_for(revision)


@pytest.mark.parametrize(
    "value",
    [
        "*",
        'W/"' + "a" * 64 + '"',
        '"' + "a" * 64 + '", "' + "b" * 64 + '"',
        "a" * 64,
        '"' + "a" * 63 + '"',
        '"' + "A" * 64 + '"',
        '"' + "g" * 64 + '"',
        '"' + "a" * 64,
        "",
    ],
)
def test_parse_if_match_rejects_ambiguous_or_malformed_values(value: str):
    with pytest.raises(ValueError):
        parse_if_match(value)


def test_resource_lock_returns_one_precreated_rlock_per_resource():
    watchlist_lock = resource_lock(ResourceName.WATCHLIST)

    assert isinstance(watchlist_lock, type(RLock()))
    assert resource_lock(ResourceName.WATCHLIST) is watchlist_lock
    assert resource_lock(ResourceName.PREFERENCES) is not watchlist_lock


def test_resource_lock_rejects_unknown_names():
    with pytest.raises(ValueError):
        resource_lock("unknown")  # type: ignore[arg-type]
