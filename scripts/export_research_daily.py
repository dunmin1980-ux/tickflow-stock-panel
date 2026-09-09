#!/usr/bin/env python3
"""Offline single-symbol research export and three-repeat byte verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.services.phase2_research_daily import research_json_bytes  # noqa: E402
from app.services.phase2_research_panel import build_research_panel  # noqa: E402


def _snapshot(root: Path) -> dict[str, str]:
    paths = sorted(root.rglob('*'))
    if root.is_symlink() or any(p.is_symlink() for p in paths):
        raise ValueError('research_source_symlink_forbidden')
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths if p.is_file()}


def _publish(path: Path, content: bytes) -> None:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('research_export_symlink_forbidden')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.research-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export_days(input_root: Path, state_root: Path, output_root: Path, dates: list[date]) -> dict:
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    if dates != sorted(set(dates)) or not dates or any(d > today for d in dates):
        raise ValueError('research_export_dates_invalid')
    for source in (input_root.resolve(), state_root.resolve()):
        output = output_root.resolve()
        if output.is_relative_to(source) or source.is_relative_to(output):
            raise ValueError('research_output_overlaps_source')
    source_before, account_before = _snapshot(input_root), _snapshot(state_root)
    days = []
    for target in dates:
        results = [build_research_panel(input_root, target, state_root=state_root)['engine'] for _ in range(3)]
        encoded = [research_json_bytes(result) for result in results]
        markdown = [result['daily_markdown'].encode('utf-8') for result in results]
        if encoded[0] != encoded[1] or encoded[1] != encoded[2] or markdown[0] != markdown[1] or markdown[1] != markdown[2]:
            raise ValueError('research_replay_nondeterministic')
        result = results[0]
        _publish(output_root / str(target) / 'research_daily.json', encoded[0])
        _publish(output_root / str(target) / 'research_daily.md', markdown[0])
        days.append({
            'date': str(target), 'consensus': result['aggregate']['research_consensus'],
            'triggered': result['aggregate']['triggered_strategies'],
            'near_trigger': result['aggregate']['near_trigger_strategies'],
            'chan_structure': result['chan_structure']['current_structure'],
            'paper_action': result['summary']['paper_action'],
            'json_sha256': hashlib.sha256(encoded[0]).hexdigest(),
            'markdown_sha256': hashlib.sha256(markdown[0]).hexdigest(),
        })
    if source_before != _snapshot(input_root) or account_before != _snapshot(state_root):
        raise ValueError('research_source_or_account_mutated')
    result = {'replay_x3': 'PASSED', 'symbol': '000403.SZ', 'day_count': len(days), 'days': days,
              'source_unchanged': True, 'account_unchanged': True, 'provider_calls': 0,
              'daily_runner_executed': False, 'can_publish': False,
              'source_file_hashes': source_before, 'account_file_hashes': account_before}
    _publish(output_root / 'replay_verification.json', research_json_bytes(result))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--date', type=date.fromisoformat)
    group.add_argument('--through', type=date.fromisoformat, help='Replay saved dates up to this date, without requests')
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/research_engine_v1')
    args = parser.parse_args()
    base = ROOT / 'backend/data/user_data/phase2_option_c_paper'
    dates = [args.date] if args.date else sorted(date.fromisoformat(p.name) for p in (base / 'inputs').iterdir()
                                              if p.is_dir() and not p.is_symlink() and p.name.startswith('2026-')
                                              and date.fromisoformat(p.name) <= args.through)
    result = export_days(base / 'inputs', base / 'reference_account', args.output, dates)
    print(json.dumps({k: result[k] for k in ('replay_x3', 'day_count', 'provider_calls', 'source_unchanged', 'account_unchanged')}))


if __name__ == '__main__':
    main()
