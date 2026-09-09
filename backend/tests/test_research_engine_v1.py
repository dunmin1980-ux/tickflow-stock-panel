from __future__ import annotations

import copy
import json

import pytest

from app.services.phase2_research_panel import evaluate_strategies
from tests.test_phase2_research_panel import market


def test_all_six_rules_expose_numeric_condition_contract():
    rules = evaluate_strategies(market([10. + i * .01 for i in range(100)]))
    for rule in rules:
        for evidence in rule['conditions']:
            assert evidence['operator'] in ('gt', 'gte', 'lt', 'lte', 'between')
            assert isinstance(evidence['observed'], float)
            assert evidence['unit'] in ('QFQ_PRICE', 'RATIO', 'PERCENTAGE_POINTS')
            assert evidence['gap'] >= 0
            assert 'threshold' in evidence
            assert 'previous_conditions' in rule


def test_uniform_strategy_contract_and_closest_gap():
    from app.services.phase2_research_daily import normalize_strategy, closest_trigger
    from app.schemas.research_engine import StrategyResearch
    rule = evaluate_strategies(market([10. + i * .01 for i in range(100)]))[4]
    result = normalize_strategy(rule, '2026-09-09')
    parsed = StrategyResearch.model_validate(result)
    assert parsed.timeframe == '1d'
    assert parsed.price_basis == 'QFQ'
    assert parsed.conditions_total == 4
    assert parsed.condition_ratio == parsed.conditions_met / 4
    assert len(parsed.failed_conditions) == 4 - parsed.conditions_met
    assert closest_trigger([result])['strategy_id'] == result['strategy_id']


def test_qfq_is_the_only_research_price_input():
    from app.services.phase2_research_panel import research_market
    from tests.test_phase2_visual_daily_input import FakeGateway
    gateway = FakeGateway()
    before = evaluate_strategies(research_market(gateway.qfq, gateway.raw))
    raw = copy.deepcopy(gateway.raw)
    for bar in raw:
        for field in ('open', 'high', 'low', 'close'):
            bar[field] *= 12
    after = evaluate_strategies(research_market(gateway.qfq, raw))
    assert before == after


def test_strategy_schema_rejects_nan_wrong_basis_and_trade_status():
    from app.schemas.research_engine import StrategyResearch
    from app.services.phase2_research_daily import normalize_strategy
    source = normalize_strategy(evaluate_strategies(market([10.] * 100))[0], '2026-09-09')
    for patch in ({'price_basis': 'raw'}, {'status': 'BUY'}, {'condition_ratio': float('nan')}):
        with pytest.raises(ValueError):
            StrategyResearch.model_validate({**source, **patch})
    json.dumps(source, allow_nan=False)


def synthetic_strategies():
    from app.services.phase2_research_daily import normalize_strategy
    return [normalize_strategy(r, '2026-09-09') for r in evaluate_strategies(market([10. + i * .01 for i in range(100)]))]


def test_conflicts_are_explained_without_creating_actions():
    from app.services.phase2_research_daily import aggregate_research
    strategies = synthetic_strategies()
    strategies[0]['research_bias'] = 'BEARISH'
    result = aggregate_research(strategies, {'current_structure': 'UP_STRUCTURE'}, rsi6=60.)
    assert result['conflicts']
    assert result['research_consensus'] == 'MIXED'
    assert result['research_confidence']['is_probability'] is False
    assert result['rule_trace']
    assert 'paper_action' not in result


@pytest.mark.parametrize(('quantity', 'meaning'), [(0, 'FLAT_WAIT'), (100, 'POSITION_HOLD')])
def test_hold_explanation_uses_real_action_position_and_numeric_rule(quantity, meaning):
    from app.services.phase2_research_daily import explain_paper_action
    panel = {'paper_rule': {'signal': 'MIXED_OBSERVATION', 'positive_conditions': [
        {'label': 'DIF/DEA', 'actual': 'DIF=0.0105 / DEA=0.0470', 'required': 'DIF > DEA', 'passed': False},
        {'label': 'RSI6', 'actual': 'RSI6=50.0891', 'required': 'RSI6 ≥ 50', 'passed': True},
    ]}}
    result = explain_paper_action(panel, {'paper_action': 'HOLD', 'quantity': quantity})
    assert result['hold_reason'] == meaning
    assert any('0.0105' in line for line in result['hold_explanation'])
    assert any('50.0891' in line for line in result['hold_explanation'])
    assert all('综合研究决定' not in line for line in result['hold_explanation'])


def test_unpublished_daily_is_not_fabricated_hold():
    from app.services.phase2_research_daily import explain_paper_action
    result = explain_paper_action({'paper_rule': {'signal': 'MIXED_OBSERVATION', 'positive_conditions': []}}, None)
    assert result['paper_action'] == 'NOT_RUN'
    assert result['position_state'] == 'UNKNOWN'


def test_closest_trigger_uses_observed_gap_not_profit_probability():
    from app.services.phase2_research_daily import closest_trigger
    strategies = synthetic_strategies()
    candidates = [s for s in strategies if s['status'] not in ('TRIGGERED', 'NOT_AVAILABLE')]
    best = max(s['condition_ratio'] for s in candidates)
    result = closest_trigger(strategies)
    assert result['condition_ratio'] == best
    assert result['distance_to_trigger']
    assert all(c['passed'] is not True for c in result['distance_to_trigger'])


def test_unavailable_rules_never_become_bearish_votes():
    from app.services.phase2_research_daily import aggregate_research, normalize_strategy
    strategies = [normalize_strategy(r, '2026-09-09') for r in evaluate_strategies(market([10.] * 20))]
    result = aggregate_research(strategies, {'current_structure': 'INSUFFICIENT_DATA'}, rsi6=50.)
    assert result['research_consensus'] == 'INSUFFICIENT_DATA'
    assert result['bearish_count'] == 0
    assert result['closest_trigger'] is None


def test_research_daily_replay_and_markdown_are_deterministic(tmp_path):
    from app.services.phase2_research_panel import build_research_panel
    from app.services.phase2_research_daily import research_json_bytes
    from app.schemas.phase2_option_c import SimulationConfig
    from app.services.phase2_option_c_continuous import new_continuous_state
    from tests.test_phase2_visual_daily_input import FakeGateway, TARGET, _builder
    gateway = FakeGateway()
    _builder(tmp_path, gateway).prepare(TARGET, new_continuous_state(SimulationConfig.default()))
    runs = [build_research_panel(tmp_path / 'inputs', TARGET)['engine'] for _ in range(3)]
    assert research_json_bytes(runs[0]) == research_json_bytes(runs[1]) == research_json_bytes(runs[2])
    assert runs[0]['daily_markdown'] == runs[1]['daily_markdown'] == runs[2]['daily_markdown']
    for title in ('今日研究结论', '策略矩阵', '支持因素', '限制因素', '策略冲突', '缠论日线结构',
                  '与昨日相比', '最接近触发条件', '下一观察条件', '数据质量'):
        assert title in runs[0]['daily_markdown']
    assert 'can_publish: false' in runs[0]['daily_markdown']
    assert 'NOT INVESTMENT ADVICE' in runs[0]['daily_markdown']
    assert runs[0]['aggregate']['delta_vs_previous']['trade_date'] < TARGET.isoformat()
    assert runs[0]['aggregate']['delta_vs_previous']['current_consensus'] == runs[0]['summary']['research_consensus']
    assert gateway.calls == 1


def test_research_export_writes_separate_reports_without_touching_sources(tmp_path):
    import importlib.util
    from pathlib import Path
    from app.schemas.phase2_option_c import SimulationConfig
    from app.services.phase2_option_c_continuous import new_continuous_state
    from tests.test_phase2_visual_daily_input import FakeGateway, TARGET, _builder
    from tests.test_phase2_research_panel import snapshot
    gateway = FakeGateway()
    _builder(tmp_path, gateway).prepare(TARGET, new_continuous_state(SimulationConfig.default()))
    spec = importlib.util.spec_from_file_location('research_export', Path(__file__).parents[2] / 'scripts/export_research_daily.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    before = snapshot(tmp_path / 'inputs')
    index = module.export_days(tmp_path / 'inputs', tmp_path / 'state', tmp_path / 'reports', [TARGET])
    assert index['replay_x3'] == 'PASSED'
    assert index['source_unchanged'] is True
    assert index['days'][0]['paper_action'] == 'NOT_RUN'
    assert (tmp_path / 'reports' / str(TARGET) / 'research_daily.md').is_file()
    assert before == snapshot(tmp_path / 'inputs')
    assert gateway.calls == 1
    with pytest.raises(ValueError, match='output_overlaps'):
        module.export_days(tmp_path / 'inputs', tmp_path / 'state', tmp_path / 'inputs', [TARGET])


@pytest.mark.parametrize('damage', ['orphan', 'changed_action', 'changed_quantity', 'missing_daily_hash',
                                   'rehashed_quantity', 'missing_daily', 'rehashed_action'])
def test_paper_readback_rejects_unpublished_or_altered_daily(tmp_path, damage):
    import hashlib
    from app.services.phase2_research_daily import load_paper_context
    from tests.test_phase2_visual_workbench import FakeDailyGateway, TODAY, daily_builder, service_for
    service = service_for(tmp_path, builder=daily_builder(tmp_path, FakeDailyGateway()))
    service.run(TODAY)
    root = tmp_path / 'state' / 'days' / str(TODAY)
    path = root / 'chenquant_daily.json'
    daily = json.loads(path.read_bytes())
    assert load_paper_context(tmp_path / 'state', TODAY, daily['facts_identity'])['paper_action'] == daily['paper_action']
    receipt_path = root / 'run_receipt.json'
    receipt = json.loads(receipt_path.read_bytes())
    if damage == 'orphan':
        receipt_path.unlink()
    elif damage == 'missing_daily':
        path.unlink()
    elif damage == 'missing_daily_hash':
        del receipt['artifact_sha256']['chenquant_daily.json']
        receipt_path.write_text(json.dumps(receipt))
    else:
        if damage in ('changed_action', 'rehashed_action'):
            daily['paper_action'] = 'BUY'
        else:
            daily['position']['quantity'] += 100
        path.write_text(json.dumps(daily))
        if damage in ('rehashed_quantity', 'rehashed_action'):
            receipt['artifact_sha256']['chenquant_daily.json'] = hashlib.sha256(path.read_bytes()).hexdigest()
            receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        load_paper_context(tmp_path / 'state', TODAY, daily['facts_identity'])


def test_paper_readback_matches_published_lifecycle_including_held_and_exited_lots(tmp_path):
    from pathlib import Path
    from app.services.phase2_research_daily import load_paper_context
    from app.services.phase2_option_c_daily import run_daily_once
    from app.services.phase2_option_c_release_fixture import build_release_lifecycle_inputs
    from tests.test_phase2_research_panel import snapshot
    root = tmp_path / 'state'
    quantities = []
    for day_input in build_release_lifecycle_inputs():
        result = run_daily_once(Path(__file__).parents[2], root, day_input)
        before = snapshot(root)
        context = load_paper_context(root, day_input.trade_date, day_input.facts_sha256,
                                     day_input.input_identity)
        assert context == {'paper_action': result.daily.paper_action,
                           'quantity': result.daily.position.quantity}
        assert snapshot(root) == before
        quantities.append(context['quantity'])
    assert quantities == [0, 100, 100, 0]
