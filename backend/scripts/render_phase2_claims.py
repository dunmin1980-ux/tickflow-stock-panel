"""Render and route deterministic Phase 2 Claims without AI or network access."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.phase2_claims_delivery import publish_claims_bundle


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--reports-root", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    result = publish_claims_bundle(args.repo_root, args.reports_root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
