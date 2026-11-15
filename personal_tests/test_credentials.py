"""Synthetic markers generated in memory, never actual credentials."""
import secrets
import unittest


class CredentialTests(unittest.TestCase):
    def test_runtime_contract_is_explicit_and_redacted(self):
        from personal_trading.credentials import RuntimeCredentials
        key, secret = secrets.token_hex(16), secrets.token_hex(32)
        credentials = RuntimeCredentials.from_environ({"ALPACA_API_KEY": key, "ALPACA_API_SECRET": secret})
        self.assertTrue(credentials.key == key)
        self.assertTrue(credentials.secret == secret)
        self.assertFalse(key in repr(credentials) or secret in repr(credentials))
        for environment in ({}, {"ALPACA_API_KEY": key}, {"ALPACA_API_SECRET": secret},
                            {"ALPACA_API_KEY": key, "ALPACA_API_SECRET": " "},
                            {"ALPACA_API_KEY": 1, "ALPACA_API_SECRET": secret}):
            with self.assertRaises(ValueError) as caught:
                RuntimeCredentials.from_environ(environment)
            self.assertFalse(key in str(caught.exception) or secret in str(caught.exception))


if __name__ == "__main__":
    unittest.main()
