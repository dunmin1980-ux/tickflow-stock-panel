"""Read-only single-stock research using validated daily evidence and builtin rules."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from app.backtest.matrix import (
    MarketDataMatrix,
    build_market_data_matrix,
    matrix_feature,
    valid_ewm_adjust_false,
)
from app.services.phase2_visual_daily_input import (
    VisualDailyInputError,
    load_validated_daily_bundle,
)
from app.strategy.builtin import (
    boll_breakout,
    bullish_alignment,
    macd_golden,
    pullback_to_support,
    volume_price_surge,
)

BUILTINS = (macd_golden, bullish_alignment, boll_breakout,
            volume_price_surge, pullback_to_support)


def _condition(label: str, required: str, passed: bool, **values: float) -> dict[str, Any]:
    available = all(math.isfinite(float(v)) for v in values.values())
    return {
        "label": label,
        "required": required,
        "actual": " / ".join(f"{k}={v:.4f}" if math.isfinite(float(v)) else f"{k}=缺失"
                             for k, v in values.items()),
        "passed": bool(passed) if available else None,
    }


def _test(label, required, observed, threshold, operator, unit="QFQ_PRICE", *,
          passed=None, historical=False, **values):
    bounds = threshold if isinstance(threshold, list) else [threshold]
    available = all(math.isfinite(float(v)) for v in [observed, *bounds])
    margin = None
    if available:
        if operator == "between":
            margin = min(observed - bounds[0], bounds[1] - observed)
        else:
            margin = observed - bounds[0] if operator in ("gt", "gte") else bounds[0] - observed
        if passed is None:
            passed = margin >= 0 if operator in ("gte", "lte") else margin > 0
    result = _condition(label, required, bool(passed), **values)
    result.update({
        "observed": float(observed) if available else None,
        "threshold": ([float(v) for v in bounds] if operator == "between" else float(bounds[0])) if available else None,
        "operator": operator, "unit": unit,
        "margin": float(margin) if margin is not None else None,
        "gap": max(0., -float(margin)) if margin is not None else None,
        "temporal_scope": "PREVIOUS_DAY" if historical else "CURRENT_DAY",
        "passed": bool(passed) if available else None,
    })
    return result


def _status(conditions: list[dict], row_count: int) -> str:
    if row_count < 60 or any(c["passed"] is None for c in conditions):
        return "DATA_UNAVAILABLE"
    return "MATCHED" if all(c["passed"] for c in conditions) else "NOT_MATCHED"


def evaluate_strategies(market: MarketDataMatrix) -> list[dict[str, Any]]:
    """Evaluate signals only; never invoke a backtest matcher or a trading ledger."""
    if market.symbols != ("000403.SZ",) or market.shape[0] < 2:
        raise VisualDailyInputError("RESEARCH_MARKET_SCOPE_INVALID")
    features = {name: matrix_feature(market, name) for name in (
        "close", "open", "ma5", "ma10", "ma20", "ma60", "boll_upper",
        "boll_lower", "vol_ratio_5d", "momentum_20d",
    )}
    valid = np.isfinite(market.close)
    dif = (valid_ewm_adjust_false(market.close, valid, span=12)
           - valid_ewm_adjust_false(market.close, valid, span=26))
    features["dif"] = dif
    features["dea"] = valid_ewm_adjust_false(dif, np.isfinite(dif), span=9)
    defaults = {m.META["id"]: {p["id"]: p["default"] for p in m.META["params"]}
                for m in BUILTINS}
    proximity = defaults["pullback_to_support"]["ma_proximity"]
    # Retain builtin array precision at strict price/volume boundaries.
    nearby_ma20 = ((features["close"] > features["ma20"] * (1.0 - proximity))
                  & (features["close"] < features["ma20"] * (1.0 + proximity)))
    low_volume = features["vol_ratio_5d"] < defaults["pullback_to_support"]["vol_ratio_max"]

    def conditions(index: int) -> dict[str, list[dict]]:
        def v(name: str, offset: int = 0) -> float:
            i = index + offset
            return float(features[name][i, 0]) if i >= 0 else float("nan")

        def above(label: str, left: str, right: str) -> dict:
            return _test(label, f"{left} > {right}", v(left), v(right), "gt",
                         **{left: v(left), right: v(right)})

        def volume(minimum: float) -> dict:
            threshold = float(np.asarray(minimum, dtype=features["vol_ratio_5d"].dtype))
            return _test("量能放大", f"当日量 / 前5日均量 ≥ {minimum:g}",
                         v("vol_ratio_5d"), threshold, "gte", "RATIO", 量比=v("vol_ratio_5d"))

        momentum = _test("20日动量", "20日涨幅 > 0%", v("momentum_20d") * 100,
                         0., "gt", "PERCENTAGE_POINTS", 百分比=v("momentum_20d") * 100)
        return {
            "macd_golden": [
                above("今日 MACD", "dif", "dea"),
                _test("前一交易日 MACD", "前日 DIF ≤ DEA，确认当日交叉",
                      v("dif", -1), v("dea", -1), "lte", historical=True,
                      DIF=v("dif", -1), DEA=v("dea", -1)),
                volume(defaults["macd_golden"]["vol_ratio_min"]),
            ],
            "bullish_alignment": [above("短期均线", "ma5", "ma10"),
                                  above("中期均线", "ma10", "ma20"),
                                  above("长期均线", "ma20", "ma60"), momentum],
            "boll_breakout": [above("收盘位置", "close", "boll_upper"),
                              volume(defaults["boll_breakout"]["vol_ratio_min"])],
            "volume_price_surge": [
                above("今日站上 MA20", "close", "ma20"),
                _test("前日位置", "前日收盘 ≤ 前日 MA20，确认当日突破",
                           v("close", -1), v("ma20", -1), "lte", historical=True,
                           收盘=v("close", -1), MA20=v("ma20", -1)),
                volume(defaults["volume_price_surge"]["vol_ratio_min"]),
                above("阳线", "close", "open"),
            ],
            "pullback_to_support": [
                _test("MA20 附近", f"MA20 × {1-proximity:g} < 收盘 < MA20 × {1+proximity:g}",
                           v("close"), [float((features["ma20"] * (1-proximity))[index, 0]),
                                        float((features["ma20"] * (1+proximity))[index, 0])],
                           "between", passed=nearby_ma20[index, 0],
                           收盘=v("close"), MA20=v("ma20")),
                _test("量能收缩", f"当日量 / 前5日均量 < {defaults['pullback_to_support']['vol_ratio_max']:g}",
                           v("vol_ratio_5d"), float(np.asarray(defaults['pullback_to_support']['vol_ratio_max'],
                                                             dtype=features['vol_ratio_5d'].dtype)),
                           "lt", "RATIO", passed=low_volume[index, 0],
                           量比=v("vol_ratio_5d")),
                above("MA60 上方", "close", "ma60"), momentum,
            ],
            "boll_lower_reclaim": [
                _test("前日触及下轨", "前日收盘 ≤ 前日布林下轨",
                           v("close", -1), v("boll_lower", -1), "lte", historical=True,
                           收盘=v("close", -1), 下轨=v("boll_lower", -1)),
                above("今日回到下轨上方", "close", "boll_lower"),
                _test("收盘回升", "今日收盘 > 前日收盘", v("close"), v("close", -1), "gt",
                           今日=v("close"), 前日=v("close", -1)),
            ],
        }

    last = market.shape[0] - 1
    current, previous = conditions(last), conditions(last-1)
    rows = []
    metadata = [(m.META["id"], m.META["name"], m.META["description"], m) for m in BUILTINS]
    metadata.append(("boll_lower_reclaim", "布林下轨回升",
                     "前日收盘不高于下轨，今日重回下轨上方且收盘回升", None))
    for sid, name, description, module in metadata:
        now = _status(current[sid], last+1)
        before = _status(previous[sid], last)
        risk = None
        if module is not None:
            signals = module.MATRIX_STRATEGY.compute_signals(market, defaults[sid])
            # Explanations must agree with the existing strategy, including edge cases.
            for i, status in ((last, now), (last-1, before)):
                if status != "DATA_UNAVAILABLE" and bool(signals.entry[i, 0]) != (status == "MATCHED"):
                    raise VisualDailyInputError("RESEARCH_RULE_EXPLANATION_MISMATCH")
            risk = bool(signals.exit[last, 0]) if now != "DATA_UNAVAILABLE" else None
        change = ("UNAVAILABLE" if "DATA_UNAVAILABLE" in (now, before)
                  else "UNCHANGED" if now == before
                  else "NEW_MATCH" if now == "MATCHED" else "MATCH_ENDED")
        rows.append({
            "id": sid, "name": name, "description": description,
            "status": now, "previous_status": before, "change": change,
            "conditions": current[sid],
            "previous_conditions": previous[sid],
            "directional_bias": (
                ("BULLISH" if features['dif'][last, 0] > features['dea'][last, 0]
                 else "BEARISH" if features['dif'][last, 0] < features['dea'][last, 0] else "NEUTRAL")
                if sid == 'macd_golden' else
                ("BULLISH" if all(c['passed'] for c in current[sid]) else
                 "BEARISH" if (features['ma5'][last, 0] < features['ma10'][last, 0] < features['ma20'][last, 0]
                               and features['momentum_20d'][last, 0] < 0) else "NEUTRAL")
                if sid == 'bullish_alignment' else "NEUTRAL"
            ),
            "matched_conditions": sum(c["passed"] is True for c in current[sid]),
            "risk_triggered": risk,
            "calculation_source": (f"{module.__name__}.MATRIX_STRATEGY.compute_signals"
                                   if module else f"{__name__}.evaluate_strategies:boll_lower_reclaim_v1"),
            "parameters": defaults.get(sid, {"boll_period": 20, "stddev": 2, "ddof": 1}),
        })
    return rows


def research_market(qfq: list[dict], raw: list[dict]) -> MarketDataMatrix:
    if len(qfq) != len(raw) or any(a['trade_date'] != b['trade_date'] for a, b in zip(qfq, raw)):
        raise VisualDailyInputError("RESEARCH_DATE_ALIGNMENT_INVALID")
    frame = pl.DataFrame([{
        "date": date.fromisoformat(row["trade_date"]), "symbol": "000403.SZ",
        "open": row["open"], "high": row["high"], "low": row["low"],
        "close": row["close"], "volume": raw[i]["volume"],
    } for i, row in enumerate(qfq)])
    return build_market_data_matrix(frame)


def build_research_panel(input_root: Path, target: date, *, state_root: Path | None = None) -> dict[str, Any]:
    bundle = load_validated_daily_bundle(input_root, target)
    sources = {}
    for name in ("qfq_daily.json", "raw_daily.json"):
        path = input_root / target.isoformat() / name
        if path.is_symlink() or not path.is_file():
            raise VisualDailyInputError("RESEARCH_SOURCE_INVALID")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != bundle.manifest.artifact_sha256[name]:
            raise VisualDailyInputError("RESEARCH_SOURCE_CHANGED")
        sources[name] = json.loads(raw)["rows"]
    qfq, raw = sources["qfq_daily.json"], sources["raw_daily.json"]
    market = research_market(qfq, raw)
    strategies = evaluate_strategies(market)
    ind = bundle.facts["indicators"]
    dif, dea, hist, rsi = (float(ind[k]["value"]) for k in
                          ("macd_dif", "macd_dea", "macd_hist", "rsi6"))
    relation = next(c.object.relation for c in bundle.claims.claims if c.predicate == "macd_dif_vs_dea")
    positive = [
        _condition("MACD 线关系", "DIF > DEA", relation == "ABOVE", DIF=dif, DEA=dea),
        _condition("MACD 柱值", "柱值 > 0", hist > 0, 柱值=hist),
        _condition("RSI6", "RSI6 ≥ 50", rsi >= 50, RSI6=rsi),
    ]
    panel = {
        "status": "READY", "trade_date": target.isoformat(),
        "previous_trade_date": bundle.manifest.previous_trade_date.isoformat(),
        "comparison_basis": "SAME_SNAPSHOT_PREVIOUS_TRADING_DAY",
        "price_basis": "qfq", "volume_basis": "raw_same_source_relative_ratio",
        "source_provider": bundle.manifest.source_provider,
        "source_hashes": bundle.manifest.artifact_sha256,
        "affects_paper_action": False, "can_publish": False, "trading_advice": False,
        "chan_status": "READY",
        "strategies": strategies,
        "matched_count": sum(s["status"] == "MATCHED" for s in strategies),
        "risk_count": sum(s["risk_triggered"] is True for s in strategies),
        "paper_rule": {"signal": bundle.day_input.signal.code,
                       "positive_conditions": positive},
        "vendor_pending": bundle.facts["vendor_pending"],
    }
    from app.services.phase2_research_daily import build_engine, load_paper_context
    context = load_paper_context(state_root, target, bundle.manifest.artifact_sha256['facts.json'],
                                 bundle.day_input.input_identity) if state_root else None
    panel['engine'] = build_engine(panel, qfq, raw, claims_count=len(bundle.claims.claims), paper_context=context)
    return panel
