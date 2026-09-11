from __future__ import annotations

import copy
import hashlib
import json
from datetime import date

import polars as pl
import pytest
from test_phase2_visual_daily_input import TARGET, FakeGateway, _builder
from test_strategy_graphics import snapshot

from app.indicators.pipeline import compute_indicators
from app.schemas.phase2_option_c import SimulationConfig
from app.services.phase2_facts import INDICATOR_COLUMNS, INDICATOR_SPECS
from app.services.phase2_option_c_continuous import new_continuous_state
from app.services.phase2_research_panel import BUILTINS, build_research_panel
from app.services.phase2_strategy_graphics import build_strategy_graphics
from app.services.phase2_visual_daily_input import VisualDailyInputError


@pytest.fixture
def volume_input(tmp_path):
    gateway = FakeGateway(bullish=True)
    for index, row in enumerate(gateway.raw):
        row['volume'] = index * 2.5
    gateway.raw[-2]['volume'] = 0
    _builder(tmp_path, gateway).prepare(TARGET, new_continuous_state(SimulationConfig.default()))
    root = tmp_path / 'inputs'
    return root, gateway, build_research_panel(root, TARGET)


def _rewrite_source(root, panel, name, rows):
    path = root / TARGET.isoformat() / name
    payload = json.loads(path.read_bytes())
    payload['rows'] = rows
    path.write_text(json.dumps(payload))
    panel['source_hashes'][name] = hashlib.sha256(path.read_bytes()).hexdigest()


def test_volume_uses_exact_raw_values_and_dates_without_conversion(volume_input):
    root, gateway, panel = volume_input
    result = build_strategy_graphics(root, TARGET, panel)
    assert len(result['points']) == 30
    assert [(p['trade_date'], p['volume']) for p in result['points']] == [
        (r['trade_date'], r['volume']) for r in gateway.raw[-30:]
    ]
    assert result['points'][0]['volume'] == 175.0
    assert result['points'][-2]['volume'] == 0.0
    assert result['points'][-1]['volume'] == 247.5
    assert result['points'][-1]['volume'] != gateway.qfq[-1]['volume']
    assert result['trade_date'] == result['points'][-1]['trade_date'] == TARGET.isoformat()
    assert result['source_hashes'] == {name: panel['source_hashes'][name]
                                      for name in ('qfq_daily.json', 'raw_daily.json')}
    json.dumps(result, allow_nan=False)


def test_volume_metadata_identifies_raw_source_and_unconfirmed_relative_units(volume_input):
    root, _, panel = volume_input
    result = build_strategy_graphics(root, TARGET, panel)
    assert result['volume_metadata'] == {
        'source': 'raw_daily.json',
        'data_basis': 'RAW',
        'unit': 'relative',
        'source_unit_unconfirmed': True,
    }


def test_volume_averages_match_shared_pipeline_with_full_history_warmup(volume_input):
    root, gateway, panel = volume_input
    result = build_strategy_graphics(root, TARGET, panel)
    frame = pl.DataFrame([{
        'symbol': '000403.SZ', 'date': date.fromisoformat(qfq['trade_date']),
        **{key: float(qfq[key]) for key in ('open', 'high', 'low', 'close')},
        'volume': float(raw['volume']),
    } for qfq, raw in zip(gateway.qfq, gateway.raw, strict=True)])
    expected = compute_indicators(frame, needed={'vol_ma5', 'vol_ma10'}).tail(30).to_dicts()
    for key in ('volume_ma5', 'volume_ma10'):
        assert INDICATOR_SPECS[key]['data_basis'] == 'raw'
        assert [point[key] for point in result['points']] == [
            row[INDICATOR_COLUMNS[key]] for row in expected
        ]
    assert result['points'][0]['volume_ma5'] == 170.0
    assert result['points'][0]['volume_ma10'] == 163.75
    assert result['points'][-1]['volume_ma5'] == 193.5
    assert result['points'][-1]['volume_ma10'] == 211.75


@pytest.mark.parametrize('history_size', [4, 7, 10])
def test_volume_average_warmup_gaps_stay_none(volume_input, history_size):
    root, gateway, panel = volume_input
    for name, rows in (('qfq_daily.json', gateway.qfq), ('raw_daily.json', gateway.raw)):
        _rewrite_source(root, panel, name, rows[-history_size:])
    result = build_strategy_graphics(root, TARGET, panel)
    assert len(result['points']) == history_size
    for index, point in enumerate(result['points']):
        for window in (5, 10):
            value = point[f'volume_ma{window}']
            if index + 1 < window:
                assert value is None
            else:
                rows = gateway.raw[-history_size:][index + 1 - window:index + 1]
                assert value == sum(row['volume'] for row in rows) / window
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('name', ['qfq_daily.json', 'raw_daily.json'])
def test_volume_projection_rejects_changed_source_hash(volume_input, name):
    root, _, panel = volume_input
    path = root / TARGET.isoformat() / name
    payload = json.loads(path.read_bytes())
    payload['rows'][-1]['volume'] += 1
    path.write_text(json.dumps(payload))
    with pytest.raises(VisualDailyInputError, match=r'^GRAPHICS_SOURCE_CHANGED$'):
        build_strategy_graphics(root, TARGET, panel)


@pytest.mark.parametrize('mutation', ['future', 'duplicate', 'unordered', 'misaligned'])
def test_volume_projection_rejects_invalid_dates(volume_input, mutation):
    root, gateway, panel = volume_input
    rows = copy.deepcopy(gateway.qfq)
    if mutation == 'future':
        rows[-1]['trade_date'] = '2099-01-01'
    elif mutation == 'duplicate':
        rows[-2]['trade_date'] = rows[-3]['trade_date']
    elif mutation == 'unordered':
        rows[-3], rows[-2] = rows[-2], rows[-3]
    else:
        rows.pop(0)
    _rewrite_source(root, panel, 'qfq_daily.json', rows)
    error = ('GRAPHICS_DATE_ALIGNMENT_INVALID' if mutation == 'misaligned'
             else 'GRAPHICS_DATES_INVALID')
    with pytest.raises(VisualDailyInputError, match=rf'^{error}$'):
        build_strategy_graphics(root, TARGET, panel)


@pytest.mark.parametrize('value', [None, float('nan'), float('inf')], ids=['none', 'nan', 'inf'])
@pytest.mark.parametrize('name', ['qfq_daily.json', 'raw_daily.json'])
def test_volume_projection_rejects_missing_or_nonfinite_volume(volume_input, name, value):
    root, gateway, panel = volume_input
    rows = copy.deepcopy(gateway.qfq if name == 'qfq_daily.json' else gateway.raw)
    rows[-1]['volume'] = value
    _rewrite_source(root, panel, name, rows)
    with pytest.raises(VisualDailyInputError, match=r'^GRAPHICS_NONFINITE_SOURCE$'):
        build_strategy_graphics(root, TARGET, panel)


def test_volume_projection_preserves_indicator_warmup_nulls(volume_input):
    root, gateway, panel = volume_input
    for name, rows in (('qfq_daily.json', gateway.qfq), ('raw_daily.json', gateway.raw)):
        _rewrite_source(root, panel, name, rows[-80:])
    result = build_strategy_graphics(root, TARGET, panel)
    assert result['points'][0]['volume'] == 175.0
    assert all(point['ma60'] is None for point in result['points'][:9])
    assert result['points'][9]['ma60'] is not None
    assert all(record['paper_action'] is None for record in result['paper_records'].values())
    json.dumps(result, allow_nan=False)


def test_volume_projection_does_not_change_research_rules_or_source_files(volume_input):
    root, gateway, panel = volume_input
    before = snapshot(root)
    original = copy.deepcopy(panel)
    thresholds = copy.deepcopy([module.META for module in BUILTINS])
    result = build_strategy_graphics(root, TARGET, panel)
    assert result['points'][-1]['volume'] == 247.5
    assert result == build_strategy_graphics(root, TARGET, panel)
    assert panel == original == build_research_panel(root, TARGET)
    assert [module.META for module in BUILTINS] == thresholds
    assert snapshot(root) == before
    assert gateway.calls == 1
    assert result['affects_paper_action'] is False
    assert result['can_publish'] is False


def test_volume_projection_leaves_published_paper_and_research_unchanged(tmp_path):
    from test_phase2_visual_workbench import TODAY, FakeDailyGateway, daily_builder, service_for

    gateway = FakeDailyGateway()
    service = service_for(tmp_path, builder=daily_builder(tmp_path, gateway))
    published = service.run(TODAY)
    before = snapshot(tmp_path)
    panel = build_research_panel(tmp_path / 'inputs', TODAY, state_root=tmp_path / 'state')
    original = copy.deepcopy(panel)
    result = build_strategy_graphics(tmp_path / 'inputs', TODAY, panel, tmp_path / 'state')
    assert result['points'][-1]['volume'] == 100099.0
    assert panel == original
    record = result['paper_records'][TODAY.isoformat()]
    assert record['status'] == 'PUBLISHED'
    assert record['paper_action'] == published['latest_daily']['paper_action']
    after = service.dashboard(TODAY)
    assert after['research']['engine'] == published['research']['engine'] == panel['engine']
    for key in ('account', 'positions', 'decisions', 'trades', 'equity_history', 'latest_daily'):
        assert after[key] == published[key]
    assert snapshot(tmp_path) == before
    assert gateway.calls == 1
