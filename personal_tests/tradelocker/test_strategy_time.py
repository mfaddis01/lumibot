"""Elapsed deadline observations are not close orders or guaranteed exits."""
import unittest
import test_strategy_signal as fixtures

class TimeExitTests(unittest.TestCase):
    def test_300_second_deadline_is_elapsed_not_five_bars_for_both_profiles(self):
        from personal_trading.tradelocker.strategy.planning import time_exit_observation
        for profile in ("primary_1m", "comparator_5m"):
            for now, due, late in (("10:06:59",False,"0"),("10:07:00",True,"0"),("10:07:12",True,"12")):
                result=time_exit_observation(profile,"2026-11-19T10:02:00Z",f"2026-11-19T{now}Z")
                self.assertEqual(result["due"],due)
                self.assertEqual(result["lateness_seconds"],fixtures.D(late))
                self.assertEqual(result["deadline_utc"].isoformat(),"2026-11-19T10:07:00+00:00")
                self.assertFalse(result["executable"])
                self.assertIn("EXECUTABLE_POST_FILL_QUOTE_PATH_REQUIRED",result["would_block"])
        for profile,fill,now in (("hourly","2026-11-19T10:00:00Z","2026-11-19T10:01:00Z"),("primary_1m","2026-11-19T10:00:00Z","2026-11-19T09:59:00Z")):
            with self.assertRaises(ValueError):
                time_exit_observation(profile,fill,now)

if __name__ == "__main__":
    unittest.main()
