"""Quote-only numerical plans. No execution, sizing, network or synthetic fills."""
from decimal import Decimal
from datetime import timedelta
from .evaluator import PROFILES, deterministic, price, utc
from .qualification import UNRESOLVED


def nonnegative(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("DECIMAL_REQUIRED")
    number = Decimal(value)
    if not number.is_finite() or number < 0:
        raise ValueError("NONNEGATIVE_REQUIRED")
    return number


@deterministic
def inspect_quote(signal, quote, *, now_utc, now_mono_ms, owner_spread_cap=None,
                  adverse_slippage=None, roundtrip_fees=None):
    numeric = []
    blockers = ["SIGNAL_ONLY_NO_EXECUTION_PATH", "SOURCE_QUOTE_AGE_UNKNOWN",
                "NATIVE_PROTECTION_UNQUALIFIED", "POST_FILL_PATH_UNAVAILABLE"]
    blockers += ["unresolved:" + p for p in UNRESOLVED]
    result = dict(executable=False, order_intent=None, numeric_screen_passed=False,
                  planned_entry=None, planned_stop=None, planned_target=None,
                  levels_basis="observed_quote_not_actual_fill", would_block=blockers)
    try:
        now, receipt = utc(now_utc), utc(quote["receipt_utc"])
        send_ms, recv_ms = quote["request_mono_ms"], quote["receipt_mono_ms"]
        if any(type(v) is not int or v < 0 for v in (send_ms, recv_ms, now_mono_ms)) or not send_ms <= recv_ms <= now_mono_ms or receipt > now:
            raise ValueError("CLOCK_ORDER")
        if receipt <= signal.bar_end_utc:
            numeric.append("QUOTE_NOT_AFTER_CLOSE")
        if receipt < signal.decision_utc:
            numeric.append("QUOTE_BEFORE_SIGNAL_AVAILABLE")
        if now >= signal.bar_end_utc + timedelta(seconds=5):
            numeric.append("SIGNAL_EXPIRED")
        if recv_ms-send_ms > 1000:
            numeric.append("QUOTE_RTT_EXCEEDED")
        if now_mono_ms-recv_ms > 1000 or now-receipt > timedelta(seconds=1):
            numeric.append("QUOTE_RECEIPT_AGE_EXCEEDED")
        bid, ask = price(quote["bid"]), price(quote["ask"])
        if ask < bid:
            raise ValueError("CROSSED_QUOTE")
        if signal.direction not in ("LONG", "SHORT") or signal.atr is None or signal.atr <= 0:
            raise ValueError("NO_RAW_SIGNAL")
        spread = ask-bid
        if spread > Decimal("0.20")*signal.atr:
            numeric.append("SPREAD_TO_ATR_EXCEEDED")
        if owner_spread_cap is None:
            numeric.append("ABSOLUTE_SPREAD_CAP_UNKNOWN")
        elif spread > nonnegative(owner_spread_cap):
            numeric.append("ABSOLUTE_SPREAD_CAP_EXCEEDED")
        if adverse_slippage is None or roundtrip_fees is None:
            numeric.append("FRICTION_UNKNOWN")
        else:
            friction = spread + nonnegative(adverse_slippage) + nonnegative(roundtrip_fees)
            if Decimal("1.5")*signal.atr < 3*friction:
                numeric.append("TARGET_TO_FRICTION_TOO_SMALL")
        entry = ask if signal.direction == "LONG" else bid
        sign = 1 if signal.direction == "LONG" else -1
        result.update(planned_entry=entry, planned_stop=entry-sign*signal.atr,
                      planned_target=entry+sign*Decimal("1.5")*signal.atr)
        if min(result["planned_stop"], result["planned_target"]) <= 0:
            numeric.append("NONPOSITIVE_PLANNED_LEVEL")
    except (ValueError, TypeError, KeyError, ArithmeticError, AttributeError):
        numeric.append("INVALID_QUOTE_OR_POLICY")
    result["numeric_screen_passed"] = not numeric
    result["numeric_reasons"] = numeric
    result["would_block"] += numeric
    return result


@deterministic
def time_exit_observation(profile, actual_fill_utc, now_utc):
    """Caller-supplied fill time is unverified. This only measures the deadline.

    It does not round to bar boundaries, synthesize a quote/fill or close exposure.
    """
    if profile not in PROFILES:
        raise ValueError("UNKNOWN_PROFILE")
    fill, now = utc(actual_fill_utc), utc(now_utc)
    if now < fill:
        raise ValueError("OBSERVATION_BEFORE_FILL")
    deadline = fill + timedelta(seconds=300)
    elapsed = now-fill
    elapsed_seconds = Decimal(elapsed.days*86400 + elapsed.seconds) + Decimal(elapsed.microseconds)/1000000
    return dict(deadline_utc=deadline, elapsed_seconds=elapsed_seconds,
                lateness_seconds=max(Decimal(0), elapsed_seconds-300), due=now >= deadline,
                executable=False, order_intent=None,
                would_block=["SIGNAL_ONLY_NO_EXECUTION_PATH", "EXECUTABLE_POST_FILL_QUOTE_PATH_REQUIRED",
                             "NATIVE_PROTECTION_RECONCILIATION_REQUIRED", "FILL_TIME_CALLER_SUPPLIED_UNVERIFIED"])
