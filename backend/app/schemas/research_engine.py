"""Versioned research-only contracts; no action generation or account mutation."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResearchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ConditionEvidence(ResearchModel):
    label: str
    required: str
    actual: str
    passed: bool | None
    observed: float | None
    threshold: float | list[float] | None
    operator: Literal['gt', 'gte', 'lt', 'lte', 'between']
    unit: Literal['QFQ_PRICE', 'RATIO', 'PERCENTAGE_POINTS']
    margin: float | None
    gap: float | None = Field(ge=0)
    temporal_scope: Literal['CURRENT_DAY', 'PREVIOUS_DAY']


class ConditionDelta(ResearchModel):
    label: str
    previous: float | None
    current: float | None
    value_delta: float | None
    margin_delta: float | None
    change: Literal['IMPROVING', 'WEAKENING', 'UNCHANGED', 'NOT_AVAILABLE']
    unit: str


class StrategyDelta(ResearchModel):
    previous_conditions_met: int
    conditions_met_delta: int
    change: Literal['IMPROVING', 'WEAKENING', 'MIXED', 'UNCHANGED', 'NOT_AVAILABLE']
    conditions: list[ConditionDelta]


class StrategyResearch(ResearchModel):
    strategy_id: Literal['macd_golden', 'bullish_alignment', 'boll_breakout',
                         'volume_price_surge', 'pullback_to_support', 'boll_lower_reclaim']
    strategy_name: str
    trade_date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    timeframe: Literal['1d']
    price_basis: Literal['QFQ']
    status: Literal['TRIGGERED', 'NEAR_TRIGGER', 'NOT_TRIGGERED', 'NOT_AVAILABLE']
    conditions_total: int = Field(gt=0)
    conditions_met: int = Field(ge=0)
    condition_ratio: float = Field(ge=0, le=1)
    evidence: list[ConditionEvidence]
    failed_conditions: list[ConditionEvidence]
    delta_vs_previous: StrategyDelta
    distance_to_trigger: list[ConditionEvidence]
    research_bias: Literal['BULLISH', 'BEARISH', 'NEUTRAL', 'SETUP']
    risk_triggered: bool | None
    calculation_source: str
    parameters: dict[str, bool | float | int]

    @model_validator(mode='after')
    def verify_counts(self):
        if (self.conditions_total != len(self.evidence)
                or self.conditions_met != sum(c.passed is True for c in self.evidence)
                or self.condition_ratio != self.conditions_met / self.conditions_total
                or self.failed_conditions != [c for c in self.evidence if c.passed is not True]
                or self.distance_to_trigger != self.failed_conditions):
            raise ValueError('strategy_condition_contract_mismatch')
        if self.status == 'TRIGGERED' and self.conditions_met != self.conditions_total:
            raise ValueError('trigger_requires_all_conditions')
        return self


Consensus = Literal['STRONG_BULLISH_ALIGNMENT', 'BULLISH_WITH_CONFLICTS', 'MIXED',
                    'BEARISH_WITH_CONFLICTS', 'STRONG_BEARISH_ALIGNMENT', 'SETUP_WATCH', 'INSUFFICIENT_DATA']


class ResearchConfidence(ResearchModel):
    category: Literal['HIGH', 'MODERATE', 'LOW']
    basis: str
    is_probability: Literal[False]


class ResearchDailySummary(ResearchModel):
    trade_date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    symbol: Literal['000403.SZ']
    research_consensus: Consensus
    research_confidence: ResearchConfidence
    paper_action: Literal['BUY', 'HOLD', 'SELL', 'NOT_RUN']
    position_state: Literal['FLAT', 'HOLDING', 'UNKNOWN']
    hold_reason: Literal['FLAT_WAIT', 'POSITION_HOLD', 'N/A', 'NOT_RUN']
    hold_explanation: list[str]
    supporting_factors: list[str]
    limiting_factors: list[str]
    strategy_conflicts: list[str]
    closest_trigger: dict[str, Any] | None
    chan_structure: dict[str, Any]
    changes_vs_previous: list[dict[str, Any]]
    next_observation_conditions: list[str]
    claims_count: int = Field(ge=0)
    data_quality: dict[str, Any]

    @model_validator(mode='after')
    def verify_action_readback(self):
        expected = ('NOT_RUN' if self.paper_action == 'NOT_RUN' else 'N/A' if self.paper_action != 'HOLD'
                    else 'FLAT_WAIT' if self.position_state == 'FLAT' else 'POSITION_HOLD')
        if self.hold_reason != expected or (self.paper_action == 'HOLD' and self.position_state == 'UNKNOWN'):
            raise ValueError('paper_action_readback_inconsistent')
        return self
