"""Synthetic rule tests, not market data or profitability evidence."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D, Context, localcontext
from pathlib import Path

def bars_at(profile="primary_1m", end="2026-11-19T10:00:00Z", close="103", count=15):
    seconds = 60 if profile == "primary_1m" else 300
    end = datetime.fromisoformat(end.replace("Z", "+00:00"))
    result = []
    for i in range(count):
        stop = end - timedelta(seconds=seconds*(count-1-i))
        result.append(dict(start_utc=(stop-timedelta(seconds=seconds)).isoformat(),
                           end_utc=stop.isoformat(), open="100", high="101", low="99", close="100"))
    result[-1].update(high="104", close=close)
    return result

def fraction(n, d):
    with localcontext(Context(prec=50)):
        return D(n)/D(d)

class SignalTests(unittest.TestCase):
    def evaluate(self, bars=None, decision="2026-11-19T10:00:00Z", profile="primary_1m", **kwargs):
        from personal_trading.tradelocker.strategy import evaluate_signal
        return evaluate_signal(bars if bars is not None else bars_at(profile), decision, profile=profile, **kwargs)

    def test_minute_profiles_shift_five_bar_channel_and_arithmetic_atr(self):
        for profile in ("primary_1m", "comparator_5m"):
            bars = bars_at(profile, count=20)
            bars[1]["high"] = "500"  # outside both channel and last 14 TRs
            result = self.evaluate(bars, profile=profile)
            self.assertEqual(result.direction, "LONG")
            self.assertEqual(result.upper, D("101"))
            self.assertEqual(result.atr, fraction(31, 14))
            self.assertEqual(result.rule_version, "xau-minute-breakout/1.0.0")
            self.assertFalse(result.executable)

    def test_warmup_strict_equality_short_zero_atr_and_gap_tr(self):
        self.assertIn("WARMUP_15_CLOSED_BARS", self.evaluate(bars_at(count=14)).reasons)
        for equal in ("101", "99"):
            self.assertEqual(self.evaluate(bars_at(close=equal)).direction, "NO_SIGNAL")
        bars = bars_at(close="98")
        bars[-1].update(low="97")
        self.assertEqual(self.evaluate(bars).direction, "SHORT")
        for b in bars:
            b.update(open="100", high="100", low="100", close="100")
        self.assertIn("ATR_NONPOSITIVE", self.evaluate(bars).reasons)
        bars = bars_at()
        bars[-2].update(open="110", high="111", low="109", close="110")
        bars[-1].update(open="120", high="122", low="119", close="121")
        self.assertEqual(self.evaluate(bars).atr, fraction(47, 14))

    def test_invalid_bars_and_unclassified_gaps_refuse_without_exceptions(self):
        import copy
        for case in ("gap", "duplicate", "unordered", "nan", "inf", "negative", "zero", "float", "bool", "ohlc", "missing", "naive", "misaligned", "duration", "unfinal", "revised", "missing_tail"):
            with self.subTest(case=case):
                bars = copy.deepcopy(bars_at())
                if case == "gap": del bars[7]
                elif case == "duplicate": bars.insert(7, dict(bars[7]))
                elif case == "unordered": bars[6], bars[7] = bars[7], bars[6]
                elif case in ("nan", "inf", "negative", "zero", "float", "bool"):
                    bars[0]["high"] = {"nan":"NaN", "inf":"Infinity", "negative":"-1", "zero":"0", "float":101.0, "bool":True}[case]
                elif case == "ohlc": bars[0]["open"] = "102"
                elif case == "missing": del bars[0]["open"]
                elif case == "naive": bars[0]["start_utc"] = "2026-11-19T09:45:00"
                elif case == "misaligned": bars[0]["start_utc"] = "2026-11-19T09:45:01Z"
                elif case == "duration": bars[0]["start_utc"] = "2026-11-19T09:44:00Z"
                elif case == "unfinal": bars[-1]["final"] = False
                elif case == "revised": bars[-1]["revised"] = True
                elif case == "missing_tail": bars.pop()
                result = self.evaluate(bars)
                self.assertEqual(result.direction, "NO_SIGNAL")
                self.assertTrue(result.reasons)
        self.assertIn("INVALID_DATA", self.evaluate(decision="2026-11-19T10:00:00-05:00").reasons)
        self.assertIn("INVALID_DATA", self.evaluate(profile="hourly").reasons)

    def test_future_mutation_no_lookahead_stale_tail_and_no_inherited_hours(self):
        bars = bars_at()
        expected = self.evaluate(bars)
        future = {"start_utc":"2026-11-19T10:00:00Z", "end_utc":"2026-11-19T10:01:00Z", "high":"NaN"}
        self.assertEqual(self.evaluate(bars+[future]), expected)
        future.update(high="999999", low="-999", close="garbage")
        self.assertEqual(self.evaluate(bars+[future]), expected)
        forming = bars_at(end="2026-11-19T10:01:00Z")[-1]
        self.assertEqual(self.evaluate(bars+[forming]), expected)
        self.assertIn("MISSING_LATEST_COMPLETED_BAR", self.evaluate(decision="2026-11-19T10:01:00Z").reasons)
        for hour in (0, 6, 7, 17, 23):
            end = f"2026-11-19T{hour:02}:00:00Z"
            self.assertEqual(self.evaluate(bars_at(end=end), decision=end).direction, "LONG")
        with localcontext(Context(prec=6)):
            self.assertEqual(self.evaluate().atr, expected.atr)

    def test_extreme_finite_atr_arithmetic_refuses_without_crashing(self):
        bars=bars_at()
        bars[-1]["high"]="1e999999999"
        result=self.evaluate(bars)
        self.assertEqual(result.direction,"NO_SIGNAL")
        self.assertIn("INVALID_ATR",result.reasons)

if __name__ == "__main__":
    unittest.main()
