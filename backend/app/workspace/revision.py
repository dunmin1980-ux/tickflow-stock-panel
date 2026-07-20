import hashlib
import json
import re
from typing import Any

from fastapi.encoders import jsonable_encoder

_REVISION_PATTERN = re.compile(r"[0-9a-f]{64}")
_STRONG_ETAG_PATTERN = re.compile(r'"([0-9a-f]{64})"')


def canonical_bytes(data: Any) -> bytes:
    encoded = jsonable_encoder(data)
    return json.dumps(
        encoded,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def revision_for(data: Any) -> str:
    return hashlib.sha256(canonical_bytes(data)).hexdigest()


def _validate_revision(revision: str) -> str:
    if _REVISION_PATTERN.fullmatch(revision) is None:
        raise ValueError("revision must be a 64-character lowercase hexadecimal digest")
    return revision


def etag_for(revision: str) -> str:
    return f'"{_validate_revision(revision)}"'


def parse_if_match(value: str | None) -> str | None:
    if value is None:
        return None

    candidate = value.strip()
    match = _STRONG_ETAG_PATTERN.fullmatch(candidate)
    if match is None:
        raise ValueError("If-Match must contain exactly one strong workspace ETag")
    return _validate_revision(match.group(1))
