"""Run the fixed local Phase 2B Provider Relay Mock matrix."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from app.services.phase2_provider_relay_runner import (
    PHASE2B_PROVIDER_RELAY_READY,
    run_mock_provider_relay,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--reports-root", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_mock_provider_relay(args.repo_root, args.reports_root)
    print(
        json.dumps(
            {
                "status": result.status,
                "errors": result.errors,
                "cleanup_complete": result.cleanup_complete,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if result.status == PHASE2B_PROVIDER_RELAY_READY else 2


if __name__ == "__main__":
    raise SystemExit(main())
