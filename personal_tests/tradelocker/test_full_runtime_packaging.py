"""Local full-code wheel qualification; never executes upstream setup.py."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from email.parser import BytesParser

ROOT = Path(__file__).resolve().parents[2]
from inputs import require


class FullRuntimePackagingTests(unittest.TestCase):
    def test_same_version_changed_source_cannot_claim_pinned_identity(self):
        from personal_trading.tradelocker.packaging.runtime.build_framework import build
        source = require('framework_source')
        manifest = json.loads((ROOT / 'personal_trading/tradelocker/packaging/runtime/framework-source-manifest.json').read_text())
        with tempfile.TemporaryDirectory() as scratch:
            altered = Path(scratch) / 'source'
            for name in manifest['files']:
                target = altered / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((source / name).read_bytes())
            target = altered / 'lumibot/__init__.py'
            target.write_bytes(target.read_bytes() + b'\n# synthetic mutation\n')
            with self.assertRaisesRegex(ValueError, 'source.*requalify'):
                build(altered, Path(scratch) / 'wheels')

    def test_local_wheel_preserves_all_framework_code_and_mandatory_dependencies(self):
        FRAMEWORK = require('framework_source')
        spec = importlib.util.spec_from_file_location('full_runtime_build', ROOT / 'personal_trading/tradelocker/packaging/runtime/build_framework.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tree = ast.parse((FRAMEWORK / 'setup.py').read_text())
        setup = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Attribute) and n.func.attr == 'setup')
        expected = ast.literal_eval(next(k.value for k in setup.keywords if k.arg == 'install_requires'))
        extras = ast.literal_eval(next(k.value for k in setup.keywords if k.arg == 'extras_require'))
        for extra, deps in extras.items():
            expected += [f'{dep}; extra == "{extra}"' for dep in deps]
        with tempfile.TemporaryDirectory() as out:
            wheel = module.build(FRAMEWORK, Path(out))
            with zipfile.ZipFile(wheel) as archive:
                names = archive.namelist()
                metadata = BytesParser().parsebytes(archive.read(next(n for n in names if n.endswith('/METADATA'))))
                self.assertEqual(metadata['Name'], 'lumibot')
                self.assertEqual(metadata['Version'], '4.6.64+source8dbcf8f.1')
                self.assertEqual(metadata.get_all('Requires-Dist'), expected)
                source_files = [p for p in (FRAMEWORK / 'lumibot').rglob('*.py') if '__pycache__' not in p.parts]
                self.assertGreater(len(source_files), 100)
                for source in source_files:
                    self.assertEqual(archive.read(source.relative_to(FRAMEWORK).as_posix()), source.read_bytes())
                manifest = json.loads(archive.read('lumibot-4.6.64+source8dbcf8f.1.dist-info/source-manifest.json'))
                for name, digest in manifest['files'].items():
                    self.assertEqual(hashlib.sha256((FRAMEWORK / name).read_bytes()).hexdigest(), digest)
                    if name.startswith('lumibot/'):
                        self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)
                self.assertIn('source', metadata['X-Local-Packaging-Disposition'])

    def test_runtime_sdk_derivative_changes_only_two_dependency_edges_and_identity(self):
        spec = importlib.util.spec_from_file_location('full_runtime_sdk', ROOT / 'personal_trading/tradelocker/packaging/runtime/repack_sdk_runtime.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        upstream = require('sdk_wheel')
        with tempfile.TemporaryDirectory() as out:
            result = module.repack(upstream, Path(out))
            with zipfile.ZipFile(upstream) as original, zipfile.ZipFile(result) as derived:
                prefix = 'tradelocker-0.56.2.dist-info/'
                local = 'tradelocker-0.56.2+lumibot.runtime.1.dist-info/'
                for name in original.namelist():
                    if name in (prefix + 'METADATA', prefix + 'RECORD'):
                        continue
                    self.assertEqual(original.read(name), derived.read(name.replace(prefix, local)))
                before = BytesParser().parsebytes(original.read(prefix + 'METADATA'))
                after = BytesParser().parsebytes(derived.read(local + 'METADATA'))
                self.assertEqual(after['Version'], '0.56.2+lumibot.runtime.1')
                expected = [r.replace('PyJWT (==2.8.0)', 'PyJWT[crypto]==2.15.1')
                            .replace('requests (==2.32.2)', 'requests==2.34.2')
                            for r in before.get_all('Requires-Dist')]
                self.assertEqual(after.get_all('Requires-Dist'), expected)
                first_bytes = result.read_bytes()
                self.assertEqual(module.repack(upstream, Path(out)).read_bytes(), first_bytes)


if __name__ == '__main__':
    unittest.main()
