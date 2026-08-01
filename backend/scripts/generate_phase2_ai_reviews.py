"""Generate the three fixed Phase 2 AI reviews with one Codex CLI call each."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.services.ai_provider import codex_cli_available, generate_codex_cli_text
from app.services.phase2_ai_review import (
    PHASE2_READY,
    Phase2ReviewError,
    ProviderResult,
    ReviewPrompt,
    run_review_batch,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--reports-root", type=Path, required=True)
    parser.add_argument("--provider", choices=("codex_cli",), default="codex_cli")
    parser.add_argument(
        "--model",
        default="",
        help="Optional Codex model override; empty uses the isolated CLI default.",
    )
    parser.add_argument("--timeout", type=float, default=900.0)
    return parser


async def _run(args: argparse.Namespace) -> dict:
    if not codex_cli_available():
        return {
            "status": "PHASE2_AI_USER_ACTION_REQUIRED",
            "reason": "codex_cli_unavailable",
        }

    async def provider(prompt: ReviewPrompt, symbol: str) -> ProviderResult:
        started = time.perf_counter_ns()
        text = await generate_codex_cli_text(
            prompt.messages,
            max_tokens=3500,
            timeout=args.timeout,
            model=args.model,
        )
        duration_ms = max(0, (time.perf_counter_ns() - started) // 1_000_000)
        return ProviderResult(
            text=text,
            provider="codex_cli",
            model=args.model or "codex_cli_default",
            model_source=(
                "explicit_cli_argument"
                if args.model
                else "isolated_codex_cli_default_configuration"
            ),
            duration_ms=duration_ms,
        )

    return await run_review_batch(
        args.repo_root,
        args.reports_root,
        provider,
        generated_at=datetime.now(ZoneInfo("Asia/Shanghai")),
    )


def main() -> int:
    args = _parser().parse_args()
    try:
        result = asyncio.run(_run(args))
    except (OSError, Phase2ReviewError, RuntimeError):
        print(
            json.dumps(
                {
                    "status": "PHASE2_AI_USER_ACTION_REQUIRED",
                    "reason": "codex_cli_generation_failed_without_retry",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 3
    summary = {
        "status": result["status"],
        "provider_attempt_count": result.get("provider_attempt_count", 0),
        "retry_count": result.get("retry_count", 0),
        "tickflow_api_request_count": result.get("tickflow_api_request_count", 0),
        "cloud_mutation_count": result.get("cloud_mutation_count", 0),
        "symbols": [
            {
                "symbol": item["symbol"],
                "review_status": item["review_status"],
                "unsupported_numeric_claim_count": item[
                    "unsupported_numeric_claim_count"
                ],
                "forbidden_term_count": item["forbidden_term_count"],
            }
            for item in result.get("symbols", [])
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == PHASE2_READY else 2


if __name__ == "__main__":
    raise SystemExit(main())
