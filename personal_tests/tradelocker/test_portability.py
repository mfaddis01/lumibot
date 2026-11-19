"""Public entrypoint and installed-framework portability regression checks."""
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class PortabilityTests(unittest.TestCase):
    def test_read_requires_explicit_visible_email(self):
        from personal_trading.tradelocker import cli
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.parser().parse_args(['read'])
        self.assertEqual(cli.parser().parse_args(['read', '--email', 'synthetic@example.invalid']).email,
                         'synthetic@example.invalid')

    def test_runtime_does_not_inject_external_framework_source(self):
        script = '''
import importlib.util, sys
from pathlib import Path
from personal_trading.tradelocker import runtime
before = list(sys.path)
runtime.configure()
assert all(item in before for item in sys.path), 'runtime injected source paths'
assert Path(importlib.util.find_spec('lumibot').origin).is_relative_to(Path(sys.prefix)), 'not installed framework'
assert not Path(runtime.SAFE_ENV['HOME']).is_relative_to(Path.cwd()), 'runtime state inside checkout'
'''
        result = subprocess.run([sys.executable, '-B', '-W', 'error', '-c', script], cwd=ROOT,
                                env={'PATH': '/usr/bin:/bin', 'TMPDIR': tempfile.gettempdir()},
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
