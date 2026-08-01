"""Validate deterministic Phase 2 Claims artifacts offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.phase2_claims_service import CLAIMS_VALID, validate_claims_directory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--claims-root", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    result = validate_claims_directory(args.repo_root, args.claims_root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == CLAIMS_VALID else 2


if __name__ == "__main__":
    raise SystemExit(main())
