"""Offline quote-screen fixtures; no fills or account qualification."""
import unittest
from decimal import Decimal as D
import test_strategy_signal as fixtures

class PlanningTests(unittest.TestCase):
    def signal(self, short=False):
        bars = fixtures.bars_at(close="98" if short else "103")
        if short:
            bars[-1]["low"] = "97"
        return fixtures.SignalTests().evaluate(bars)

    def quote(self, **changes):
        q = dict(bid="103.00", ask="103.10", receipt_utc="2026-11-19T10:00:01Z", request_mono_ms=1000, receipt_mono_ms=1100)
        q.update(changes)
        return q

    def plan(self, signal=None, quote=None, **kw):
        from personal_trading.tradelocker.strategy.planning import inspect_quote
        defaults = dict(now_utc="2026-11-19T10:00:01Z", now_mono_ms=1100, owner_spread_cap="0.5", adverse_slippage="0.10", roundtrip_fees="0.10")
        defaults.update(kw)
        return inspect_quote(signal or self.signal(), quote if quote is not None else self.quote(), **defaults)

    def test_quote_sides_frozen_atr_and_null_qualifications_never_intents(self):
        for short in (False, True):
            signal = self.signal(short)
            p = self.plan(signal)
            self.assertTrue(p["numeric_screen_passed"])
            self.assertFalse(p["executable"])
            self.assertIsNone(p["order_intent"])
            self.assertEqual(p["planned_entry"], D("103") if short else D("103.1"))
            with fixtures.localcontext(fixtures.Context(prec=50)):
                self.assertEqual(p["planned_stop"], p["planned_entry"] + (signal.atr if short else -signal.atr))
            self.assertIn("unresolved:owner_required_values.quantity_lots", p["would_block"])
            self.assertIn("unresolved:contract_required_values.quantity_to_lot_and_pnl_economics", p["would_block"])
            self.assertIn("SOURCE_QUOTE_AGE_UNKNOWN", p["would_block"])
            self.assertIn("NATIVE_PROTECTION_UNQUALIFIED", p["would_block"])
        p = self.plan(owner_spread_cap=None, adverse_slippage=None, roundtrip_fees=None)
        self.assertFalse(p["numeric_screen_passed"])
        self.assertIn("ABSOLUTE_SPREAD_CAP_UNKNOWN", p["would_block"])
        self.assertIn("FRICTION_UNKNOWN", p["would_block"])
        self.assertEqual(self.signal().direction, "LONG")

    def test_quote_transport_receipt_expiry_boundaries_not_source_age(self):
        cases = [
            ({"receipt_utc":"2026-11-19T10:00:00Z"}, {}, "QUOTE_NOT_AFTER_CLOSE"),
            ({}, {"now_utc":"2026-11-19T10:00:05Z"}, "SIGNAL_EXPIRED"),
            ({"request_mono_ms":0,"receipt_mono_ms":1001}, {"now_mono_ms":1001}, "QUOTE_RTT_EXCEEDED"),
            ({}, {"now_mono_ms":2101}, "QUOTE_RECEIPT_AGE_EXCEEDED"),
            ({}, {"now_mono_ms":1099}, "INVALID_QUOTE_OR_POLICY"),
            ({"receipt_utc":"2026-11-19T10:00:02Z"}, {}, "INVALID_QUOTE_OR_POLICY"),
            ({"ask":"102"}, {}, "INVALID_QUOTE_OR_POLICY"),
        ]
        for q, kw, reason in cases:
            with self.subTest(reason=reason):
                p = self.plan(quote=self.quote(**q), **kw)
                self.assertFalse(p["numeric_screen_passed"])
                self.assertIn(reason, p["would_block"])
        # Local thresholds inclusive; 5s signal deadline exclusive.
        p = self.plan(quote=self.quote(request_mono_ms=100,receipt_mono_ms=1100), now_mono_ms=2100, now_utc="2026-11-19T10:00:02Z")
        self.assertTrue(p["numeric_screen_passed"])
        self.assertIn("SOURCE_QUOTE_AGE_UNKNOWN", p["would_block"])
        late_signal = fixtures.SignalTests().evaluate(decision="2026-11-19T10:00:02Z")
        self.assertIn("QUOTE_BEFORE_SIGNAL_AVAILABLE", self.plan(late_signal)["would_block"])

    def test_spread_and_friction_equality_unknowns_and_nonfinite_policy(self):
        from dataclasses import replace
        signal = replace(self.signal(), atr=D("10"))
        p = self.plan(signal, self.quote(bid="100",ask="102"), owner_spread_cap="2", adverse_slippage="2", roundtrip_fees="1")
        self.assertTrue(p["numeric_screen_passed"])  # spread=0.2 ATR; target=3*friction
        for changes,reason in [({"owner_spread_cap":"1.99"},"ABSOLUTE_SPREAD_CAP_EXCEEDED"),({"roundtrip_fees":"1.01"},"TARGET_TO_FRICTION_TOO_SMALL"),({"adverse_slippage":"NaN"},"INVALID_QUOTE_OR_POLICY")]:
            kw=dict(owner_spread_cap="2", adverse_slippage="2",roundtrip_fees="1"); kw.update(changes)
            self.assertIn(reason,self.plan(signal,self.quote(bid="100",ask="102"),**kw)["would_block"])
        self.assertIn("SPREAD_TO_ATR_EXCEEDED", self.plan(signal,self.quote(bid="100",ask="102.01"))["would_block"])

    def test_nonpositive_raw_levels_refuse_no_stop_widening(self):
        from dataclasses import replace
        signal=replace(self.signal(),atr=D("10"))
        p=self.plan(signal,self.quote(bid="1",ask="1.1"))
        self.assertFalse(p["numeric_screen_passed"])
        self.assertIn("NONPOSITIVE_PLANNED_LEVEL",p["would_block"])
        self.assertLess(p["planned_stop"],0)  # show rejected raw value, never repair/widen

if __name__ == "__main__":
    unittest.main()
