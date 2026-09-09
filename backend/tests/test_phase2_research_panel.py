from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from app.backtest.matrix import build_market_data_matrix
from app.schemas.phase2_option_c import SimulationConfig
from app.services.phase2_option_c_continuous import new_continuous_state
from app.services.phase2_research_panel import build_research_panel, evaluate_strategies
from app.services.phase2_visual_daily_input import VisualDailyInputError
from tests.test_phase2_visual_daily_input import FakeGateway, TARGET, _builder


def snapshot(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def test_panel_reuses_validated_input_is_deterministic_and_never_changes_evidence(tmp_path):
    gateway = FakeGateway(bullish=True)
    _builder(tmp_path, gateway).prepare(TARGET, new_continuous_state(SimulationConfig.default()))
    before = snapshot(tmp_path)
    first = build_research_panel(tmp_path / 'inputs', TARGET)
    second = build_research_panel(tmp_path / 'inputs', TARGET)
    assert first == second
    assert snapshot(tmp_path) == before
    assert gateway.calls == 1
    assert first['status'] == 'READY'
    assert first['trade_date'] == TARGET.isoformat()
    assert first['previous_trade_date'] < first['trade_date']
    assert first['price_basis'] == 'qfq'
    assert first['affects_paper_action'] is False
    assert first['chan_status'] == 'READY'
    assert len(first['strategies']) == 6
    assert {s['id'] for s in first['strategies']} == {
        'macd_golden', 'bullish_alignment', 'boll_breakout',
        'volume_price_surge', 'pullback_to_support', 'boll_lower_reclaim',
    }
    for strategy in first['strategies']:
        assert strategy['conditions']
        assert strategy['calculation_source']
        assert all(c['actual'] and c['required'] for c in strategy['conditions'])
    assert first['paper_rule']['signal'] == 'POSITIVE_OBSERVATION'
    assert all(c['passed'] for c in first['paper_rule']['positive_conditions'])
    json.dumps(first, allow_nan=False)


def test_panel_rejects_tampered_evidence(tmp_path):
    gateway = FakeGateway()
    _builder(tmp_path, gateway).prepare(TARGET, new_continuous_state(SimulationConfig.default()))
    path = tmp_path / 'inputs' / TARGET.isoformat() / 'qfq_daily.json'
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(VisualDailyInputError, match='ARTIFACT_HASH_MISMATCH'):
        build_research_panel(tmp_path / 'inputs', TARGET)


def test_panel_does_not_use_another_dates_input(tmp_path):
    gateway = FakeGateway()
    _builder(tmp_path, gateway).prepare(TARGET, new_continuous_state(SimulationConfig.default()))
    with pytest.raises(VisualDailyInputError):
        build_research_panel(tmp_path / 'inputs', TARGET + timedelta(days=1))


def test_old_missing_amount_does_not_block_price_and_relative_volume_research(tmp_path):
    gateway = FakeGateway()
    for rows in (gateway.raw, gateway.qfq):
        previous = date.fromisoformat(rows[0]['trade_date']) - timedelta(days=1)
        rows.insert(0, {**rows[0], 'trade_date': previous.isoformat()})
    # More than the dataframe default inference window has missing qfq amounts.
    for row in gateway.qfq[:-1]:
        row['amount'] = None
    _builder(tmp_path, gateway).prepare(TARGET, new_continuous_state(SimulationConfig.default()))
    assert build_research_panel(tmp_path / 'inputs', TARGET)['status'] == 'READY'


def market(closes, volumes=None):
    n = len(closes)
    return build_market_data_matrix(pl.DataFrame({
        'date': pl.date_range(TARGET - timedelta(days=n-1), TARGET, eager=True),
        'symbol': ['000403.SZ'] * n,
        'open': np.asarray(closes) - 0.01,
        'high': np.asarray(closes) + 0.1,
        'low': np.asarray(closes) - 0.1,
        'close': closes,
        'volume': volumes if volumes is not None else [1000.] * n,
    }))


def test_strategy_outcomes_match_existing_builtin_engine():
    from app.strategy.builtin import (boll_breakout, bullish_alignment, macd_golden,
                                     pullback_to_support, volume_price_surge)
    m = market([10. + i * .02 for i in range(99)] + [14.], [1000.] * 99 + [3000.])
    results = {s['id']: s for s in evaluate_strategies(m)}
    for module in (macd_golden, bullish_alignment, boll_breakout,
                   volume_price_surge, pullback_to_support):
        params = {p['id']: p['default'] for p in module.META['params']}
        expected = module.MATRIX_STRATEGY.compute_signals(m, params)
        result = results[module.META['id']]
        assert (result['status'] == 'MATCHED') == bool(expected.entry[-1, 0])
        assert (result['previous_status'] == 'MATCHED') == bool(expected.entry[-2, 0])
        assert all(c['passed'] for c in result['conditions']) == bool(expected.entry[-1, 0])


@pytest.mark.parametrize(('prior', 'last'), [(9.8, 10.2), (10.2, 9.8)])
@pytest.mark.parametrize('volume', [500., 1000.])
def test_pullback_explanation_uses_exact_builtin_strict_boundaries(prior, last, volume):
    result = {s['id']: s for s in evaluate_strategies(market(
        [9.] * 80 + [prior] + [10.] * 18 + [last], [1000.] * 99 + [volume],
    ))}['pullback_to_support']
    assert result['status'] == 'NOT_MATCHED'
    assert result['conditions'][0]['passed'] is False


def test_boll_lower_reclaim_requires_prior_breach_and_current_reentry():
    hit = {s['id']: s for s in evaluate_strategies(market([10.] * 98 + [8., 10.]))}
    miss = {s['id']: s for s in evaluate_strategies(market([10.] * 100))}
    assert hit['boll_lower_reclaim']['status'] == 'MATCHED'
    assert hit['boll_lower_reclaim']['change'] == 'NEW_MATCH'
    assert miss['boll_lower_reclaim']['status'] == 'NOT_MATCHED'


def test_zero_volume_does_not_become_a_valid_volume_signal():
    results = {s['id']: s for s in evaluate_strategies(market([10.] * 100, [0.] * 100))}
    for name in ('macd_golden', 'boll_breakout', 'volume_price_surge', 'pullback_to_support'):
        assert results[name]['status'] == 'DATA_UNAVAILABLE'
    json.dumps(results, allow_nan=False)


def test_short_history_does_not_claim_valid_strategy_results():
    results = evaluate_strategies(market([10.] * 20))
    assert all(s['status'] == 'DATA_UNAVAILABLE' for s in results)
