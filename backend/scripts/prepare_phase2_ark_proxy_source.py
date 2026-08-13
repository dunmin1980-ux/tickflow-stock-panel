#!/usr/bin/env python3
"""Derive the isolated Ark proxy source from the frozen OpenAI implementation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "docker/phase2-openai-egress-proxy/proxy.py"
TARGET = REPO_ROOT / "docker/phase2-ark-egress-proxy/proxy.py"


REPLACEMENTS = (
    ('UPSTREAM_HOST = "api.openai.com"', 'UPSTREAM_HOST = "ark.cn-beijing.volces.com"'),
    ('UPSTREAM_PATH = "/v1/responses"', 'UPSTREAM_PATH = "/api/v3/responses"'),
    ('MODEL_ID = "gpt-5.6-terra"', 'MODEL_ID = "doubao-seed-2-1-turbo-260628"'),
    (
        "_CONTRACT_FIELDS = {\n",
        '_CONTRACT_FIELDS = {\n    "ark_responses_contract_version",\n    "capability_evidence_sha256",\n',
    ),
    (
        "_RECEIPT_FIELDS = {\n",
        '_RECEIPT_FIELDS = {\n    "provider_id",\n    "endpoint_alias",\n    "exact_model_id",\n',
    ),
    (
        'if set(value) != _CONTRACT_FIELDS or value.get("contract_schema_version") != 1:',
        'if (\n        set(value) != _CONTRACT_FIELDS\n        or value.get("contract_schema_version") != 1\n        or value.get("ark_responses_contract_version") != 1\n        or not isinstance(value.get("capability_evidence_sha256"), str)\n    ):',
    ),
    ('"provider_id": "openai",', '"provider_id": "volcengine_ark",'),
    (
        '        "component": "proxy",\n',
        '        "component": "proxy",\n'
        '        "provider_id": "volcengine_ark",\n'
        '        "endpoint_alias": "ark_responses_cn_beijing_v1",\n'
        '        "exact_model_id": MODEL_ID,\n',
    ),
)


def derived_source() -> bytes:
    text = SOURCE.read_text(encoding="utf-8")
    text = text.replace(
        '"""Dedicated, fail-closed OpenAI adapter for the Phase 2B single-symbol Canary."""',
        '"""Dedicated, fail-closed Ark adapter for the Phase 2B single-symbol Canary."""',
        1,
    )
    for before, after in REPLACEMENTS:
        if before not in text:
            raise RuntimeError(f"ark_proxy_derivation_anchor_missing:{before}")
        text = text.replace(before, after, 1)
    text = text.replace(
        "OpenAI Responses request",
        "Ark Responses request",
    )
    text = text.replace(
        "OpenAI-style Responses envelope",
        "Ark Responses envelope",
    )
    text = text.replace(
        '            "store": False,\n            "tools": [],\n            "input": [',
        '            "store": False,\n            "tools": [],\n            "temperature": 0,\n            "input": [',
        1,
    )
    if text.count("if monotonic() > deadline:") != 2:
        raise RuntimeError("ark_proxy_deadline_anchor_invalid")
    text = text.replace("if monotonic() > deadline:", "if monotonic() >= deadline:")
    contract = json.loads(
        (REPO_ROOT / "docker/phase2-ark-egress-proxy/responses-contract.json").read_text(
            encoding="utf-8"
        )
    )
    contract_sha256 = contract.get("contract_sha256")
    if not isinstance(contract_sha256, str) or len(contract_sha256) != 64:
        raise RuntimeError("ark_proxy_contract_sha256_invalid")
    marker = "_EXPECTED_CONTRACT_SHA256 = (\n"
    start = text.index(marker) + len(marker)
    end = text.index("\n)", start)
    text = text[:start] + f'    "{contract_sha256}"' + text[end:]
    return text.encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    expected = derived_source()
    if args.write:
        TARGET.parent.mkdir(parents=True, exist_ok=True)
        TARGET.write_bytes(expected)
        TARGET.chmod(0o644)
        return 0
    return 0 if TARGET.read_bytes() == expected else 2


if __name__ == "__main__":
    raise SystemExit(main())
