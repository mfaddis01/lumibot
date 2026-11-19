"""Real subprocess/offline JSON replay tests; all bars/quotes are synthetic."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import test_strategy_signal as fixtures

ROOT=Path(__file__).resolve().parents[2]

def payload(profile="primary_1m"):
    return dict(replay_schema="minute-signal-replay/1.0.0", label="SYNTHETIC_EXAMPLE_NOT_MARKET_DATA", profile=profile,
                binding="offline-unbound-XAUUSD.R", events=[dict(kind="bars", at_utc="2026-11-19T10:00:00Z", bars=fixtures.bars_at(profile))])

def denied_cli(document):
    script = '''import runpy, socket
from unittest.mock import patch
with patch.object(socket, "socket", side_effect=AssertionError("SOCKET_DENIED")), patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS_DENIED")), patch.object(socket, "gethostbyname", side_effect=AssertionError("DNS_DENIED")):
    runpy.run_module("personal_trading.tradelocker.strategy", run_name="__main__")
'''
    env={k:v for k,v in os.environ.items() if k not in ("NODE_TEST_CONTEXT", "PYTEST_CURRENT_TEST")}
    return subprocess.run([sys.executable,"-B","-W","error","-c",script,"-"], input=document, text=True,capture_output=True,cwd=ROOT,env=env,check=False)

class ReplayTests(unittest.TestCase):
    def test_runnable_json_cli_denied_network_deterministic_separate_profiles(self):
        for profile in ("primary_1m","comparator_5m"):
            encoded=json.dumps(payload(profile))
            first=denied_cli(encoded)
            self.assertEqual(first.returncode,0,first.stderr)
            self.assertEqual(first.stdout,denied_cli(encoded).stdout)
            out=json.loads(first.stdout)
            self.assertEqual(out["results"][0]["signal"]["direction"],"LONG")
            self.assertFalse(out["execution_enabled"])
            self.assertEqual(out["summary"]["raw_signals"],1)
            self.assertEqual(out["summary"]["eligible_intents"],0)
            self.assertEqual(out["label"],"SYNTHETIC_EXAMPLE_NOT_MARKET_DATA")
            self.assertIsNone(out["performance"])
            self.assertIn("POST_FILL_PATH_UNAVAILABLE",out["would_block"])

    def test_replay_first_fresh_quote_not_best_future_and_restart_no_retry(self):
        from personal_trading.tradelocker.strategy.replay import replay
        doc=payload()
        doc["numeric_policy"]=dict(owner_spread_cap="0.5",adverse_slippage="0.1",roundtrip_fees="0.1")
        doc["events"].insert(0,dict(kind="reconcile_flat",at_utc="2026-11-19T09:59:59Z",proof=dict(positions_empty=True,pending_empty=True,protection_cancelled=True,exit_reconciled=True)))
        def q(second,ask,**more):
            item=dict(now_utc=f"2026-11-19T10:00:0{second}Z",now_mono_ms=second*1000,quote=dict(receipt_utc=f"2026-11-19T10:00:0{second}Z",request_mono_ms=second*1000-100,receipt_mono_ms=second*1000,bid="103",ask=ask))
            item["quote"].update(more)
            return item
        doc["events"][-1]["quote_observations"]=[q(1,"103.10",request_mono_ms=0,receipt_mono_ms=1001),q(2,"103.2"),q(3,"103.01")]
        out=replay(doc)
        self.assertEqual(out["results"][-1]["plan"]["planned_entry"],"103.2")
        self.assertEqual(out["results"][-1]["selected_quote_index"],1)
        self.assertEqual(out["summary"]["numeric_candidates"],1)
        self.assertEqual(out["summary"]["eligible_intents"],0)
        # A consumed bar cannot acquire another candidate after restart.
        doc["state"]=out["state"]
        doc["events"]=doc["events"][-1:]
        repeat=replay(doc)
        self.assertEqual(repeat["summary"]["numeric_candidates"],0)
        self.assertIn("DUPLICATE_BAR",repeat["results"][0]["would_block"])
        self.assertIsNone(repeat["results"][0]["plan"])

    def test_strict_json_schema_refuses_unknown_null_and_arming_flags(self):
        import copy
        for key,value in (("execution_enabled",True),("replay_schema","old"),("profile","H1"),("state",None),("events",None),("label","market-certified"),("numeric_policy",{"adverse_slippage":"NaN"})):
            with self.subTest(key=key):
                doc=payload(); doc[key]=value
                p=denied_cli(json.dumps(doc))
                self.assertEqual(p.returncode,2,p.stdout+p.stderr)
                self.assertEqual(p.stdout,"")
                self.assertEqual(json.loads(p.stderr)["error"],"INVALID_OFFLINE_REPLAY_INPUT")
        text=json.dumps(payload()).replace('"profile": "primary_1m"','"profile": "primary_1m", "profile": "comparator_5m"')
        self.assertEqual(denied_cli(text).returncode,2)
        self.assertEqual(denied_cli(json.dumps(payload()).replace('"103"','NaN')).returncode,2)

    def test_shipped_examples_null_manifest_and_real_input_schema(self):
        from personal_trading.tradelocker.strategy.qualification import UNRESOLVED
        examples = ROOT / 'personal_trading/tradelocker/strategy/examples'
        manifest=json.loads((examples/"spec-manifest.json").read_text())
        self.assertEqual(set(manifest["unresolved_null_paths"]),set(UNRESOLVED))
        schema=json.loads((examples/"replay-input.schema.json").read_text())
        self.assertFalse(schema["additionalProperties"])
        for profile in ("primary_1m","comparator_5m"):
            encoded=(examples/f"synthetic-{profile}.json").read_text()
            response=denied_cli(encoded)
            self.assertEqual(response.returncode,0,response.stderr)
            result=json.loads(response.stdout)
            self.assertEqual(result["summary"],dict(evaluated_bars=2,raw_signals=2,numeric_candidates=2,eligible_intents=0,entry_attempts=0))
            for path in UNRESOLVED:
                self.assertIn("unresolved:"+path,result["would_block"])
            timed=[r for r in result["results"] if r["kind"]=="time_exit"]
            self.assertEqual([r["due"] for r in timed],[False,True])
            self.assertIn("DUPLICATE_BAR",result["results"][-1]["would_block"])

if __name__ == "__main__":
    unittest.main()
