from __future__ import annotations

import copy
import hashlib
import json

import pytest
from test_strategy_graphics import graphics, snapshot
from test_strategy_graphics import prepared as prepared_fixture

from app.services.phase2_research_daily import normalize_strategy
from app.services.phase2_research_panel import evaluate_strategies, research_market

prepared = prepared_fixture


def test_candles_keep_exact_qfq_ohlc(prepared):
    root, gateway, panel = prepared
    points = graphics(root, panel)['points']
    for point, row in zip(points, gateway.qfq[-30:], strict=True):
        for key in ('open', 'high', 'low', 'close'):
            assert point[key] == row[key]


def test_overlay_keeps_every_canonical_evaluation_and_condition_delta(prepared):
    root, gateway, panel = prepared
    markers = [m for m in graphics(root, panel)['overlay_markers'] if m['kind'] == 'STRATEGY']
    assert len(markers) == 30 * 6
    assert len({m['marker_id'] for m in markers}) == len(markers)
    for end in range(71, 101):
        day = gateway.qfq[end - 1]['trade_date']
        expected = [normalize_strategy(rule, day) for rule in
                    evaluate_strategies(research_market(gateway.qfq[:end], gateway.raw[:end]))]
        actual = [m for m in markers if m['trade_date'] == day]
        for marker, strategy in zip(actual, expected, strict=True):
            assert marker['canonical_status'] == strategy['status']
            assert marker['strategy_name'] == strategy['strategy_name']
            for key in ('conditions_met', 'conditions_total', 'evidence', 'failed_conditions', 'delta_vs_previous'):
                assert marker[key] == strategy[key]
            assert marker['summary']
            assert marker['structure'] is None


def test_chan_is_confirmed_structure_not_fabricated_strategy_progress(prepared):
    root, gateway, panel = prepared
    day, confirmed = (r['trade_date'] for r in gateway.qfq[-3:-1])
    fractal = {'type': 'TOP_FRACTAL', 'date': day, 'confirmed_at': confirmed, 'price': 11.5}
    stroke = {'type': 'STROKE_CANDIDATE', 'direction': 'UP_STROKE', 'start_date': day,
              'end_date': day, 'start_price': 10.0, 'end_price': 11.5,
              'confirmed_at': confirmed, 'source_bar_separation': 2}
    panel['engine']['chan_structure'].update(top_fractals=[fractal], bottom_fractals=[], stroke_candidates=[stroke])
    original = copy.deepcopy(panel)
    markers = [m for m in graphics(root, panel)['overlay_markers'] if m['kind'] == 'CHAN_STRUCTURE']
    assert len(markers) == 2
    assert len({m['marker_id'] for m in markers}) == 2
    for marker, event in zip(markers, (fractal, stroke), strict=True):
        assert marker['trade_date'] == confirmed
        assert marker['strategy_id'] == 'chan_daily_structure'
        assert marker['canonical_status'] == 'NOT_AVAILABLE'
        assert marker['conditions_met'] is marker['conditions_total'] is None
        assert marker['delta_vs_previous'] is None
        assert marker['evidence'] == marker['failed_conditions'] == []
        assert marker['structure'] == event
        assert '未定义策略触发合同' in marker['summary']
    assert panel == original


def test_chan_outside_visible_window_is_not_shifted_into_window(prepared):
    root, gateway, panel = prepared
    event = {'type': 'BOTTOM_FRACTAL', 'date': gateway.qfq[1]['trade_date'],
             'confirmed_at': gateway.qfq[2]['trade_date'], 'price': 9.5}
    panel['engine']['chan_structure'].update(top_fractals=[], bottom_fractals=[event], stroke_candidates=[])
    assert all(m['kind'] != 'CHAN_STRUCTURE' for m in graphics(root, panel)['overlay_markers'])


def test_no_published_paper_record_never_inherits_current_hold(prepared):
    root, _, panel = prepared
    panel['engine']['summary'].update(paper_action='HOLD', hold_explanation=['TODAY ONLY'])
    result = graphics(root, panel)
    assert len(result['paper_records']) == 30
    for day, record in result['paper_records'].items():
        assert record['trade_date'] == day
        assert record['status'] == 'NOT_AVAILABLE'
        assert record['paper_action'] is None
        assert record['hold_explanation'] is None
    assert 'TODAY ONLY' not in json.dumps(result)


def test_published_paper_record_is_date_and_receipt_bound_read_only(tmp_path):
    from test_phase2_visual_workbench import TODAY, FakeDailyGateway, daily_builder, service_for

    gateway = FakeDailyGateway()
    service = service_for(tmp_path, builder=daily_builder(tmp_path, gateway))
    service.run(TODAY)
    before = snapshot(tmp_path)
    result = service.dashboard(TODAY)['research']['graphics']
    records = result['paper_records']
    published = records[TODAY.isoformat()]
    path = tmp_path / 'state' / 'days' / TODAY.isoformat() / 'chenquant_daily.json'
    daily = json.loads(path.read_bytes())
    assert published['status'] == 'PUBLISHED'
    assert published['paper_action'] == daily['paper_action']
    assert published['research_signal'] == daily['research_signal']
    assert published['quantity'] == daily['position']['quantity']
    assert published['source_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert published['source'] == f'days/{TODAY.isoformat()}/chenquant_daily.json'
    assert published['hold_explanation'] is None  # No reason text was published by the MVP.
    assert all(r['status'] == 'NOT_AVAILABLE' for d, r in records.items() if d != TODAY.isoformat())
    assert service.dashboard(TODAY)['research']['graphics'] == result
    assert snapshot(tmp_path) == before
    assert gateway.calls == 1


@pytest.mark.parametrize('corruption', ['daily', 'receipt', 'receipt_list', 'receipt_null', 'symlink'])
def test_corrupt_published_record_is_unavailable_not_hold(tmp_path, corruption):
    from test_phase2_visual_workbench import TODAY, FakeDailyGateway, daily_builder, service_for

    from app.services.phase2_strategy_graphics import build_strategy_graphics
    gateway = FakeDailyGateway()
    service = service_for(tmp_path, builder=daily_builder(tmp_path, gateway))
    panel = service.run(TODAY)['research']
    day = tmp_path / 'state' / 'days' / TODAY.isoformat()
    path = day / ('run_receipt.json' if corruption.startswith('receipt') else 'chenquant_daily.json')
    if corruption == 'symlink':
        backup = tmp_path / 'test-daily.json'
        path.rename(backup)
        path.symlink_to(backup)
    else:
        payload = {'receipt': b'{}', 'receipt_list': b'[]', 'receipt_null': b'null'}
        path.write_bytes(path.read_bytes() + b' ' if corruption == 'daily' else payload[corruption])
    result = build_strategy_graphics(tmp_path / 'inputs', TODAY, panel, tmp_path / 'state')
    record = result['paper_records'][TODAY.isoformat()]
    assert record['status'] == 'NOT_AVAILABLE'
    assert record['paper_action'] is None
    assert record['source_sha256'] is None
