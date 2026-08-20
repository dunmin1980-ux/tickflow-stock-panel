#!/usr/bin/env python3
"""Run one offline TickFlow Option C Paper Trading day."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.phase2_option_c_daily import (
    build_reference_day_input,
    load_day_input,
    run_daily_once,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--reference", action="store_true")
    source.add_argument("--input", type=Path)
    args = parser.parse_args()

    day_input = (
        build_reference_day_input(args.repo_root) if args.reference else load_day_input(args.input)
    )
    result = run_daily_once(args.repo_root, args.state_dir, day_input)
    print(
        json.dumps(
            {
                "status": result.status,
                "date": result.daily.report_date.isoformat(),
                "symbol": result.daily.symbol,
                "research_signal": result.daily.research_signal,
                "paper_action": result.daily.paper_action,
                "pending_action": result.daily.pending_action,
                "output_directory": str(result.output_directory),
                "simulation_only": "SIMULATION ONLY",
                "can_publish": False,
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
