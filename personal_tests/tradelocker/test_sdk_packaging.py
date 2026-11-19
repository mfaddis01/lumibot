"""Explicit local packaging change: SDK source stays byte-for-byte upstream."""
import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]


class PackagingTests(unittest.TestCase):
    def test_only_declared_metadata_changes_and_source_is_identical(self):
        from personal_trading.tradelocker.repack_sdk import repack, UPSTREAM_FILENAME, LOCAL_VERSION
        from inputs import require
        upstream = require('sdk_wheel')
        with tempfile.TemporaryDirectory() as tmp:
            output = repack(upstream, Path(tmp))
            with zipfile.ZipFile(upstream) as before, zipfile.ZipFile(output) as after:
                sources = [n for n in before.namelist() if n.startswith('tradelocker/')]
                self.assertEqual(sources, [n for n in after.namelist() if n.startswith('tradelocker/')])
                for name in sources:
                    self.assertEqual(before.read(name), after.read(name))
                metadata = after.read(f'tradelocker-{LOCAL_VERSION}.dist-info/METADATA').decode()
                self.assertIn(f'Version: {LOCAL_VERSION}\n', metadata)
                self.assertIn('Requires-Dist: PyJWT[crypto]==2.15.1\n', metadata)
                self.assertNotIn('Requires-Dist: PyJWT==2.8.0', metadata)
                self.assertIn('X-Local-Packaging-Override:', metadata)
                for filename in after.namelist():
                    if filename.endswith('/RECORD'):
                        continue
                    old = filename.replace(LOCAL_VERSION, '0.56.2')
                    if filename.endswith('/METADATA'):
                        continue
                    self.assertEqual(before.read(old), after.read(filename))
            self.assertTrue(hashlib.sha256(output.read_bytes()).hexdigest())
