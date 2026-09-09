"""Deterministic aggregation and daily publication of the existing research rules."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from app.schemas.research_engine import ResearchDailySummary, StrategyResearch
from app.services.phase2_option_c_state import OptionCStateStore


class _ReadOnlyPaperSnapshots(OptionCStateStore):
    def __init__(self, root: Path):
        # Reuse publication validation without the state-store constructor's mkdir/chmod.
        self.root = root
        self.days_root = root / 'days'


def normalize_strategy(rule: dict, trade_date: str) -> dict:
    evidence = rule['conditions']
    failed = [c for c in evidence if c['passed'] is not True]
    met = len(evidence) - len(failed)
    status = ('NOT_AVAILABLE' if rule['status'] == 'DATA_UNAVAILABLE'
              else 'TRIGGERED' if not failed else 'NEAR_TRIGGER'
              if len(failed) == 1 and met / len(evidence) >= .5
              and failed[0]['temporal_scope'] == 'CURRENT_DAY' else 'NOT_TRIGGERED')
    changes = []
    for current, previous in zip(evidence, rule['previous_conditions']):
        delta = None if None in (current['margin'], previous['margin']) else current['margin'] - previous['margin']
        changes.append({
            'label': current['label'], 'previous': previous['observed'], 'current': current['observed'],
            'value_delta': None if None in (current['observed'], previous['observed']) else current['observed'] - previous['observed'],
            'margin_delta': delta, 'unit': current['unit'],
            'change': 'NOT_AVAILABLE' if delta is None else 'IMPROVING' if delta > 0
            else 'WEAKENING' if delta < 0 else 'UNCHANGED',
        })
    directions = {c['change'] for c in changes} - {'UNCHANGED'}
    delta_status = ('NOT_AVAILABLE' if 'NOT_AVAILABLE' in directions else
                    'MIXED' if len(directions) > 1 else next(iter(directions), 'UNCHANGED'))
    bias = rule['directional_bias']
    if status == 'NOT_AVAILABLE':
        bias = 'NEUTRAL'
    elif rule['id'] not in ('macd_golden', 'bullish_alignment'):
        bias = ('SETUP' if rule['id'] == 'pullback_to_support' and status in ('TRIGGERED', 'NEAR_TRIGGER')
                else 'BULLISH' if status == 'TRIGGERED' else 'BEARISH' if rule['risk_triggered']
                else 'SETUP' if status == 'NEAR_TRIGGER' else 'NEUTRAL')
    previous_met = sum(c['passed'] is True for c in rule['previous_conditions'])
    return StrategyResearch.model_validate({
        'strategy_id': rule['id'], 'strategy_name': rule['name'], 'trade_date': trade_date,
        'timeframe': '1d', 'price_basis': 'QFQ', 'status': status,
        'conditions_total': len(evidence), 'conditions_met': met, 'condition_ratio': met / len(evidence),
        'evidence': evidence, 'failed_conditions': failed, 'distance_to_trigger': failed,
        'delta_vs_previous': {'previous_conditions_met': previous_met, 'conditions_met_delta': met - previous_met,
                              'change': delta_status, 'conditions': changes},
        'research_bias': bias, 'risk_triggered': rule['risk_triggered'],
        'calculation_source': rule['calculation_source'], 'parameters': rule['parameters'],
    }).model_dump(mode='json')


def closest_trigger(strategies: list[dict]) -> dict | None:
    available = [s for s in strategies if s['status'] not in ('TRIGGERED', 'NOT_AVAILABLE')]
    if not available:
        return None
    result = sorted(available, key=lambda s: (-s['condition_ratio'], len(s['failed_conditions']), s['strategy_id']))[0]
    return {k: result[k] for k in ('strategy_id', 'strategy_name', 'conditions_met',
                                    'conditions_total', 'condition_ratio', 'distance_to_trigger')}


def aggregate_research(strategies: list[dict], chan: dict, *, rsi6: float) -> dict:
    available = [s for s in strategies if s['status'] != 'NOT_AVAILABLE']
    counts = {bias: sum(s['research_bias'] == bias for s in available)
              for bias in ('BULLISH', 'BEARISH', 'NEUTRAL', 'SETUP')}
    supporting, limiting = [], []
    for strategy in available:
        for condition in strategy['evidence']:
            text = f"{strategy['strategy_name']} / {condition['label']}：{condition['actual']}；条件 {condition['required']}"
            if condition['passed'] and condition['temporal_scope'] == 'CURRENT_DAY':
                supporting.append(text)
            elif not condition['passed']:
                limiting.append(text)
    (supporting if rsi6 >= 50 else limiting).append(f"RSI6={rsi6:.4f}；中性轴阈值 ≥ 50")
    conflicts = []
    bears = [s for s in available if s['research_bias'] == 'BEARISH']
    positives = [s for s in available if s['research_bias'] in ('BULLISH', 'SETUP')]
    for bear in bears:
        for positive in positives:
            conflicts.append(f"{bear['strategy_name']}方向偏弱，与{positive['strategy_name']}的偏强/形态观察存在分歧")
    if bears and rsi6 >= 50:
        conflicts.append('方向指标偏弱，但 RSI6 已达到 50；方向与相对强弱未同向')
    if counts['BULLISH'] and rsi6 < 50:
        conflicts.append('部分策略偏强，但 RSI6 仍低于 50')
    structure = chan['current_structure']
    if structure == 'UP_STRUCTURE' and bears:
        conflicts.append('确认的日线结构向上，但方向指标偏弱')
    if structure == 'DOWN_STRUCTURE' and positives:
        conflicts.append('确认的日线结构向下，但出现偏强/形态观察')
    for strategy in available:
        if strategy['risk_triggered'] and strategy['research_bias'] in ('SETUP', 'BULLISH'):
            conflicts.append(f"{strategy['strategy_name']}同时出现形态条件和原策略风险条件")
    by_id = {s['strategy_id']: s for s in available}
    anchors = [by_id.get(s, {}).get('research_bias') for s in ('macd_golden', 'bullish_alignment')]
    if len(available) < 4 or None in anchors:
        consensus, rule = 'INSUFFICIENT_DATA', 'DATA_COVERAGE_GATE'
    elif anchors == ['BULLISH', 'BULLISH']:
        consensus = 'STRONG_BULLISH_ALIGNMENT' if structure == 'UP_STRUCTURE' and not conflicts else 'BULLISH_WITH_CONFLICTS'
        rule = 'MACD_MA_BULLISH_WITH_CHAN_CONFIRMATION_GATE'
        if not conflicts and structure != 'UP_STRUCTURE':
            conflicts.append('MACD 与均线同向偏强，但缠论工程结构尚未确认向上')
    elif anchors == ['BEARISH', 'BEARISH']:
        consensus = 'STRONG_BEARISH_ALIGNMENT' if structure == 'DOWN_STRUCTURE' and not conflicts else 'BEARISH_WITH_CONFLICTS'
        rule = 'MACD_MA_BEARISH_WITH_CHAN_CONFIRMATION_GATE'
        if not conflicts and structure != 'DOWN_STRUCTURE':
            conflicts.append('MACD 与均线同向偏弱，但缠论工程结构尚未确认向下')
    elif conflicts or len(set(anchors)) > 1:
        consensus, rule = 'MIXED', 'DIRECTION_OR_STRUCTURE_CONFLICT'
    elif counts['SETUP']:
        consensus, rule = 'SETUP_WATCH', 'SETUP_WITHOUT_DIRECTIONAL_CONFIRMATION'
    else:
        consensus, rule = 'MIXED', 'NO_DIRECTIONAL_CONFLUENCE'
    confidence = ('LOW' if len(available) < 6 or structure == 'INSUFFICIENT_DATA'
                  else 'HIGH' if consensus.startswith('STRONG_') else 'MODERATE')
    return {
        **{f'{k.lower()}_count': v for k, v in counts.items()},
        'triggered_strategies': [s['strategy_id'] for s in available if s['status'] == 'TRIGGERED'],
        'near_trigger_strategies': [s['strategy_id'] for s in available if s['status'] == 'NEAR_TRIGGER'],
        'supporting_factors': supporting, 'limiting_factors': limiting, 'conflicts': conflicts,
        'closest_trigger': closest_trigger(strategies), 'chan_structure': chan,
        'research_consensus': consensus,
        'research_confidence': {'category': confidence, 'is_probability': False,
                                'basis': f"可用策略 {len(available)}/6；结构 {structure}；分歧 {len(conflicts)} 项。仅指证据一致性，不是胜率。"},
        'rule_trace': [rule, f"MACD={anchors[0]}; MA={anchors[1]}; CHAN={structure}",
                       'COUNTS_ARE_DESCRIPTIVE_NOT_MAJORITY_VOTE; ACTION_UNCHANGED'],
    }


def explain_paper_action(panel: dict, paper_context: dict | None) -> dict:
    if paper_context is None:
        return {'paper_action': 'NOT_RUN', 'position_state': 'UNKNOWN', 'hold_reason': 'NOT_RUN',
                'hold_explanation': ['该研究日期没有已发布模拟动作，不能把未运行解释为 HOLD。']}
    action = paper_context['paper_action']
    quantity = paper_context['quantity']
    if action not in ('BUY', 'HOLD', 'SELL') or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
        raise ValueError('research_paper_context_invalid')
    reason = 'FLAT_WAIT' if action == 'HOLD' and quantity == 0 else 'POSITION_HOLD' if action == 'HOLD' else 'N/A'
    explanation = ['模拟动作读取当日已发布账本；多策略研究不参与现有动作规则。']
    if action == 'HOLD':
        explanation.append('账户空仓，HOLD 表示不产生新模拟订单。' if quantity == 0
                           else '账户有持仓，HOLD 表示维持现有持仓。')
        signal = panel['paper_rule']['signal']
        explanation.append({'MIXED_OBSERVATION': '现有 MACD/RSI6 规则未同向满足，信号为混合观察。',
                            'RISK_OBSERVATION': '现有方向与 RSI6 规则偏弱；空仓时没有可减少的模拟持仓。' if quantity == 0 else '现有规则偏弱；动作以已发布记录为准。',
                            'POSITIVE_OBSERVATION': '现有规则偏强；已有持仓时不会仅因偏强重复形成新订单。'}.get(signal, '现有信号以已发布记录为准。'))
    for condition in panel['paper_rule']['positive_conditions']:
        explanation.append(f"{condition['label']}：{condition['actual']}；{condition['required']}（{'满足' if condition['passed'] else '未满足'}）")
    return {'paper_action': action, 'position_state': 'FLAT' if quantity == 0 else 'HOLDING',
            'hold_reason': reason, 'hold_explanation': explanation}


def load_paper_context(state_root: Path, target: date, facts_sha: str,
                       input_identity: str | None = None) -> dict | None:
    from app.schemas.phase2_option_c import MvpChenQuantDaily, canonical_option_c_bytes
    root = state_root / 'days' / target.isoformat()
    path = root / 'chenquant_daily.json'
    if not root.exists() and not root.is_symlink():
        return None
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError('research_paper_daily_invalid')
    receipt_path = root / 'run_receipt.json'
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ValueError('research_paper_receipt_missing')
    receipt = json.loads(receipt_path.read_bytes())
    hashes = receipt.get('artifact_sha256', {})
    if (not isinstance(hashes, dict)
            or not {'chenquant_daily.json', 'continuous_state.json'}.issubset(hashes)
            or any(not isinstance(name, str) or Path(name).name != name or name in ('.', '..')
                   for name in hashes)):
        raise ValueError('research_paper_receipt_invalid')
    published = _ReadOnlyPaperSnapshots(state_root).find_published_day(
        target, input_identity or receipt.get('input_identity'))
    daily = MvpChenQuantDaily.model_validate_json(path.read_bytes())
    if daily.report_date != target or daily.facts_identity != facts_sha:
        raise ValueError('research_paper_date_or_facts_mismatch')
    state = published.state
    decision = state.decision_ledger[-1]
    if (hashlib.sha256(path.read_bytes()).hexdigest() != hashes['chenquant_daily.json']
            or daily.account_state_sha256 != hashlib.sha256(canonical_option_c_bytes(state)).hexdigest()
            or daily.input_identity != published.input_identity
            or decision.input_identity != published.input_identity
            or decision.facts_sha256 != facts_sha
            or decision.trade_date != target
            or daily.paper_action != decision.paper_action
            or daily.research_signal != decision.research_signal
            or daily.position.quantity != sum(lot.remaining_quantity for lot in state.account.lots)):
        raise ValueError('research_paper_state_mismatch')
    return {'paper_action': daily.paper_action, 'quantity': daily.position.quantity}


def _metric_change(key, label, previous, current, unit, up='IMPROVING', down='WEAKENING'):
    delta = None if None in (previous, current) else current - previous
    return {'id': key, 'label': label, 'previous': previous, 'current': current, 'delta': delta,
            'unit': unit, 'direction': 'NOT_AVAILABLE' if delta is None else up if delta > 0
            else down if delta < 0 else 'UNCHANGED'}


def build_engine(panel: dict, qfq: list[dict], raw: list[dict], *, claims_count: int,
                 paper_context: dict | None) -> dict:
    from app.services.phase2_chan_structure import analyze_chan
    from app.services.phase2_research_panel import evaluate_strategies, research_market
    from app.services.phase2_visual_daily_input import _indicator_values
    current, previous = _indicator_values(raw, qfq), _indicator_values(raw[:-1], qfq[:-1])
    strategies = [normalize_strategy(rule, panel['trade_date']) for rule in panel['strategies']]
    chan = analyze_chan(qfq, date.fromisoformat(panel['trade_date']))
    aggregate = aggregate_research(strategies, chan, rsi6=current['rsi6'])
    previous_date = qfq[-2]['trade_date']
    previous_rules = [normalize_strategy(rule, previous_date) for rule in
                      evaluate_strategies(research_market(qfq[:-1], raw[:-1]))]
    previous_aggregate = aggregate_research(previous_rules, analyze_chan(qfq, date.fromisoformat(previous_date)),
                                            rsi6=previous['rsi6'])
    aggregate['delta_vs_previous'] = {
        'trade_date': previous_date, 'previous_consensus': previous_aggregate['research_consensus'],
        'current_consensus': aggregate['research_consensus'],
        'changed': previous_aggregate['research_consensus'] != aggregate['research_consensus'],
    }
    action = explain_paper_action(panel, paper_context)
    changes = [_metric_change(key, label, previous[key], current[key], unit, up, down) for key, label, unit, up, down in (
        ('macd_hist', 'MACD 柱值', 'QFQ_PRICE', 'IMPROVING', 'WEAKENING'),
        ('rsi6', 'RSI6', 'POINTS', 'RISING', 'FALLING'),
    )]
    ma = next(s for s in strategies if s['strategy_id'] == 'bullish_alignment')
    changes.append(_metric_change('ma_structure', '均线结构满足数', ma['delta_vs_previous']['previous_conditions_met'],
                                  ma['conditions_met'], 'CONDITIONS'))
    volume = panel['strategies'][0]
    changes.append(_metric_change('volume_ratio', '相对量能', volume['previous_conditions'][-1]['observed'],
                                  volume['conditions'][-1]['observed'], 'RATIO', 'EXPANDING', 'CONTRACTING'))
    def boll_position(values, close):
        width = values['boll_upper'] - values['boll_lower']
        return (close - values['boll_lower']) / width if width > 0 else None
    changes.append(_metric_change('boll_position', '布林区间位置', boll_position(previous, qfq[-2]['close']),
                                  boll_position(current, qfq[-1]['close']), 'RATIO', 'RISING', 'FALLING'))
    changes.append({'id': 'chan_structure', 'label': '缠论日线结构', 'previous': None, 'current': None,
                    'delta': None, 'unit': 'STRUCTURE', 'direction': chan['change']})
    next_conditions = []
    for condition in panel['paper_rule']['positive_conditions']:
        if not condition['passed']:
            next_conditions.append(f"观察{condition['label']}是否满足 {condition['required']}；当前 {condition['actual']}")
    closest = aggregate['closest_trigger']
    if closest:
        for condition in closest['distance_to_trigger']:
            prefix = '后续交易日重新确认' if condition['temporal_scope'] == 'PREVIOUS_DAY' else '观察'
            next_conditions.append(f"{prefix}{closest['strategy_name']} / {condition['label']}：{condition['required']}；当前 {condition['actual']}")
    next_conditions.append('观察后续收盘是否确认新的日线分型；未确认结构不倒填到过去。')
    summary = {
        'trade_date': panel['trade_date'], 'symbol': '000403.SZ', **action,
        'research_consensus': aggregate['research_consensus'], 'research_confidence': aggregate['research_confidence'],
        'supporting_factors': aggregate['supporting_factors'], 'limiting_factors': aggregate['limiting_factors'],
        'strategy_conflicts': aggregate['conflicts'], 'closest_trigger': closest,
        'chan_structure': chan, 'changes_vs_previous': changes,
        'next_observation_conditions': next_conditions, 'claims_count': claims_count,
        'data_quality': {'source_provider': panel['source_provider'], 'price_basis': 'QFQ',
                         'source_hashes': panel['source_hashes'], 'vendor_pending': panel['vendor_pending'],
                         'claims_status': 'VALID', 'financial_data': 'NOT_CONNECTED', 'material_news': 'NOT_CONNECTED'},
    }
    summary = ResearchDailySummary.model_validate(summary).model_dump(mode='json')
    engine = {
        'schema_version': 1, 'symbol': '000403.SZ', 'trade_date': panel['trade_date'], 'timeframe': '1d', 'price_basis': 'QFQ',
        'strategies': strategies, 'chan_structure': chan, 'aggregate': aggregate, 'summary': summary,
        'indicators': {key: current[key] for key in ('macd_dif', 'macd_dea', 'macd_hist', 'rsi6')},
        'indicator_source': 'app.services.phase2_visual_daily_input._indicator_values / app.indicators.pipeline.compute_indicators',
        'safety': {'research_only': True, 'simulation_only': True, 'can_publish': False,
                   'trading_advice': False, 'real_trading': 'DISABLED'},
    }
    engine['daily_markdown'] = render_research_daily(engine)
    research_json_bytes(engine)
    return engine


CONSENSUS_LABELS = {
    'STRONG_BULLISH_ALIGNMENT': '多项同向偏强', 'BULLISH_WITH_CONFLICTS': '偏强但有分歧',
    'MIXED': '强弱混合', 'BEARISH_WITH_CONFLICTS': '偏弱但有分歧',
    'STRONG_BEARISH_ALIGNMENT': '多项同向偏弱', 'SETUP_WATCH': '形态观察', 'INSUFFICIENT_DATA': '数据不足',
}


def render_research_daily(engine: dict) -> str:
    s = engine['summary']
    lines = ['---', 'type: multi-strategy-research', 'source_system: tickflow-stock-panel',
             'data_scope: watchlist-sample', 'can_publish: false', 'trading_advice: false',
             'verification_status: pending', 'timeframe: 1d', 'price_basis: QFQ',
             f"trade_date: {s['trade_date']}", 'symbol: 000403.SZ', '---', '', '# 今日研究结论', '',
             f"{s['trade_date']} · 000403.SZ 派林生物", '',
             f"综合状态：{CONSENSUS_LABELS[s['research_consensus']]}（{s['research_consensus']}）",
             f"研究证据一致性：{s['research_confidence']['category']}；{s['research_confidence']['basis']}",
             f"Paper Action：{s['paper_action']}；HOLD 含义：{s['hold_reason']}", '', '## HOLD 解释', '']
    lines += [f'- {line}' for line in s['hold_explanation']]
    lines += ['', '## 策略矩阵', '', '| 策略 | 状态 | Bias | 满足条件 | 关键缺口 | 今日变化 |',
              '|---|---|---|---|---|---|']
    for r in engine['strategies']:
        gap = '；'.join(f"{c['label']} ({c['actual']})" for c in r['failed_conditions']) or '无'
        lines.append(f"| {r['strategy_name']} | {r['status']} | {r['research_bias']} | {r['conditions_met']}/{r['conditions_total']} | {gap} | {r['delta_vs_previous']['change']} |")
    chan = engine['chan_structure']
    lines.append(f"| 缠论日线结构 | {chan['current_structure']} | 结构观察 | 不适用 | 中枢 DEFERRED | {chan['change']} |")
    for title, key in [('支持因素', 'supporting_factors'), ('限制因素', 'limiting_factors'), ('策略冲突', 'strategy_conflicts')]:
        lines += ['', f'## {title}', ''] + [f'- {line}' for line in s[key] or ['无符合当前规则的项目']]
    lines += ['', '## 缠论日线结构', '', 'CHAN_DAILY_STRUCTURE_V1：确定性工程解释，不代表完整缠论。',
              f"结构：{chan['current_structure']}；较前日：{chan['change']}",
              f"最近确认结构高/低：{chan['structure_high']} / {chan['structure_low']}（QFQ）",
              f"当前收盘：{chan['latest_close']}；相对确认区间：{chan['price_location']}。确认结构不等于当前价格仍在区间内。",
              'CENTRAL_ZONE=DEFERRED；笔仅为 STROKE_CANDIDATE，确认日不早于右侧日线日期。']
    for label, key in [('顶分型', 'top_fractals'), ('底分型', 'bottom_fractals')]:
        for point in chan[key][-3:]:
            lines.append(f"- 最近确认{label}：{point['date']} / {point['price']} QFQ；确认日期 {point['confirmed_at']}")
    for stroke in chan['stroke_candidates'][-3:]:
        lines.append(f"- 候选笔 {stroke['direction']}：{stroke['start_date']} → {stroke['end_date']}；"
                     f"{stroke['start_price']} → {stroke['end_price']} QFQ；确认日期 {stroke['confirmed_at']}")
    lines += ['', '## 与昨日相比', '']
    comparison = engine['aggregate']['delta_vs_previous']
    lines.append(f"综合研究：{comparison['previous_consensus']} → {comparison['current_consensus']}；前日 {comparison['trade_date']}。")
    for change in s['changes_vs_previous']:
        lines.append(f"- {change['label']}：{change['previous']} → {change['current']}；变化 {change['delta']} {change['unit']}；{change['direction']}")
    lines += ['', '## 最接近触发条件', '']
    closest = s['closest_trigger']
    if closest:
        lines.append(f"{closest['strategy_name']}：{closest['conditions_met']}/{closest['conditions_total']}。完成度不是胜率。")
        for c in closest['distance_to_trigger']:
            lines.append(f"- {c['label']}：当前 {c['actual']}；阈值 {c['required']}；数值缺口 {c['gap']} {c['unit']}。严格条件须跨越边界。")
        lines.append('差值只针对当前指标阈值，不是未来价格预测；历史事件条件须等待后续交易日重新确认。')
    else:
        lines.append('无可比较的未触发策略。')
    lines += ['', '## 下一观察条件', ''] + [f'- {line}' for line in s['next_observation_conditions']]
    lines += ['', '## 数据质量', '', f"Claims：VALID / {s['claims_count']}；技术价格口径：QFQ。",
              f"数据来源：{s['data_quality']['source_provider']}；财务/重大消息：NOT_CONNECTED。",
              '量额权威单位和其他供应商 pending 项继续保留；本报告不能替代公告与财务核验。',
              '', 'RESEARCH ONLY · SIMULATION ONLY · NOT INVESTMENT ADVICE',
              '研究结论不改变现有模拟动作，不自动发布。', '']
    return '\n'.join(lines)


def research_json_bytes(engine: dict) -> bytes:
    return (json.dumps(engine, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2) + '\n').encode('utf-8')
