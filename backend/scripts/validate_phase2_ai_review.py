"""Validate Phase 2 AI samples and the isolated Obsidian preview offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.phase2_ai_review import (
    PHASE2_READY,
    Phase2ReviewError,
    validate_review_delivery,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--reports-root", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = validate_review_delivery(args.repo_root, args.reports_root)
    except (OSError, Phase2ReviewError, ValueError):
        result = {
            "status": "PHASE2_AI_OUTPUT_BLOCKED",
            "errors": ["offline_delivery_validation_failed"],
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == PHASE2_READY else 2


if __name__ == "__main__":
    raise SystemExit(main())
