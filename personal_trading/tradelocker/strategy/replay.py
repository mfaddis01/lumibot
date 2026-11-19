"""Local JSON replay only. No adapters, sockets, credentials, or order objects."""
from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal
from .evaluator import VERSION, SCHEMA_VERSION, utc
from .input import REPLAY_SCHEMA, validate
from .planning import inspect_quote, time_exit_observation
from .qualification import UNRESOLVED
from .state import ReplayState, observe


def json_value(value):
    if is_dataclass(value):
        return json_value(asdict(value))
    if isinstance(value, (Decimal, datetime)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    if isinstance(value, dict):
        return {key: json_value(v) for key, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    return value


def replay(document):
    validate(document)
    profile, binding = document["profile"], document["binding"]
    state = ReplayState.restore(document["state"], profile=profile, binding=binding) if "state" in document else ReplayState.new(profile, binding)
    results = []
    counts = dict(evaluated_bars=0, raw_signals=0, numeric_candidates=0, eligible_intents=0, entry_attempts=0)
    previous_time = None
    for event in document["events"]:
        now = utc(event["at_utc"])
        if previous_time is not None and now < previous_time:
            raise ValueError("OUT_OF_ORDER_EVENTS")
        previous_time = now
        # Every supplied quote is an observation in the replay chronology, even
        # if skipped, expired, after the selected quote, or on a blocked bar.
        # Validate timestamps only here; do not inspect later prices to choose
        # a candidate or to make an earlier bar decision.
        for observation in event.get("quote_observations", []):
            observed_at = utc(observation["now_utc"])
            if observed_at < previous_time:
                raise ValueError("OUT_OF_ORDER_EVENTS")
            previous_time = observed_at
        kind = event["kind"]
        if kind == "reconcile_flat":
            state = state.reconcile_flat(now, **event["proof"])
            result = dict(kind=kind, flat_assertion="caller_supplied_not_broker_proof", executable=False)
        elif kind == "exposure":
            state = state.observe_exposure(event["status"])
            result = dict(kind=kind, exposure=state.exposure, executable=False)
        elif kind == "time_exit":
            result = dict(kind=kind, **time_exit_observation(profile, event["actual_fill_utc"], now))
        else:
            prior_state = state
            result = observe(state, event["bars"], now)
            state = result.pop("state")
            counts["evaluated_bars"] += len(state.evaluated_bar_ends)-len(prior_state.evaluated_bar_ends)
            counts["raw_signals"] += len(state.consumed_signal_ids)-len(prior_state.consumed_signal_ids)
            result.update(kind=kind, plan=None, selected_quote_index=None, quote_skips=[])
            if result["state_screen_passed"]:
                for i, observation in enumerate(event.get("quote_observations", [])):
                    plan = inspect_quote(result["signal"], **observation, **document.get("numeric_policy", {}))
                    # The first fresh, two-sided observed quote is the only numerical
                    # candidate. Cost/spread rejection consumes this bar; never shop
                    # later quotes for a better price or retry an unknown order.
                    transport = {"QUOTE_NOT_AFTER_CLOSE", "QUOTE_BEFORE_SIGNAL_AVAILABLE", "QUOTE_RTT_EXCEEDED",
                                 "QUOTE_RECEIPT_AGE_EXCEEDED", "INVALID_QUOTE_OR_POLICY"}
                    if "SIGNAL_EXPIRED" in plan["numeric_reasons"]:
                        result["would_block"].append("SIGNAL_EXPIRED")
                        break
                    if transport.intersection(plan["numeric_reasons"]):
                        result["quote_skips"].append(dict(index=i, reasons=plan["numeric_reasons"]))
                        continue
                    result["plan"], result["selected_quote_index"] = plan, i
                    counts["numeric_candidates"] += int(plan["numeric_screen_passed"])
                    break
                if result["plan"] is None:
                    result["would_block"].append("NO_ELIGIBLE_OBSERVED_QUOTE")
        results.append(result)
    return json_value(dict(replay_schema=REPLAY_SCHEMA, strategy_schema=SCHEMA_VERSION,
        rule_version=VERSION, label=document["label"], profile=state.profile,
        execution_enabled=False, order_intent=None, results=results, state=state.to_dict(),
        summary=counts, performance=None,
        would_block=["SIGNAL_ONLY_NO_EXECUTION_PATH", "POST_FILL_PATH_UNAVAILABLE",
            "NATIVE_PROTECTION_UNQUALIFIED", "SOURCE_QUOTE_AGE_UNKNOWN"]+["unresolved:"+p for p in UNRESOLVED]))
