"""Serializable offline signal state. Not an execution journal or account proof."""
from dataclasses import asdict, dataclass, fields, replace
from hashlib import sha256
import json
from .evaluator import PROFILES, VERSION, evaluate_signal, price, utc

STATE_SCHEMA = "minute-replay-state/1.0.0"
EXPOSURES = ("FLAT", "OPEN", "PENDING", "AMBIGUOUS", "UNRECONCILED")


@dataclass(frozen=True)
class ReplayState:
    state_schema: str
    rule_version: str
    profile: str
    binding: str
    exposure: str = "UNRECONCILED"
    last_reconciled_exit_utc: str | None = None
    consumed_signal_ids: tuple[str, ...] = ()
    bar_digests: tuple[tuple[str, str], ...] = ()
    evaluated_bar_ends: tuple[str, ...] = ()
    last_decision_utc: str | None = None

    @classmethod
    def new(cls, profile, binding):
        if profile not in PROFILES or not isinstance(binding, str) or not binding or "|" in binding:
            raise ValueError("INVALID_STATE_BINDING")
        return cls(STATE_SCHEMA, VERSION, profile, binding)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def restore(cls, data, *, profile, binding):
        try:
            cls.new(profile, binding)
            if set(data) != {f.name for f in fields(cls)}:
                raise ValueError("STATE_FIELDS")
            for key in ("consumed_signal_ids", "bar_digests", "evaluated_bar_ends"):
                if not isinstance(data[key], (list, tuple)):
                    raise ValueError("STATE_COLLECTION")
            state = cls(**{**data, "consumed_signal_ids": tuple(data["consumed_signal_ids"]),
                           "bar_digests": tuple(tuple(p) for p in data["bar_digests"]),
                           "evaluated_bar_ends": tuple(data["evaluated_bar_ends"])})
            if state.state_schema != STATE_SCHEMA or state.rule_version != VERSION or state.profile != profile or state.binding != binding or state.exposure not in EXPOSURES:
                raise ValueError("STATE_IDENTITY")
            prefix = "|".join((VERSION, profile, binding)) + "|"
            consumed_ends = []
            for sid in state.consumed_signal_ids:
                if not isinstance(sid, str) or not sid.startswith(prefix) or sid.rsplit("|", 1)[-1] not in ("LONG", "SHORT"):
                    raise ValueError("STATE_SIGNAL_ID")
                end = utc(sid[len(prefix):].rsplit("|", 1)[0]).isoformat()
                if end not in state.evaluated_bar_ends or end in consumed_ends:
                    raise ValueError("STATE_CONSUMED_BAR_IDENTITY")
                consumed_ends.append(end)
            if len(set(state.consumed_signal_ids)) != len(state.consumed_signal_ids):
                raise ValueError("STATE_DUPLICATE_ID")
            keys = []
            for end, digest in state.bar_digests:
                utc(end)
                if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    raise ValueError("STATE_DIGEST")
                keys.append(end)
            if len(set(keys)) != len(keys):
                raise ValueError("STATE_DUPLICATE_BAR")
            for end in state.evaluated_bar_ends:
                utc(end)
                if end not in keys:
                    raise ValueError("STATE_EVALUATION_WITHOUT_BAR")
            for time in (state.last_decision_utc, state.last_reconciled_exit_utc):
                if time is not None:
                    utc(time)
            if keys:
                if state.last_decision_utc is None or max(utc(t) for t in keys) > utc(state.last_decision_utc):
                    raise ValueError("STATE_MISSING_DECISION_CLOCK")
            if state.exposure == "FLAT" and state.last_reconciled_exit_utc is None:
                raise ValueError("STATE_FLAT_WITHOUT_RECONCILIATION")
            return state
        except (TypeError, KeyError, ValueError, AttributeError):
            raise ValueError("INCOMPATIBLE_STATE") from None

    def observe_exposure(self, status):
        if status not in EXPOSURES or status == "FLAT":
            raise ValueError("RECONCILIATION_REQUIRED_FOR_FLAT")
        return replace(self, exposure=status)

    def reconcile_flat(self, at, *, positions_empty, pending_empty, protection_cancelled, exit_reconciled):
        if not all(v is True for v in (positions_empty, pending_empty, protection_cancelled, exit_reconciled)):
            raise ValueError("INCOMPLETE_RECONCILIATION")
        at = utc(at)
        prior = [utc(t) for t in (self.last_decision_utc, self.last_reconciled_exit_utc) if t]
        if prior and at < max(prior):
            raise ValueError("OUT_OF_ORDER_RECONCILIATION")
        return replace(self, exposure="FLAT", last_reconciled_exit_utc=at.isoformat())


def observe(state, bars, decision_utc):
    signal = evaluate_signal(bars, decision_utc, profile=state.profile)
    reasons = list(signal.reasons)
    signal_id = None
    decision = signal.decision_utc
    if decision and state.last_decision_utc and decision < utc(state.last_decision_utc):
        reasons.append("OUT_OF_ORDER_DECISION")
    elif decision:
        # A valid observation clock is evidence even without a full signal (or
        # with rejected OHLC). Recording time does not validate/consume a bar.
        state = replace(state, last_decision_utc=decision.isoformat())
    digests = dict(state.bar_digests)
    # Warmup has already passed the evaluator's completed-bar validation; it
    # lacks a signal window, not authoritative observed OHLC. Invalid/incomplete
    # histories must never enter this fingerprint path.
    # Completed OHLC validation is independent of indicator readiness. A flat
    # or arithmetically unrepresentable ATR must not discard observed bar data.
    observation_reasons = {"WARMUP_15_CLOSED_BARS", "ATR_NONPOSITIVE", "INVALID_ATR"}
    if all(reason in observation_reasons for reason in reasons):
        for bar in bars:
            if utc(bar["end_utc"]) > decision:
                continue
            end = utc(bar["end_utc"]).isoformat()
            payload = [utc(bar["start_utc"]).isoformat(), end] + [str(price(bar[k])) for k in ("open", "high", "low", "close")]
            digest = sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
            if end in digests and digests[end] != digest:
                reasons.append("REVISED_OBSERVED_BAR")
                break
            digests[end] = digest
        if all(reason in observation_reasons for reason in reasons):
            state = replace(state, bar_digests=tuple(sorted(digests.items())))
    if not reasons and signal.bar_end_utc:
        if signal.bar_end_utc.isoformat() in state.evaluated_bar_ends:
            reasons.append("DUPLICATE_BAR")
    if not reasons and signal.bar_end_utc:
        state = replace(state,
                        evaluated_bar_ends=state.evaluated_bar_ends+(signal.bar_end_utc.isoformat(),))
    if signal.direction in ("LONG", "SHORT"):
        signal_id = "|".join((VERSION, state.profile, state.binding, signal.bar_end_utc.isoformat(), signal.direction))
        if not reasons:
            state = replace(state, consumed_signal_ids=state.consumed_signal_ids+(signal_id,))
        if state.exposure != "FLAT":
            reasons.append("NOT_RECONCILED_FLAT")
        if state.last_reconciled_exit_utc and signal.bar_end_utc <= utc(state.last_reconciled_exit_utc):
            reasons.append("BAR_NOT_AFTER_RECONCILED_EXIT")
    return dict(state=state, signal=signal, signal_id=signal_id, would_block=reasons,
                state_screen_passed=signal.direction in ("LONG", "SHORT") and not reasons)
