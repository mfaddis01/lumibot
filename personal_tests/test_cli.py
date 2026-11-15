"""Exercise the real CLI in clean subprocesses; no secrets or sockets needed."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from test_settings import fixture_config

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def invoke(self, *arguments):
        # No inherited broker credentials or dotenv loading in these fixtures.
        env = {k: os.environ[k] for k in ("PATH", "TMPDIR") if k in os.environ}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run([sys.executable, "-m", "personal_trading", *arguments],
                              cwd=ROOT, env=env, text=True, capture_output=True)

    def test_offline_readiness_validates_without_framework_or_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "settings.json"
            config.write_text(json.dumps(fixture_config()))
            for command in ("validate", "readiness"):
                result = self.invoke(command, "--config", str(config))
                self.assertEqual(result.returncode, 0, result.stderr)
                status = json.loads(result.stdout)
                self.assertEqual(status["phase"], "OFFLINE_VALIDATED")
                self.assertFalse(status["orders_enabled"])
                self.assertFalse(status["broker_checked"])
                self.assertEqual(status["execution"], "DISARMED")
                self.assertFalse("00000000" in result.stdout)
            self.assertEqual(list(Path(directory).iterdir()), [config])
    def test_invalid_input_diagnostics_never_echo_values_or_paths(self):
        import secrets
        marker = secrets.token_hex(16)  # synthetic marker, not a credential
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / marker
            duplicate_valid_config = '{"paper":true,' + json.dumps(fixture_config())[1:]
            for text in (marker, duplicate_valid_config, " " * 65537 + json.dumps(fixture_config()),
                         json.dumps({"API_SECRET": marker})):
                path.write_text(text)
                result = self.invoke("validate", "--config", str(path))
                self.assertEqual(result.returncode, 2)
                self.assertFalse(marker in result.stdout + result.stderr)
                self.assertFalse("Traceback" in result.stderr)
            result = self.invoke(marker)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(marker in result.stdout + result.stderr)
            path.unlink()
            result = self.invoke("validate", "--config", str(path))
            self.assertEqual(result.returncode, 2)
            self.assertFalse(marker in result.stdout + result.stderr)

    def test_run_is_explicitly_unavailable_even_with_armed_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for armed in (False, True):
                config = fixture_config()
                config["armed"] = armed
                path.write_text(json.dumps(config))
                result = self.invoke("run", "--config", str(path))
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertEqual(json.loads(result.stdout)["execution"], "DISARMED")
                self.assertFalse(json.loads(result.stdout)["orders_enabled"])


if __name__ == "__main__":
    unittest.main()
