from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.schemas.phase2_claims import WorkerClaimsCandidate
from app.services.phase2_ai_worker_protocol import build_worker_projection

REPO_ROOT = Path(__file__).resolve().parents[2]
FACTS_PATH = REPO_ROOT / "reports/phase2_facts/000403SZ_facts.json"


def projection() -> dict[str, Any]:
    raw = FACTS_PATH.read_bytes()
    return build_worker_projection(
        json.loads(raw),
        hashlib.sha256(raw).hexdigest(),
        facts_bytes=raw,
    )


def candidate() -> dict[str, Any]:
    fixture = json.loads(
        (REPO_ROOT / "reports/phase2_claims/fixtures/000403SZ_claims.json").read_text(
            encoding="utf-8"
        )
    )
    value = {
        "candidate_schema_version": 1,
        "projection_sha256": projection()["projection_sha256"],
        "symbol": fixture["symbol"],
        "name": fixture["name"],
        "trade_date": fixture["trade_date"],
        "timezone": fixture["timezone"],
        "claims": fixture["claims"],
        "trading_advice": False,
    }
    WorkerClaimsCandidate.model_validate(value)
    return value


def provider_request():
    from app.providers.base import ProviderRequest

    value = projection()
    return ProviderRequest(
        provider_id="volcengine_ark",
        exact_model_id="doubao-seed-2-1-turbo-260628",
        endpoint_alias="ark_responses_cn_beijing_v1",
        request_id="a" * 32,
        symbol="000403.SZ",
        projection_sha256=value["projection_sha256"],
        facts_sha256=value["facts_sha256"],
        claims_schema=WorkerClaimsCandidate.model_json_schema(mode="validation"),
        minimal_projection=value,
    )
