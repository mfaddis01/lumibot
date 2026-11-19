"""Strict local replay envelope. Semantic market-data failures remain NO_SIGNAL."""
import json
from .evaluator import PROFILES, utc
from .planning import nonnegative

REPLAY_SCHEMA = "minute-signal-replay/1.0.0"
LABELS = ("SYNTHETIC_EXAMPLE_NOT_MARKET_DATA", "USER_SUPPLIED_UNQUALIFIED_BARS")
POLICY_FIELDS = ("owner_spread_cap", "adverse_slippage", "roundtrip_fees")
PROOF_FIELDS = ("positions_empty", "pending_empty", "protection_cancelled", "exit_reconciled")


def keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value)-set(required)-set(optional):
        raise ValueError("INVALID_FIELDS")


def loads(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("DUPLICATE_KEY")
            result[key] = value
        return result
    def invalid_number(value):
        raise ValueError("JSON_DECIMAL_STRING_REQUIRED")
    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_number, parse_float=invalid_number)


def validate(document):
    keys(document, ("replay_schema", "label", "profile", "binding", "events"), ("state", "numeric_policy"))
    if document["replay_schema"] != REPLAY_SCHEMA or document["label"] not in LABELS or document["profile"] not in PROFILES:
        raise ValueError("SCHEMA_IDENTITY")
    if not isinstance(document["events"], list):
        raise ValueError("EVENTS_LIST_REQUIRED")
    policy = document.get("numeric_policy", {})
    keys(policy, (), POLICY_FIELDS)
    for value in policy.values():
        if value is not None:
            nonnegative(value)
    for event in document["events"]:
        if not isinstance(event, dict):
            raise ValueError("EVENT_OBJECT_REQUIRED")
        kind = event.get("kind")
        if kind == "bars":
            keys(event, ("kind", "at_utc", "bars"), ("quote_observations",))
            if not isinstance(event["bars"], list):
                raise ValueError("BARS_LIST_REQUIRED")
            for bar in event["bars"]:
                keys(bar, ("start_utc", "end_utc", "open", "high", "low", "close"), ("final", "revised"))
            observations = event.get("quote_observations", [])
            if not isinstance(observations, list):
                raise ValueError("QUOTES_LIST_REQUIRED")
            previous = None
            for observation in observations:
                keys(observation, ("quote", "now_utc", "now_mono_ms"))
                keys(observation["quote"], ("bid", "ask", "receipt_utc", "request_mono_ms", "receipt_mono_ms"))
                now = utc(observation["now_utc"])
                if previous is not None and now < previous:
                    raise ValueError("OUT_OF_ORDER_QUOTES")
                previous = now
        elif kind == "reconcile_flat":
            keys(event, ("kind", "at_utc", "proof"))
            keys(event["proof"], PROOF_FIELDS)
        elif kind == "exposure":
            keys(event, ("kind", "at_utc", "status"))
        elif kind == "time_exit":
            keys(event, ("kind", "at_utc", "actual_fill_utc"))
        else:
            raise ValueError("UNKNOWN_EVENT")
        utc(event["at_utc"])
    return document
