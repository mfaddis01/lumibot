"""Real framework integration, with synthetic HTTP/account fixtures only.

No sockets, actual credentials, order/fill emulation, or broker replacements.
"""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from personal_trading.settings import Settings
from test_settings import fixture_config

HAS_SDK = importlib.util.find_spec("alpaca") is not None


class IntegrationGuardTests(unittest.TestCase):
    def test_disarmed_or_unacknowledged_settings_cannot_touch_credentials_or_state(self):
        from personal_trading.integration import inspect_paper_account

        class UnreadableEnvironment:
            def get(self, key):
                raise AssertionError("credentials were read before authorization")

        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "must-not-exist"
            for armed, acknowledgement in ((False, None), (True, None), (True, "not-the-config")):
                config = fixture_config()
                config["armed"] = armed
                with self.assertRaises(ValueError):
                    inspect_paper_account(Settings.from_mapping(config), state_directory=state,
                                          acknowledgement=acknowledgement, environment=UnreadableEnvironment())
                self.assertFalse(state.exists())

    def test_existing_ownership_and_identity_mismatch_stop_before_credentials(self):
        from personal_trading.integration import inspect_paper_account
        from personal_trading.state import exclusive_executor, bound_executor

        class UnreadableEnvironment:
            def get(self, key):
                raise AssertionError("credentials read before local ownership checks")

        config = fixture_config()
        config["armed"] = True
        settings = Settings.from_mapping(config)
        with tempfile.TemporaryDirectory() as directory:
            arguments = dict(state_directory=directory, acknowledgement=settings.identity,
                             environment=UnreadableEnvironment())
            with exclusive_executor(directory), self.assertRaises(RuntimeError):
                inspect_paper_account(settings, **arguments)
            with bound_executor(directory, "b" * 64):
                pass
            with self.assertRaises(RuntimeError):
                inspect_paper_account(settings, **arguments)


@unittest.skipUnless(HAS_SDK, "optional real-framework fixture tests require alpaca-py")
class FrameworkFixtureTests(unittest.TestCase):
    def setUp(self):
        import json
        import secrets
        from unittest.mock import patch
        import requests
        self.environment = {"ALPACA_API_KEY": secrets.token_hex(16), "ALPACA_API_SECRET": secrets.token_hex(32)}
        self.config = fixture_config()
        self.config["armed"] = True
        self.settings = Settings.from_mapping(self.config)
        self.account = {"id": self.config["expected_account_id"], "account_number": "SYNTHETIC-FIXTURE",
                        "status": "ACTIVE", "trading_blocked": False,
                        "account_blocked": False, "trade_suspended_by_user": False}
        self.requests = []
        self.directory = self.enterContext(tempfile.TemporaryDirectory())

        def fixture_request(session, method, url, **kwargs):
            self.requests.append((method, url))
            if method.upper() != "GET" or url != "https://paper-api.alpaca.markets/v2/account":
                raise AssertionError("fixture forbids non-account requests and all writes")
            self.assertFalse(kwargs.get("allow_redirects", True))
            self.assertIsNotNone(kwargs.get("timeout"))
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps(self.account).encode()
            response.headers["Content-Type"] = "application/json"
            return response

        self.enterContext(patch("requests.sessions.Session.request", fixture_request))
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("real sockets forbidden")))

    def inspect(self):
        from personal_trading.integration import inspect_paper_account
        return inspect_paper_account(self.settings, state_directory=self.directory,
                                     acknowledgement=self.settings.identity, environment=self.environment)

    def test_real_sdk_alpaca_and_trader_construct_with_no_workers_or_strategy(self):
        import threading
        from unittest.mock import patch
        from lumibot.brokers import Alpaca
        from lumibot.traders import Trader
        seen = []
        original_broker, original_trader = Alpaca.__init__, Trader.__init__

        def observe_broker(broker, *args, **kwargs):
            self.assertEqual(len(self.requests), 1)  # account check happened first
            original_broker(broker, *args, **kwargs)
            self.assertTrue(broker.is_paper)
            self.assertIsNone(broker._orders_thread)
            self.assertFalse(hasattr(broker, "stream"))
            seen.append(type(broker).__name__)

        def observe_trader(trader, *args, **kwargs):
            original_trader(trader, *args, **kwargs)
            self.assertFalse(trader._strategies)
            seen.append(type(trader).__name__)

        before = {t.ident for t in threading.enumerate()}
        with patch.object(Alpaca, "__init__", observe_broker), patch.object(Trader, "__init__", observe_trader):
            status = self.inspect()
        self.assertEqual(seen, ["Alpaca", "Trader"])
        self.assertEqual(self.requests, [("GET", "https://paper-api.alpaca.markets/v2/account")])
        self.assertEqual(status["phase"], "PAPER_ACCOUNT_VERIFIED")
        self.assertTrue(status["framework_constructed"])
        self.assertFalse(status["orders_enabled"])
        self.assertEqual(before, {t.ident for t in threading.enumerate()})
        self.assertFalse(any(value in repr(status) for value in self.environment.values()))
        self.assertFalse(self.account["id"] in repr(status))
    def test_account_identity_and_active_unblocked_status_precede_framework_construction(self):
        from unittest.mock import patch
        from lumibot.brokers import Alpaca
        original = dict(self.account)
        cases = [dict(original, id="00000000-0000-4000-8000-000000000002"),
                 dict(original, status="ACCOUNT_CLOSED")]
        for flag in ("trading_blocked", "account_blocked", "trade_suspended_by_user"):
            cases.append(dict(original, **{flag: True}))
            missing = dict(original)
            del missing[flag]
            cases.append(missing)
        for index, account in enumerate(cases):
            self.account = account
            with self.subTest(case=index), patch.object(Alpaca, "__init__") as constructor:
                with self.assertRaises(RuntimeError):
                    self.inspect()
                self.assertEqual(constructor.call_count, 0)
    def test_raw_eligibility_flags_require_json_false_before_sdk_or_framework_construction(self):
        from contextlib import ExitStack
        from unittest.mock import patch
        from alpaca.trading.models import TradeAccount
        from lumibot.brokers import Alpaca
        from lumibot.data_sources import AlpacaData
        from lumibot.traders import Trader

        original = dict(self.account)
        missing = object()
        invalids = (0, 1, "false", "true", "no", "yes", "0", "1", "", None, missing, True)
        seen = []

        def observe(cls):
            constructor = cls.__init__

            def observed(instance, *args, **kwargs):
                seen.append(cls.__name__)
                return constructor(instance, *args, **kwargs)

            return observed

        with ExitStack() as stack:
            for cls in (TradeAccount, AlpacaData, Alpaca, Trader):
                stack.enter_context(patch.object(cls, "__init__", observe(cls)))
            for flag in ("trading_blocked", "account_blocked", "trade_suspended_by_user"):
                for value in invalids:
                    label = "missing" if value is missing else repr(value)
                    with self.subTest(flag=flag, raw=label):
                        self.account = dict(original)
                        if value is missing:
                            del self.account[flag]
                        else:
                            self.account[flag] = value
                        seen.clear()
                        self.requests.clear()
                        with self.assertRaisesRegex(RuntimeError, "paper inspection failed"):
                            self.inspect()
                        self.assertEqual(seen, [], "raw flags reached SDK/framework constructors")
                        self.assertEqual(self.requests, [("GET", "https://paper-api.alpaca.markets/v2/account")])

    def test_provider_and_framework_failures_cannot_leak_secrets_to_errors_or_logs(self):
        import contextlib
        import io
        import logging
        import traceback
        from unittest.mock import patch
        from lumibot.brokers import Alpaca
        marker = self.environment["ALPACA_API_SECRET"]
        for target in ("requests.sessions.Session.request", "lumibot.brokers.alpaca.Alpaca.__init__"):
            output = io.StringIO()
            handler = logging.StreamHandler(output)
            logging.getLogger().addHandler(handler)
            try:
                def leaking_failure(*args, **kwargs):
                    print(marker)
                    logging.getLogger().warning(marker)
                    raise ValueError(marker)
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), patch(target, leaking_failure):
                    try:
                        self.inspect()
                    except Exception as error:
                        rendered = "".join(traceback.format_exception(error))
                        has_raw_context = error.__context__ is not None
                    else:
                        self.fail("fixture failure was ignored")
                self.assertFalse(has_raw_context)
                self.assertFalse(marker in output.getvalue())
                self.assertFalse(marker in rendered)
                self.assertTrue("paper inspection failed" in rendered)
            finally:
                logging.getLogger().removeHandler(handler)

    def test_ambient_telemetry_cannot_start_workers_and_is_restored(self):
        import os
        import threading
        from unittest.mock import patch
        before = {t.ident for t in threading.enumerate()}
        with patch.dict(os.environ, {"LUMIBOT_TELEMETRY": "1"}):
            self.inspect()
            self.assertEqual(os.environ["LUMIBOT_TELEMETRY"], "1")
        self.assertEqual(before, {t.ident for t in threading.enumerate()})
    def test_redirect_response_cannot_qualify_an_account_even_with_plausible_body(self):
        import json
        import requests
        from unittest.mock import patch
        from lumibot.brokers import Alpaca

        def redirect(session, method, url, **kwargs):
            self.assertFalse(kwargs["allow_redirects"])
            response = requests.Response()
            response.status_code = 302
            response.headers["Location"] = "https://api.alpaca.markets/v2/account"
            response._content = json.dumps(self.account).encode()
            return response

        with patch("requests.sessions.Session.request", redirect), patch.object(Alpaca, "__init__") as constructor:
            with self.assertRaises(RuntimeError):
                self.inspect()
            self.assertEqual(constructor.call_count, 0)

    def test_transport_denies_writes_other_origins_and_other_reads(self):
        from personal_trading.integration import _inspection_session
        with _inspection_session() as session:
            self.assertFalse(session.trust_env)
            for method, url in (("POST", "https://paper-api.alpaca.markets/v2/account"),
                                ("GET", "https://api.alpaca.markets/v2/account"),
                                ("GET", "https://paper-api.alpaca.markets/v2/orders")):
                with self.assertRaises(RuntimeError):
                    session.request(method, url)
        self.assertEqual(self.requests, [])
    def test_unqualified_framework_or_sdk_versions_fail_before_account_requests(self):
        from unittest.mock import patch
        import lumibot
        for target, value in (("lumibot.__version__", "unqualified"),
                              ("importlib.metadata.version", lambda name: "unqualified")):
            with self.subTest(target=target), patch(target, value):
                with self.assertRaises(RuntimeError):
                    self.inspect()
                self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
