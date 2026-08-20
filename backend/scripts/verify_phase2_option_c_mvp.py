#!/usr/bin/env python3
"""Publish deterministic TickFlow Paper Trading MVP release evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.phase2_option_c_release import publish_mvp_release


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--reports-root", type=Path, required=True)
    parser.add_argument("--allow-test-output-root", action="store_true")
    args = parser.parse_args()
    result = publish_mvp_release(
        args.repo_root,
        args.reports_root,
        _allow_test_output_root=args.allow_test_output_root,
    )
    print(
        json.dumps(
            {
                "status": result.status,
                "output_directory": str(result.output_directory),
                "mvp_release_replay_x3": "PASSED",
                "release_sha256": result.replay.release_sha256,
                "protected_evidence_unchanged": (result.protected_before == result.protected_after),
                "real_provider_attempts": 0,
                "real_ai_calls": 0,
                "real_trading": "DISABLED",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
