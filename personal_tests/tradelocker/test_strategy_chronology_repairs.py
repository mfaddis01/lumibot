"""Synthetic CLI regressions for CR2-01/02/03; no orders or market data."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
from typing import cast
import unittest

ROOT = Path(__file__).resolve().parents[2]


def bars(end="2026-11-19T10:00:00Z", count=15, seconds=60) -> list[dict]:
    finish = datetime.fromisoformat(end.replace("Z", "+00:00"))
    result = []
    for i in range(count):
        at = finish - timedelta(seconds=seconds * (count - 1 - i))
        result.append(dict(start_utc=(at-timedelta(seconds=seconds)).isoformat(),
                           end_utc=at.isoformat(), open="200", high="202", low="198", close="200"))
    result[-1].update(high="205", close="204")
    return result


def quote(at="2026-11-19T10:00:01Z", mono=1100, bid="204", ask="204.1") -> dict:
    return dict(now_utc=at, now_mono_ms=mono,
                quote=dict(bid=bid, ask=ask, receipt_utc=at,
                           request_mono_ms=mono-100, receipt_mono_ms=mono))


def flat():
    return dict(kind="reconcile_flat", at_utc="2026-11-19T09:59:59Z",
                proof=dict(positions_empty=True, pending_empty=True,
                           protection_cancelled=True, exit_reconciled=True))


def document(events, profile="primary_1m") -> dict:
    return dict(replay_schema="minute-signal-replay/1.0.0", label="SYNTHETIC_EXAMPLE_NOT_MARKET_DATA",
                profile=profile, binding="independent-synthetic-XAUUSD.R",
                numeric_policy=dict(owner_spread_cap=".5", adverse_slippage=".1", roundtrip_fees=".1"),
                events=events)


def cli(doc):
    # Actual module CLI in a fresh stdlib-only interpreter. The test launcher can
    # apply inherited kernel socket denial; the implementation has no network path.
    return subprocess.run([sys.executable, "-B", "-S", "-W", "error", "-m",
                           "personal_trading.tradelocker.strategy", "-"],
                          input=json.dumps(doc), text=True, capture_output=True,
                          cwd=ROOT, timeout=10, check=False)


def chronology_document() -> dict:
    later = bars()
    later.append(dict(start_utc="2026-11-19T10:00:00Z", end_utc="2026-11-19T10:01:00Z",
                      open="204", high="208", low="203", close="207"))
    return document([flat(), dict(kind="bars", at_utc="2026-11-19T10:00:00Z", bars=bars(),
                                  quote_observations=[quote("2026-11-19T10:02:00Z", 121100)]),
                     dict(kind="bars", at_utc="2026-11-19T10:01:00Z", bars=later,
                          quote_observations=[quote("2026-11-19T10:01:01Z", 61100, "207", "207.1")])])


class ChronologyRepairs(unittest.TestCase):
    def test_cr201_exact_expired_observation_cli_rejects_backwards_event(self):
        completed = cli(chronology_document())
        self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(json.loads(completed.stderr)["error"], "INVALID_OFFLINE_REPLAY_INPUT")

    def test_cr202_exact_warmup_revision_cli_blocks_changed_atr_history(self):
        before = bars(count=14)
        after = deepcopy(before)
        after[7]["high"] = "250"
        after.append(dict(start_utc="2026-11-19T10:00:00Z", end_utc="2026-11-19T10:01:00Z",
                          open="204", high="208", low="203", close="207"))
        completed = cli(document([flat(), dict(kind="bars", at_utc="2026-11-19T10:00:00Z", bars=before),
                                  dict(kind="bars", at_utc="2026-11-19T10:01:00Z", bars=after,
                                       quote_observations=[quote("2026-11-19T10:01:01Z", 61100, "207", "207.1")])]))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        out = json.loads(completed.stdout)
        self.assertIn("REVISED_OBSERVED_BAR", out["results"][-1]["would_block"])
        self.assertFalse(out["results"][-1]["state_screen_passed"])
        self.assertEqual(out["summary"]["numeric_candidates"], 0)
        self.assertEqual(len(out["state"]["bar_digests"]), 14)
        self.assertEqual(out["state"]["evaluated_bar_ends"], [])
        self.assertEqual(out["state"]["consumed_signal_ids"], [])

    def test_cr203_exact_warmup_restart_cli_uses_own_emitted_clock(self):
        first_doc = document([flat(), dict(kind="bars", at_utc="2026-11-19T10:02:00Z",
                                           bars=bars("2026-11-19T10:02:00Z", count=14))])
        first = cli(first_doc)
        self.assertEqual(first.returncode, 0, first.stderr)
        state = json.loads(first.stdout)["state"]
        # Do not forge/patch state: use exactly the real producer's serialized output.
        second_doc = document([dict(kind="bars", at_utc="2026-11-19T10:00:00Z", bars=bars(),
                                     quote_observations=[quote()])])
        second_doc["state"] = state
        second = cli(second_doc)
        self.assertEqual(second.returncode, 0, second.stderr)
        out = json.loads(second.stdout)
        self.assertIn("OUT_OF_ORDER_DECISION", out["results"][0]["would_block"])
        self.assertFalse(out["results"][0]["state_screen_passed"])
        self.assertEqual(out["summary"]["numeric_candidates"], 0)
        self.assertEqual(state["last_decision_utc"], "2026-11-19T10:02:00+00:00")
        self.assertEqual(out["state"], state)

    def test_warmup_restore_requires_clock_covering_fingerprinted_bars(self):
        from personal_trading.tradelocker.strategy.state import ReplayState, observe
        state = observe(ReplayState.new("primary_1m", "synthetic"), bars(count=14),
                        "2026-11-19T10:00:00Z")["state"]
        assert isinstance(state, ReplayState)
        for clock in (None, "2026-11-19T09:59:00Z"):
            with self.subTest(clock=clock):
                data = state.to_dict()
                data["last_decision_utc"] = clock
                with self.assertRaisesRegex(ValueError, "INCOMPATIBLE_STATE"):
                    ReplayState.restore(data, profile=state.profile, binding=state.binding)

    def test_all_quote_observations_enforce_clock_independently_of_selection(self):
        from personal_trading.tradelocker.strategy.replay import replay
        for mode in ("skipped", "blocked_bar", "after_selected", "after_expiry"):
            with self.subTest(mode=mode):
                doc = chronology_document()
                event = doc["events"][1]
                if mode == "skipped":
                    event["quote_observations"][0]["quote"]["request_mono_ms"] = 130000
                elif mode == "blocked_bar":
                    event["bars"] = bars(count=14)
                elif mode == "after_selected":
                    event["quote_observations"].insert(0, quote())
                else:
                    event["quote_observations"].insert(0, quote("2026-11-19T10:00:05Z", 5100))
                with self.assertRaisesRegex(ValueError, "^OUT_OF_ORDER_EVENTS$"):
                    replay(doc)
        doc = document([flat(), dict(kind="bars", at_utc="2026-11-19T10:00:02Z", bars=bars(),
                                     quote_observations=[quote()])])
        with self.assertRaisesRegex(ValueError, "^OUT_OF_ORDER_EVENTS$"):
            replay(doc)

    def test_chronological_later_event_and_first_quote_prices_remain_independent(self):
        from personal_trading.tradelocker.strategy.replay import replay
        doc = chronology_document()
        doc["events"][1]["quote_observations"] = [quote("2026-11-19T10:00:05Z", 5100)]
        result = cast(dict, replay(doc))
        self.assertEqual(result["summary"]["numeric_candidates"], 1)
        self.assertIn("SIGNAL_EXPIRED", result["results"][1]["would_block"])
        # Timestamp admission of an unselected later quote must never inspect
        # its prices or use them in the first numerical plan.
        event = dict(kind="bars", at_utc="2026-11-19T10:00:00Z", bars=bars(),
                     quote_observations=[quote(), quote("2026-11-19T10:00:02Z", 2100)])
        baseline = cast(dict, replay(document([flat(), event])))
        event["quote_observations"][1]["quote"].update(bid=object(), ask=object())
        changed = cast(dict, replay(document([flat(), event])))
        self.assertEqual(changed, baseline)
        self.assertEqual(changed["results"][1]["selected_quote_index"], 0)

    def test_both_profiles_warmup_restore_progress_and_revision_retention(self):
        from personal_trading.tradelocker.strategy.state import ReplayState, observe
        for profile, seconds in (("primary_1m", 60), ("comparator_5m", 300)):
            with self.subTest(profile=profile):
                before = bars(count=14, seconds=seconds)
                state = observe(ReplayState.new(profile, "synthetic"), before,
                                "2026-11-19T10:00:00Z")["state"]
                assert isinstance(state, ReplayState)
                self.assertEqual(len(state.bar_digests), 14)
                self.assertEqual(state.evaluated_bar_ends, ())
                self.assertEqual(state.consumed_signal_ids, ())
                state = ReplayState.restore(state.to_dict(), profile=profile, binding="synthetic")
                altered = deepcopy(before)
                altered[7]["high"] = "250"
                revised = observe(state, altered, "2026-11-19T10:00:00Z")
                self.assertIn("WARMUP_15_CLOSED_BARS", revised["would_block"])
                self.assertIn("REVISED_OBSERVED_BAR", revised["would_block"])
                self.assertEqual(revised["state"], state)
                rewind = observe(state, before, "2026-11-19T09:59:59Z")
                self.assertIn("OUT_OF_ORDER_DECISION", rewind["would_block"])
                self.assertEqual(rewind["state"], state)
                end = "2026-11-19T10:01:00Z" if seconds == 60 else "2026-11-19T10:05:00Z"
                after = before + [dict(start_utc="2026-11-19T10:00:00Z", end_utc=end,
                                       open="204", high="208", low="203", close="207")]
                state = state.reconcile_flat("2026-11-19T10:00:00Z",
                                             positions_empty=True, pending_empty=True,
                                             protection_cancelled=True, exit_reconciled=True)
                valid = observe(state, after, end)
                self.assertTrue(valid["state_screen_passed"])
                final_state = valid["state"]
                assert isinstance(final_state, ReplayState)
                self.assertEqual(len(final_state.evaluated_bar_ends), 1)
                self.assertEqual(len(final_state.consumed_signal_ids), 1)
                self.assertEqual(len(final_state.bar_digests), 15)
                restored = ReplayState.restore(final_state.to_dict(), profile=profile, binding="synthetic")
                self.assertIn("DUPLICATE_BAR", observe(restored, after, end)["would_block"])

    def test_future_and_invalid_ohlc_do_not_become_authoritative_or_consumed(self):
        from personal_trading.tradelocker.strategy.state import ReplayState, observe
        future = dict(end_utc="2026-11-19T10:01:00Z", start_utc=object(), final=False,
                      revised=True, open=object(), high=None, low="NaN", close=object())
        for profile, seconds in (("primary_1m", 60), ("comparator_5m", 300)):
            with self.subTest(profile=profile):
                original = bars(count=14, seconds=seconds)
                initial = ReplayState.new(profile, "synthetic")
                baseline = observe(initial, original, "2026-11-19T10:00:00Z")
                with_future = observe(initial, original+[future], "2026-11-19T10:00:00Z")
                self.assertEqual(with_future, baseline)
                for field, value in (("final", False), ("revised", True), ("high", "0"),
                                     ("low", "999"), ("close", "NaN"), ("start_utc", "not-utc")):
                    bad = deepcopy(original)
                    bad[7][field] = value
                    rejected = observe(initial, bad, "2026-11-19T10:00:00Z")
                    self.assertIn("INVALID_DATA", rejected["would_block"])
                    self.assertFalse(rejected["state_screen_passed"])
                    state = rejected["state"]
                    assert isinstance(state, ReplayState)
                    self.assertEqual(state.bar_digests, ())
                    self.assertEqual(state.evaluated_bar_ends, ())
                    self.assertEqual(state.consumed_signal_ids, ())
                    self.assertEqual(state.last_decision_utc, "2026-11-19T10:00:00+00:00")
                    self.assertEqual(observe(state, original, "2026-11-19T10:00:00Z")["state"],
                                     baseline["state"])


if __name__ == "__main__":
    unittest.main()
