"""Synthetic configuration fixtures only; no accounts, credentials, or fills."""
import unittest
from copy import deepcopy
from importlib import import_module


def fixture_config():
    """Illustrative test limits, NOT owner-approved trading parameters."""
    return {
        "paper": True,
        "endpoint": "https://paper-api.alpaca.markets",
        "armed": False,
        "expected_account_id": "00000000-0000-4000-8000-000000000001",
        "policy_sha256": "a" * 64,
        "risk": {
            "symbols": ["TEST"],
            "asset_types": ["stock"],
            "max_order_notional": "10.00",
            "max_gross_exposure": "20.00",
            "max_daily_loss": "5.00",
            "max_open_orders": 1,
            "max_option_contracts": 0,
        },
    }


class SettingsTests(unittest.TestCase):
    def test_explicit_configuration_is_valid_and_secrets_are_not_part_of_it(self):
        self.assertIsNotNone(import_module("personal_trading.settings").Settings.from_mapping(fixture_config()))
    def test_only_the_exact_paper_origin_is_allowed(self):
        from personal_trading.settings import Settings
        for endpoint in (
            "https://api.alpaca.markets", "http://paper-api.alpaca.markets",
            "https://paper-api.alpaca.markets/", "https://paper-api.alpaca.markets/v2",
            "https://paper-api.alpaca.markets.evil.invalid",
            "https://paper-api.alpaca.markets:443", "https://user@paper-api.alpaca.markets",
            "https://paper-api.alpaca.markets?redirect=live", None,
        ):
            config = fixture_config()
            config["endpoint"] = endpoint
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                Settings.from_mapping(config)
        for paper in (False, "true", 1, None):
            config = fixture_config()
            config["paper"] = paper
            with self.subTest(paper=paper), self.assertRaises(ValueError):
                Settings.from_mapping(config)

    def test_schema_fails_closed_without_explicit_scope_limits_and_arming(self):
        from personal_trading.settings import Settings
        cases = [None, [], {}]
        original = fixture_config()
        for key in original:
            value = deepcopy(original)
            del value[key]
            cases.append(value)
        for key in original["risk"]:
            value = deepcopy(original)
            del value["risk"][key]
            cases.append(value)
        for key, invalids in {
            "armed": [None, "false", 0],
            "expected_account_id": [None, "not-an-account"],
            "policy_sha256": [None, "policy-not-approved", "A" * 64],
            "risk": [None, {}],
            "API_SECRET": ["synthetic-invalid-field"],
        }.items():
            for invalid in invalids:
                value = deepcopy(original)
                value[key] = invalid
                cases.append(value)
        for key, invalids in {
            "symbols": [[], ["TEST", "TEST"], ["test"], "TEST", [1]],
            "asset_types": [[], ["crypto"], ["stock", "stock"]],
            "max_order_notional": [None, 0, -1, True, "NaN", "Infinity", "-0.1", "0"],
            "max_gross_exposure": ["0", "1"],
            "max_daily_loss": ["NaN", "0", "21"],
            "max_open_orders": [0, -1, True, "1", 1.5],
            "max_option_contracts": [-1, True, "0"],
            "unexpected": [1],
        }.items():
            for invalid in invalids:
                value = deepcopy(original)
                value["risk"][key] = invalid
                cases.append(value)
        value = deepcopy(original)
        value["risk"]["asset_types"] = ["option"]
        cases.append(value)  # options require a positive explicit contract limit
        for index, value in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError):
                Settings.from_mapping(value)
        value = deepcopy(original)
        value["risk"]["asset_types"] = ["stock", "option"]
        value["risk"]["max_option_contracts"] = 2
        self.assertIsNotNone(Settings.from_mapping(value))

    def test_load_discards_malformed_credential_document_and_exception_chain(self):
        from pathlib import Path
        import tempfile
        import traceback
        from personal_trading.settings import Settings

        marker = "SYNTHETIC_NOT_A_CREDENTIAL_SETTINGS_CONTEXT"
        document = '{"ALPACA_API_SECRET":"' + marker + '",'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "malformed.json"
            path.write_text(document, encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                Settings.load(path)
        error = caught.exception
        with self.subTest(surface="context"):
            self.assertIsNone(error.__context__)
        with self.subTest(surface="cause"):
            self.assertIsNone(error.__cause__)
        with self.subTest(surface="raw_document"):
            retained = (getattr(error, "doc", ""), getattr(error.__context__, "doc", ""),
                        getattr(error.__cause__, "doc", ""))
            self.assertFalse(any(marker in str(value) for value in retained))
        with self.subTest(surface="public_diagnostics"):
            rendered = "".join(traceback.format_exception(error))
            self.assertEqual(str(error), "configuration invalid or unreadable")
            self.assertFalse(marker in rendered + repr(error) + repr(error.args) + repr(vars(error)))

    def test_identity_is_stable_immutable_and_covers_all_configuration(self):
        from personal_trading.settings import Settings
        value = fixture_config()
        first = Settings.from_mapping(value)
        self.assertRegex(first.identity, r"^[0-9a-f]{64}$")
        self.assertEqual(first.identity, Settings.from_mapping(dict(reversed(list(value.items())))).identity)
        value["risk"]["symbols"].append("OTHER")
        self.assertNotEqual(first.identity, Settings.from_mapping(value).identity)
        copy = first.as_mapping()
        copy["armed"] = True
        self.assertFalse(first.as_mapping()["armed"])
        self.assertFalse("TEST" in repr(first))
        with self.assertRaises(ValueError):
            first.require_inspection_authorization(first.identity)
        copy = first.as_mapping()
        copy["armed"] = True
        armed = Settings.from_mapping(copy)
        with self.assertRaises(ValueError):
            armed.require_inspection_authorization(first.identity)
        armed.require_inspection_authorization(armed.identity)


if __name__ == "__main__":
    unittest.main()
