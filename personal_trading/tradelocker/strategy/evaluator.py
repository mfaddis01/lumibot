"""Deterministic closed-bar research observations; never executable orders."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from functools import wraps

VERSION = "xau-minute-breakout/1.0.0"
SCHEMA_VERSION = "2.0.0"
PROFILES = {"primary_1m": 60, "comparator_5m": 300}
UTC = timezone.utc


def deterministic(function):
    @wraps(function)
    def run(*args, **kwargs):
        with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
            return function(*args, **kwargs)
    return run


@dataclass(frozen=True)
class Signal:
    direction: str
    reasons: tuple[str, ...]
    profile: str
    bar_end_utc: datetime | None = None
    decision_utc: datetime | None = None
    upper: Decimal | None = None
    lower: Decimal | None = None
    atr: Decimal | None = None
    rule_version: str = VERSION
    executable: bool = False


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("UTC_REQUIRED")
    return value.astimezone(UTC)


def price(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("DECIMAL_STRING_REQUIRED")
    result = Decimal(value)
    if not result.is_finite() or result <= 0:
        raise ValueError("FINITE_POSITIVE_PRICE_REQUIRED")
    return result


@deterministic
def evaluate_signal(bars, decision_utc, *, profile="primary_1m"):
    decision = None
    try:
        decision = utc(decision_utc)
        seconds = PROFILES[profile]
        usable = []
        previous = None
        for b in bars:
            end = utc(b["end_utc"])
            if end > decision:
                continue  # Not available at decision; never read future OHLC/finality.
            start = utc(b["start_utc"])
            if end-start != timedelta(seconds=seconds) or (start-datetime(1970, 1, 1, tzinfo=UTC)) % timedelta(seconds=seconds):
                raise ValueError("BAR_INTERVAL")
            if b.get("final", True) is not True or b.get("revised", False) is not False:
                raise ValueError("BAR_FINALITY")
            op, high, low, close = (price(b[k]) for k in ("open", "high", "low", "close"))
            if not low <= min(op, close) <= max(op, close) <= high:
                raise ValueError("OHLC")
            if previous is not None and start != previous:
                reason = "UNCLASSIFIED_GAP" if start > previous else "INVALID_DATA"
                return Signal("NO_SIGNAL", (reason,), profile, decision_utc=decision)
            previous = end
            usable.append(b)
        latest = decision - ((decision-datetime(1970, 1, 1, tzinfo=UTC)) % timedelta(seconds=seconds))
        if usable and utc(usable[-1]["end_utc"]) != latest:
            return Signal("NO_SIGNAL", ("MISSING_LATEST_COMPLETED_BAR",), profile, decision_utc=decision)
    except (ValueError, KeyError, TypeError, ArithmeticError, AttributeError):
        return Signal("NO_SIGNAL", ("INVALID_DATA",), profile, decision_utc=decision)
    if len(usable) < 15:
        return Signal("NO_SIGNAL", ("WARMUP_15_CLOSED_BARS",), profile, decision_utc=decision)
    window = usable[-15:]
    highs = [price(b["high"]) for b in window]
    lows = [price(b["low"]) for b in window]
    closes = [price(b["close"]) for b in window]
    upper, lower = max(highs[-6:-1]), min(lows[-6:-1])
    try:
        trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(1, 15)]
        atr = sum(trs, Decimal(0)) / Decimal(14)
        if not atr.is_finite():
            raise ValueError("NONFINITE_ATR")
    except (ArithmeticError, ValueError):
        return Signal("NO_SIGNAL", ("INVALID_ATR",), profile, utc(window[-1]["end_utc"]), decision, upper, lower)
    if atr <= 0:
        return Signal("NO_SIGNAL", ("ATR_NONPOSITIVE",), profile, utc(window[-1]["end_utc"]), decision, upper, lower, atr)
    direction = "LONG" if closes[-1] > upper else "SHORT" if closes[-1] < lower else "NO_SIGNAL"
    return Signal(direction, (), profile, utc(window[-1]["end_utc"]), decision, upper, lower, atr)
