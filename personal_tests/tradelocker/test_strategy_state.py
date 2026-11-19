"""Synthetic restart/dedup/reconciled-flat interface, not a broker journal."""
import unittest
import test_strategy_signal as fixtures

class StateTests(unittest.TestCase):
    def test_per_bar_identity_restart_and_later_same_day_after_reconciled_flat(self):
        from personal_trading.tradelocker.strategy.state import ReplayState, observe
        state = ReplayState.new("primary_1m", "synthetic-demo-XAUUSD.R")
        state = state.reconcile_flat("2026-11-19T09:59:59Z", positions_empty=True, pending_empty=True, protection_cancelled=True, exit_reconciled=True)
        first = observe(state, fixtures.bars_at(), "2026-11-19T10:00:00Z")
        self.assertTrue(first["state_screen_passed"])
        self.assertIn("xau-minute-breakout/1.0.0|primary_1m|synthetic-demo-XAUUSD.R|", first["signal_id"])
        state = ReplayState.restore(first["state"].to_dict(), profile="primary_1m", binding="synthetic-demo-XAUUSD.R")
        self.assertIn("DUPLICATE_BAR", observe(state,fixtures.bars_at(),"2026-11-19T10:00:00Z")["would_block"])
        state = state.observe_exposure("OPEN")
        state = state.reconcile_flat("2026-11-19T10:00:30Z", positions_empty=True, pending_empty=True, protection_cancelled=True, exit_reconciled=True)
        bars = fixtures.bars_at()
        bars.append(dict(start_utc="2026-11-19T10:00:00Z", end_utc="2026-11-19T10:01:00Z", open="103", high="106", low="102", close="105"))
        later = observe(state,bars,"2026-11-19T10:01:00Z")
        self.assertTrue(later["state_screen_passed"])
        self.assertNotEqual(later["signal_id"], first["signal_id"])
        self.assertEqual(len(later["state"].consumed_signal_ids),2)

    def test_revisions_direction_changes_old_bars_and_incompatible_restart_refuse(self):
        import copy
        from personal_trading.tradelocker.strategy.state import ReplayState, observe
        state = ReplayState.new("primary_1m", "synthetic")
        first = observe(state,fixtures.bars_at(),"2026-11-19T10:00:00Z")
        state = first["state"]
        altered = fixtures.bars_at(close="98")
        altered[-1]["low"] = "97"
        changed = observe(state,altered,"2026-11-19T10:00:00Z")
        self.assertIn("REVISED_OBSERVED_BAR", changed["would_block"])
        self.assertFalse(changed["state_screen_passed"])
        old = observe(state,fixtures.bars_at(end="2026-11-19T09:59:00Z"),"2026-11-19T09:59:00Z")
        self.assertIn("OUT_OF_ORDER_DECISION", old["would_block"])
        for key, value in (("rule_version","xau-hourly-channel-breakout/1.0.0"),("state_schema","old"),("profile","comparator_5m"),("binding","different"),("exposure","FLATISH"),("consumed_signal_ids",None),("bar_digests",None),("unexpected",True)):
            with self.subTest(key=key):
                data = copy.deepcopy(state.to_dict()); data[key]=value
                with self.assertRaises(ValueError):
                    ReplayState.restore(data,profile="primary_1m",binding="synthetic")

    def test_pending_ambiguous_exit_proof_and_bar_during_open_are_blocked(self):
        from personal_trading.tradelocker.strategy.state import ReplayState, observe
        for status in ("UNRECONCILED","OPEN","PENDING","AMBIGUOUS"):
            state=ReplayState.new("primary_1m","synthetic").observe_exposure(status)
            self.assertIn("NOT_RECONCILED_FLAT",observe(state,fixtures.bars_at(),"2026-11-19T10:00:00Z")["would_block"])
        state=ReplayState.new("primary_1m","synthetic")
        with self.assertRaises(ValueError):
            state.reconcile_flat("2026-11-19T10:00:30Z", positions_empty=True,pending_empty=True,protection_cancelled=False,exit_reconciled=True)
        state=state.reconcile_flat("2026-11-19T10:00:30Z", positions_empty=True,pending_empty=True,protection_cancelled=True,exit_reconciled=True)
        self.assertIn("BAR_NOT_AFTER_RECONCILED_EXIT",observe(state,fixtures.bars_at(),"2026-11-19T10:00:01Z")["would_block"])
        with self.assertRaises(ValueError):
            state.observe_exposure("FLAT")

    def test_zero_atr_observations_retain_fingerprints_across_restart(self):
        from personal_trading.tradelocker.strategy.state import ReplayState, observe
        bars = [dict(b, open='100', high='100', low='100', close='100') for b in fixtures.bars_at()]
        first = observe(ReplayState.new('primary_1m', 'synthetic'), bars, '2026-11-19T10:00:00Z')
        self.assertIn('ATR_NONPOSITIVE', first['would_block'])
        self.assertEqual(len(first['state'].bar_digests), len(bars))
        restored = ReplayState.restore(first['state'].to_dict(), profile='primary_1m', binding='synthetic')
        revised = [dict(b) for b in bars]
        revised[-3]['high'] = '150'
        revised.append(dict(start_utc='2026-11-19T10:00:00Z', end_utc='2026-11-19T10:01:00Z',
                            open='100', high='152', low='100', close='151'))
        result = observe(restored, revised, '2026-11-19T10:01:00Z')
        self.assertIn('REVISED_OBSERVED_BAR', result['would_block'])
        self.assertFalse(result['state_screen_passed'])
        self.assertEqual(result['state'].bar_digests, restored.bar_digests)

    def test_restore_corrupt_consumed_id_cannot_replay_or_flip_same_bar(self):
        import copy
        from personal_trading.tradelocker.strategy.state import ReplayState,observe
        state=observe(ReplayState.new("primary_1m","synthetic"),fixtures.bars_at(),"2026-11-19T10:00:00Z")["state"]
        for key,value in (("evaluated_bar_ends",[]),("last_decision_utc",None),("consumed_signal_ids",list(state.consumed_signal_ids)+[state.consumed_signal_ids[0].replace("LONG","SHORT")])):
            with self.subTest(key=key):
                data=copy.deepcopy(state.to_dict());data[key]=value
                with self.assertRaises(ValueError):
                    ReplayState.restore(data,profile="primary_1m",binding="synthetic")

if __name__ == "__main__":
    unittest.main()
