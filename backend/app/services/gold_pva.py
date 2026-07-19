import math
from dataclasses import dataclass, replace
from statistics import fmean

PANIC_THRESHOLD = -5.0
GREED_THRESHOLD = 8.0


@dataclass(frozen=True)
class GoldPvaResult:
    legacy_reference_60: float
    native_ema60: float
    p: float
    v: float
    a: float
    state: str
    candidate_signals: tuple[str, ...] = ()


def ema(values: list[float], period: int) -> float:
    multiplier = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = (value - result) * multiplier + result
    return result


def classify_state(p: float) -> str:
    if p <= PANIC_THRESHOLD:
        return "恐慌"
    if p >= GREED_THRESHOLD:
        return "贪婪"
    return "死机"


def build_candidate_signals(result: GoldPvaResult, closes: list[float]) -> tuple[str, ...]:
    signals: list[str] = []
    if result.p <= PANIC_THRESHOLD:
        signals.append("恐慌极端")

    velocities = [new - old for old, new in zip(closes[-4:-1], closes[-3:], strict=True)]
    if len(velocities) == 3 and velocities[0] <= velocities[1] <= velocities[2] and result.a > 0:
        signals.append("惯性衰竭")

    if result.p <= -3 and result.v > 0:
        signals.append("均值回归")

    if result.p >= GREED_THRESHOLD:
        signals.append("贪婪态")

    return tuple(signals)


def calculate_pva(
    *, current_price: float, previous_close: float, completed_closes: list[float]
) -> GoldPvaResult:
    if len(completed_closes) < 60:
        raise ValueError("history_insufficient: need 60 completed closes")

    closes = [float(value) for value in completed_closes[-60:]]
    if (
        not math.isfinite(current_price)
        or not math.isfinite(previous_close)
        or any(not math.isfinite(value) or value <= 0 for value in closes)
        or current_price <= 0
        or previous_close <= 0
    ):
        raise ValueError("invalid_price")

    reference = fmean(closes)
    # The deployed monitor indexes completed-only history as if it included today.
    legacy_history_velocity = closes[-2] - closes[-3]
    v = current_price - previous_close
    p = (current_price / reference - 1) * 100
    a = v - legacy_history_velocity
    base = GoldPvaResult(
        legacy_reference_60=round(reference, 2),
        native_ema60=round(ema(closes, 60), 2),
        p=round(p, 2),
        v=round(v, 2),
        a=round(a, 2),
        state=classify_state(p),
    )
    return replace(base, candidate_signals=build_candidate_signals(base, closes))
