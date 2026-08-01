"""Validate the Phase 2 Fake Provider worker's real container isolation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.phase2_isolation_runtime import run_runtime_validation


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--reports-root", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    result = run_runtime_validation(args.repo_root, args.reports_root)
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
    return 0 if result.status == "PHASE2B_ISOLATION_RUNTIME_VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
