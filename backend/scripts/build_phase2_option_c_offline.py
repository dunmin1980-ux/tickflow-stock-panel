#!/usr/bin/env python3
"""Build the deterministic Phase 2B Option C evidence tree offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.phase2_option_c_delivery import publish_option_c_run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--reports-root", type=Path, required=True)
    args = parser.parse_args()
    result = publish_option_c_run(args.repo_root, args.reports_root)
    print(
        json.dumps(
            {
                "status": result.status,
                "output_directory": str(result.output_directory),
                "artifact_count": len(result.artifact_sha256),
                "replay_status": result.replay.status,
                "replay_count": result.replay.replay_count,
                "protected_evidence_unchanged": (
                    result.protected_before == result.protected_after
                ),
                "tickflow_api_requests": 0,
                "real_provider_attempts": 0,
                "real_ai_calls": 0,
                "broker_calls": 0,
                "cloud_deployments": 0,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
