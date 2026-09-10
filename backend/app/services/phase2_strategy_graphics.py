"""Read-only chart projections of validated inputs; no new research or action rules."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from pathlib import Path

import polars as pl

from app.indicators.pipeline import compute_indicators
from app.services.phase2_facts import INDICATOR_COLUMNS
from app.services.phase2_research_daily import normalize_strategy
from app.services.phase2_research_panel import evaluate_strategies, research_market
from app.services.phase2_visual_daily_input import VisualDailyInputError

CHART_COLUMNS = {key: INDICATOR_COLUMNS[key] for key in (
    'ma5', 'ma10', 'ma20', 'ma60', 'boll_upper', 'boll_middle', 'boll_lower',
    'macd_dif', 'macd_dea', 'macd_hist', 'rsi6',
)}


def build_strategy_graphics(input_root: Path, target: date, panel: dict) -> dict:
    """Only accepts the already-validated research panel and its exact source hashes."""
    if panel.get('status') != 'READY' or panel.get('trade_date') != target.isoformat():
        raise VisualDailyInputError('GRAPHICS_PANEL_INVALID')
    day_root = input_root / target.isoformat()
    if day_root.is_symlink() or not day_root.is_dir():
        raise VisualDailyInputError('GRAPHICS_SOURCE_INVALID')
    sources, hashes = {}, {}
    for name in ('qfq_daily.json', 'raw_daily.json'):
        path = day_root / name
        if path.is_symlink() or not path.is_file():
            raise VisualDailyInputError('GRAPHICS_SOURCE_INVALID')
        payload = path.read_bytes()
        hashes[name] = hashlib.sha256(payload).hexdigest()
        if hashes[name] != panel['source_hashes'].get(name):
            raise VisualDailyInputError('GRAPHICS_SOURCE_CHANGED')
        rows = json.loads(payload)['rows']
        dates = [date.fromisoformat(row['trade_date']) for row in rows]
        if (not dates or dates[-1] != target or dates != sorted(set(dates))
                or any(d > target for d in dates)):
            raise VisualDailyInputError('GRAPHICS_DATES_INVALID')
        if any(not math.isfinite(float(row[key])) for row in rows
               for key in ('open', 'high', 'low', 'close', 'volume')):
            raise VisualDailyInputError('GRAPHICS_NONFINITE_SOURCE')
        sources[name] = rows
    qfq, raw = sources['qfq_daily.json'], sources['raw_daily.json']
    if len(qfq) != len(raw) or any(a['trade_date'] != b['trade_date'] for a, b in zip(qfq, raw, strict=True)):
        raise VisualDailyInputError('GRAPHICS_DATE_ALIGNMENT_INVALID')
    frame = pl.DataFrame([{
        'symbol': '000403.SZ', 'date': date.fromisoformat(row['trade_date']),
        **{key: float(row[key]) for key in ('open', 'high', 'low', 'close')},
        'volume': float(raw[i]['volume']),
    } for i, row in enumerate(qfq)])
    # Warm every indicator on the complete snapshot before limiting presentation to 30 days.
    computed = compute_indicators(frame, needed=set(CHART_COLUMNS.values()))
    points = []
    for row in computed.tail(30).to_dicts():
        point = {'trade_date': row['date'].isoformat(), 'close': row['close']}
        for key, column in CHART_COLUMNS.items():
            value = row[column]
            if value is not None and not math.isfinite(value):
                raise VisualDailyInputError('GRAPHICS_NONFINITE_INDICATOR')
            point[key] = value
        points.append(point)
    markers = []
    for end in range(max(2, len(qfq) - 29), len(qfq) + 1):
        day = qfq[end - 1]['trade_date']
        # Retrospective same-snapshot prefixes, never historically published decisions.
        for rule in evaluate_strategies(research_market(qfq[:end], raw[:end])):
            strategy = normalize_strategy(rule, day)
            if strategy['status'] in ('TRIGGERED', 'NEAR_TRIGGER'):
                markers.append({
                    'trade_date': day, 'strategy_id': strategy['strategy_id'],
                    'strategy_name': strategy['strategy_name'], 'status': strategy['status'],
                    'price': float(qfq[end - 1]['close']),
                    'conditions_met': strategy['conditions_met'],
                    'conditions_total': strategy['conditions_total'],
                })
    return {
        'schema_version': 1, 'status': 'READY', 'symbol': '000403.SZ',
        'trade_date': target.isoformat(), 'timeframe': '1d', 'price_basis': 'QFQ',
        'history_basis': 'SAME_SNAPSHOT_PREFIX', 'window_requested': 30,
        'points': points, 'strategy_markers': markers, 'source_hashes': hashes,
        'source_provider': panel['source_provider'],
        'indicator_source': 'app.indicators.pipeline.compute_indicators',
        'strategy_source': 'app.services.phase2_research_panel.evaluate_strategies / app.services.phase2_research_daily.normalize_strategy',
        'affects_paper_action': False, 'can_publish': False,
    }
