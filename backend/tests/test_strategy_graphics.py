from __future__ import annotations

import copy
import hashlib
import json

import pytest
from test_phase2_visual_daily_input import TARGET, FakeGateway, _builder

from app.schemas.phase2_option_c import SimulationConfig
from app.services.phase2_option_c_continuous import new_continuous_state
from app.services.phase2_research_daily import normalize_strategy
from app.services.phase2_research_panel import (
    build_research_panel,
    evaluate_strategies,
    research_market,
)
from app.services.phase2_visual_daily_input import VisualDailyInputError, _indicator_values


@pytest.fixture
def prepared(tmp_path):
    gateway = FakeGateway(bullish=True)
    _builder(tmp_path, gateway).prepare(TARGET, new_continuous_state(SimulationConfig.default()))
    panel = build_research_panel(tmp_path / 'inputs', TARGET)
    return tmp_path / 'inputs', gateway, panel


def graphics(root, panel):
    from app.services.phase2_strategy_graphics import build_strategy_graphics
    return build_strategy_graphics(root, TARGET, panel)


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def test_window_uses_full_warmup_and_exact_existing_indicators(prepared):
    root, gateway, panel = prepared
    result = graphics(root, panel)
    assert result['status'] == 'READY'
    assert result['price_basis'] == 'QFQ'
    assert result['history_basis'] == 'SAME_SNAPSHOT_PREFIX'
    assert len(result['points']) == 30
    assert result['points'][0]['trade_date'] == gateway.qfq[-30]['trade_date']
    assert result['points'][0]['ma60'] == pytest.approx(sum(r['close'] for r in gateway.qfq[11:71]) / 60)
    last = result['points'][-1]
    existing = _indicator_values(gateway.raw, gateway.qfq)
    for key in ('ma5', 'ma10', 'ma20', 'ma60', 'boll_upper', 'boll_middle',
                'boll_lower', 'macd_dif', 'macd_dea', 'macd_hist', 'rsi6'):
        assert last[key] == existing[key]
    assert last['trade_date'] == TARGET.isoformat()
    json.dumps(result, allow_nan=False)


def test_read_only_idempotent_and_keeps_rule_conclusions(prepared):
    root, gateway, panel = prepared
    before = snapshot(root)
    original = copy.deepcopy(panel)
    first = graphics(root, panel)
    assert first == graphics(root, panel)
    assert panel == original
    assert snapshot(root) == before
    assert gateway.calls == 1  # The fixture builder only; graphics never fetches.
    assert first['source_hashes'] == {name: panel['source_hashes'][name]
                                    for name in ('qfq_daily.json', 'raw_daily.json')}


def test_markers_are_existing_rules_on_each_prefix_not_future_rows(prepared):
    root, gateway, panel = prepared
    result = graphics(root, panel)
    expected = []
    for end in range(71, 101):
        day = gateway.qfq[end-1]['trade_date']
        rules = evaluate_strategies(research_market(gateway.qfq[:end], gateway.raw[:end]))
        for rule in rules:
            strategy = normalize_strategy(rule, day)
            if strategy['status'] in ('TRIGGERED', 'NEAR_TRIGGER'):
                expected.append((day, strategy['strategy_id'], strategy['status']))
    assert [(m['trade_date'], m['strategy_id'], m['status']) for m in result['strategy_markers']] == expected


@pytest.mark.parametrize('mutation', ['hash', 'symlink', 'future_date', 'nonfinite'])
def test_invalid_source_is_rejected(prepared, mutation):
    root, _, panel = prepared
    path = root / TARGET.isoformat() / 'qfq_daily.json'
    if mutation == 'symlink':
        other = path.with_suffix('.test-backup')
        path.rename(other)
        path.symlink_to(other)
    elif mutation == 'hash':
        path.write_bytes(path.read_bytes() + b' ')
    else:
        payload = json.loads(path.read_bytes())
        payload['rows'][-1]['trade_date' if mutation == 'future_date' else 'close'] = (
            '2099-01-01' if mutation == 'future_date' else float('nan'))
        path.write_text(json.dumps(payload))
        panel['source_hashes']['qfq_daily.json'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(VisualDailyInputError):
        graphics(root, panel)


def test_dashboard_attaches_graphics_without_state_mutation(tmp_path):
    from test_phase2_visual_workbench import TODAY, FakeDailyGateway, daily_builder, service_for
    gateway = FakeDailyGateway()
    service = service_for(tmp_path, builder=daily_builder(tmp_path, gateway))
    service.run(TODAY)
    before = snapshot(tmp_path)
    result = service.dashboard(TODAY)
    assert result['research']['graphics']['status'] == 'READY'
    assert result['research']['engine'] == build_research_panel(
        tmp_path / 'inputs', TODAY, state_root=tmp_path / 'state')['engine']
    assert snapshot(tmp_path) == before
    assert gateway.calls == 1


def test_warmup_gaps_stay_null_and_raw_price_is_never_charted(prepared):
    root, _, panel = prepared
    for name in ('qfq_daily.json', 'raw_daily.json'):
        path = root / TARGET.isoformat() / name
        payload = json.loads(path.read_bytes())
        payload['rows'] = payload['rows'][-80:]
        if name == 'raw_daily.json':
            for row in payload['rows']:
                for key in ('open', 'high', 'low', 'close'):
                    row[key] *= 3
        path.write_text(json.dumps(payload))
        panel['source_hashes'][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    result = graphics(root, panel)
    assert len(result['points']) == 30
    assert all(p['ma60'] is None for p in result['points'][:9])
    assert result['points'][9]['ma60'] is not None
    assert result['points'][-1]['close'] < 20


def test_graphics_failure_does_not_hide_valid_research_or_account(tmp_path, monkeypatch):
    from test_phase2_visual_workbench import TODAY, FakeDailyGateway, daily_builder, service_for

    from app.services import phase2_strategy_graphics
    gateway = FakeDailyGateway()
    service = service_for(tmp_path, builder=daily_builder(tmp_path, gateway))
    ready = service.run(TODAY)
    before = snapshot(tmp_path)

    def reject(*args):
        raise VisualDailyInputError('GRAPHICS_SOURCE_CHANGED')

    monkeypatch.setattr(phase2_strategy_graphics, 'build_strategy_graphics', reject)
    blocked = service.dashboard(TODAY)
    assert blocked['research']['graphics']['status'] == 'BLOCKED'
    assert blocked['research']['engine'] == ready['research']['engine']
    assert blocked['account'] == ready['account']
    assert snapshot(tmp_path) == before
