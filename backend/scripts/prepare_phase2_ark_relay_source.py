#!/usr/bin/env python3
"""Derive the Ark relay source from the shared relay with timeout-only changes."""

from __future__ import annotations

import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "docker/phase2-canary-relay/relay.py"
TARGET = REPO_ROOT / "docker/phase2-ark-canary-relay/relay.py"

REPLACEMENTS = (
    ('"host_orchestrator_timeout_seconds": 90,', '"host_orchestrator_timeout_seconds": 210,'),
    ('"provider_read_timeout_seconds": 60,', '"provider_read_timeout_seconds": 180,'),
    ('"provider_total_timeout_seconds": 60,', '"provider_total_timeout_seconds": 180,'),
    ('"relay_candidate_wait_timeout_seconds": 75,', '"relay_candidate_wait_timeout_seconds": 195,'),
    (
        'if time.monotonic() > deadline:\n            raise RelayError("RELAY_TIMEOUT", proxy_request_count=1)',
        'if time.monotonic() >= deadline:\n            raise RelayError("RELAY_TIMEOUT", proxy_request_count=1)',
    ),
)


def derived_source() -> bytes:
    text = SOURCE.read_text(encoding="utf-8")
    for before, after in REPLACEMENTS:
        if text.count(before) != 1:
            raise RuntimeError(f"ark_relay_derivation_anchor_invalid:{before}")
        text = text.replace(before, after, 1)
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
