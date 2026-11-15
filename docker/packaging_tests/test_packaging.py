"""Offline real Git fixtures. Never writes Git state in the actual checkout."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

DOCKER = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('packaging_context', DOCKER / 'packaging_context.py')


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.home),
                    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                    'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'}
        self.git('init', '-q')
        self.git('config', 'user.name', 'Private Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.repo / 'setup.py').write_text('fixture source\n')
        self.commit()

    def git(self, *args):
        return subprocess.run(['/usr/bin/git', '-C', str(self.repo), *args], env=self.env,
                              check=True, capture_output=True).stdout

    def commit(self):
        self.git('add', '--all')
        self.git('commit', '-qm', 'private synthetic fixture')

    def module(self):
        path = DOCKER / 'packaging_context.py'
        self.assertTrue(path.is_file(), 'committed-only exporter is missing')
        assert SPEC is not None and SPEC.loader is not None
        module = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(module)
        return module

    def prepare_export(self):
        module = self.module()
        paths = ['setup.py', 'README.md', 'LICENSE', 'MANIFEST.in', 'setup.cfg',
                 'pyproject.toml', 'personal_release/VERSION', 'lumibot/__init__.py',
                 'lumibot/resources/conf.yaml', 'personal_trading/__init__.py',
                 'personal_trading/__main__.py', 'personal_trading/cli.py']
        import hashlib
        records = []
        for name in paths:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('0.1.0\n' if name.endswith('VERSION') else 'synthetic fixture\n')
            records.append({'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        for name in module.PACKAGING_FILES:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((DOCKER.parent / name).read_bytes() if (DOCKER.parent / name).is_file()
                             else b'synthetic packaging fixture\n')
        (self.repo / 'docker/packaging-inputs.json').write_text(json.dumps({'schema_version': 1, 'source': records}))
        (self.repo / '.gitignore').write_text('.env\n.oci/\n.ssh/\n.hermes/\n')
        self.commit()
        return module

    def test_repeat_export_only_reviewed_git_blobs_and_complete_inventory(self):
        module = self.module()
        self.assertTrue(hasattr(module, 'PACKAGING_FILES'), 'positive packaging input selection missing')
        module = self.prepare_export()
        import secrets
        marker = secrets.token_hex(32)
        for name in ['.env', '.oci/config', '.ssh/id_fixture', '.hermes/config.yaml']:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(marker)
        first, second = self.root / 'one', self.root / 'two'
        a = module.export_context(self.repo, first)
        b = module.export_context(self.repo, second)
        self.assertEqual(a, b)
        files = {str(p.relative_to(first)) for p in first.rglob('*') if p.is_file()}
        self.assertNotIn('source/.env', files)
        self.assertIn('source/personal_release/VERSION', files)
        self.assertIn('source/personal_trading/cli.py', files)
        self.assertIn('docker/Dockerfile', files)
        self.assertIn('docker/requirements.linux-amd64.txt', files)
        inventory = json.loads((first / 'packaging-inventory.json').read_text())
        self.assertEqual(files - {'packaging-inventory.json'}, {f['path'] for f in inventory['files']})
        self.assertEqual(inventory['source_commit'], self.git('rev-parse', 'HEAD').decode().strip())
        self.assertEqual(inventory['source_mode'], 'committed')
        import hashlib
        self.assertEqual(a, hashlib.sha256((first / 'packaging-inventory.json').read_bytes()).hexdigest())
        for entry in inventory['files']:
            data = (first / entry['path']).read_bytes()
            self.assertNotIn(marker.encode(), data)
            self.assertEqual(entry['sha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual(entry['size'], len(data))
            self.assertEqual(data, (second / entry['path']).read_bytes())

    def test_context_verification_rejects_tamper_extra_symlink_and_wrong_digest(self):
        module = self.prepare_export()
        self.assertTrue(hasattr(module, 'verify_context'), 'build input verification missing')
        destination = self.root / 'context'
        digest = module.export_context(self.repo, destination)
        self.assertEqual(module.verify_context(destination, digest)['source_mode'], 'committed')
        target = destination / 'source/setup.py'
        original = target.read_bytes()
        target.write_bytes(original + b'tamper')
        with self.assertRaisesRegex(ValueError, 'hash|size'):
            module.verify_context(destination, digest)
        target.write_bytes(original)
        extra = destination / 'source/unreviewed.py'
        extra.write_text('extra')
        with self.assertRaisesRegex(ValueError, 'inventory'):
            module.verify_context(destination, digest)
        extra.unlink()
        target.unlink()
        target.symlink_to(self.repo / 'setup.py')
        with self.assertRaisesRegex(ValueError, 'link|regular'):
            module.verify_context(destination, digest)
        target.unlink()
        target.write_bytes(original)
        with self.assertRaisesRegex(ValueError, 'digest'):
            module.verify_context(destination, '0' * 64)
        with self.assertRaisesRegex(ValueError, 'digest'):
            module.verify_context(destination, '')

    def test_export_untracked_link_unreviewed_and_stale_policy_refusals(self):
        module = self.prepare_export()
        (self.repo / 'untracked').write_text('fixture')
        with self.assertRaisesRegex(ValueError, 'clean'):
            module.export_context(self.repo, self.root / 'untracked-context')
        (self.repo / 'untracked').unlink()
        target = self.repo / 'lumibot/__init__.py'
        target.write_text('new source')
        self.commit()
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            module.export_context(self.repo, self.root / 'stale')
        target.unlink()
        target.symlink_to('../setup.py')
        self.commit()
        with self.assertRaisesRegex(ValueError, 'linked'):
            module.export_context(self.repo, self.root / 'linked')
        target.unlink()
        target.write_text('synthetic fixture\n')
        extra = self.repo / 'lumibot/unreviewed.py'
        extra.write_text('new framework source')
        self.commit()
        with self.assertRaisesRegex(ValueError, 'unreviewed'):
            module.export_context(self.repo, self.root / 'extra')

    def test_real_build_cli_verifies_context_and_refuses_wrong_host_before_execution(self):
        module = self.prepare_export()
        import platform
        self.assertIn('def main(', (DOCKER / 'packaging_build.py').read_text(), 'build-stage CLI is missing')
        destination = self.root / 'context'
        digest = module.export_context(self.repo, destination)
        def invoke(command, sha):
            return subprocess.run([sys.executable, str(DOCKER / 'packaging_build.py'), command,
                                   '--root', str(destination), '--digest', sha],
                                  env=self.env, cwd=self.root, text=True, capture_output=True, timeout=20)
        result = invoke('verify', digest)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['context_sha256'], digest)
        for command in ['verify', 'fetch', 'build']:
            result = invoke(command, '0' * 64)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertNotIn('Traceback', result.stderr)
        version = json.loads((DOCKER / 'packaging-base.json').read_text())['python_version_from_config']
        if platform.python_version() != version:
            for command in ['fetch', 'build']:
                result = invoke(command, digest)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('Python does not match', result.stderr)
                self.assertNotIn('Traceback', result.stderr)

    def test_dirty_source_refused_before_destination_created(self):
        module = self.module()
        (self.repo / 'setup.py').write_text('dirty\n')
        destination = self.root / 'context'
        with self.assertRaisesRegex(ValueError, 'clean'):
            module.export_context(self.repo, destination)
        self.assertFalse(destination.exists())


if __name__ == '__main__':
    unittest.main()
