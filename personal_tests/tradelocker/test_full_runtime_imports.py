"""Full installed-runtime constructor probe contract (dedicated child process)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]


class FullRuntimeImportTests(unittest.TestCase):
    def test_installed_sdk_framework_and_idle_runner_construct_offline(self):
        env = {
            'PATH': '/usr/bin:/bin',

            'TMPDIR': __import__('tempfile').gettempdir(),
        }
        result = subprocess.run([sys.executable, '-B', '-W', 'error', '-m',
                                 'personal_trading.tradelocker.packaging.runtime.probe'],
                                cwd=ROOT, env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        evidence = json.loads(result.stdout)
        self.assertEqual(evidence['framework_distribution'], '4.6.64+source8dbcf8f.1')
        self.assertEqual(evidence['sdk_distribution'], '0.56.2+lumibot.runtime.1')
        self.assertTrue(evidence['installed_framework_import'])
        self.assertTrue(evidence['real_sdk_constructor'])
        self.assertTrue(evidence['real_broker_data_strategy_trader_constructors'])
        self.assertEqual(evidence['external_connections'], 0)
        self.assertEqual(evidence['denied_ipv6_loopback_probes'], 1)
        self.assertEqual(evidence['workers_started'], 0)
        self.assertEqual(evidence['strategies_registered'], 0)
        self.assertFalse(evidence['strategy_executor_built'])
        self.assertIsNone(evidence['broker_telemetry'])
        self.assertEqual(evidence['adapter_version_gate'], 'ACCEPTED_EXACT_LOCAL_IDENTITY')
        self.assertTrue(evidence['real_candidate_reader'])
        self.assertTrue(evidence['real_supervised_demo_broker'])
        self.assertTrue(evidence['reader_closed_and_tokens_cleared'])
        self.assertEqual(evidence['strategy_hooks_called'], 0)
        self.assertEqual(evidence['synthetic_trade_write_methods'], [])
        self.assertFalse(evidence['account_calls_performed'])
        self.assertEqual(evidence['actual_execution'], 'blocked_unqualified_contract')
        calls = evidence['synthetic_http_calls']
        self.assertIn('POST /backend-api/auth/jwt/token', calls)
        self.assertIn('GET /backend-api/auth/jwt/all-accounts', calls)
        self.assertIn('GET /backend-api/trade/accounts/123/instruments', calls)
        self.assertIn('GET /backend-api/trade/quotes', calls)
        self.assertTrue(all(call.startswith('GET ') or
                            call == 'POST /backend-api/auth/jwt/token' for call in calls))


if __name__ == '__main__':
    unittest.main()
