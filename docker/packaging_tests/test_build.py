"""Offline packaging plan/assembly boundaries; not a Docker execution claim."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

DOCKER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DOCKER))


class BuildTests(unittest.TestCase):
    def module(self):
        path = DOCKER / 'packaging_build.py'
        self.assertTrue(path.is_file(), 'offline image build adapter missing')
        spec = importlib.util.spec_from_file_location('packaging_build', path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_public_plan_preserves_all_264_with_real_ibapi_source_exception(self):
        module = self.module()
        plan = module.validate_plan(DOCKER)
        self.assertEqual(len(plan['application']), 264)
        self.assertEqual(len(plan['tools']), 3)
        source = [p for p in plan['application'] if p['kind'] == 'sdist']
        self.assertEqual(len(source), 1)
        self.assertEqual(source[0]['name'], 'ibapi')
        self.assertEqual(source[0]['sha256'], '49f6678bf4cced996920f32ad4b48e6897749ac30ba14a661082285f4ec09cd6')
        self.assertTrue(source[0]['url'].startswith('https://files.pythonhosted.org/'))
        self.assertTrue(all(p['kind'] == 'wheel' for p in plan['application'] if p['name'] != 'ibapi'))
        self.assertFalse(any('/home/' in json.dumps(p) or 'file:' in json.dumps(p) for p in plan['application']))
        artifacts = json.loads((DOCKER / 'packaging-artifacts.json').read_text())
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            for name in ['requirements.linux-amd64.txt', 'dependency-build-tools.txt', 'dependency-provenance.json', 'packaging-base.json']:
                (target / name).write_bytes((DOCKER / name).read_bytes())
            for mutate in ['missing', 'hash', 'private', 'extra-source', 'version']:
                value = copy.deepcopy(artifacts)
                if mutate == 'missing':
                    value['application'].pop()
                elif mutate == 'hash':
                    value['application'][0]['sha256'] = '0' * 64
                elif mutate == 'private':
                    value['application'][0]['url'] = 'https://private.invalid/hidden.whl'
                elif mutate == 'extra-source':
                    value['application'][0]['kind'] = 'sdist'
                else:
                    value['application'][0]['version'] = '0.0.0'
                (target / 'packaging-artifacts.json').write_text(json.dumps(value))
                with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                    module.validate_plan(target)


    def test_downloaded_bytes_require_exact_hash_size_and_no_partial_publish(self):
        module = self.module()
        self.assertTrue(hasattr(module, 'retain_artifact'), 'hash-enforced public artifact retention missing')
        data = b'synthetic public archive fixture'
        item = {'filename': 'fixture.whl', 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            path = module.retain_artifact(io.BytesIO(data), item, target)
            self.assertEqual(path.read_bytes(), data)
            for content in [data + b'X', data[:-1], b'X' * len(data)]:
                path.unlink()
                with self.assertRaises(ValueError):
                    module.retain_artifact(io.BytesIO(content), item, target)
                self.assertEqual(list(target.iterdir()), [])
                path.write_bytes(data)


    def test_explicit_overlay_and_resources_work_outside_checkout(self):
        module = self.module()
        self.assertTrue(hasattr(module, 'install_overlay'), 'personal overlay/resource installation missing')
        import subprocess
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, site = root / 'source', root / 'installed'
            (source / 'lumibot/resources').mkdir(parents=True)
            (source / 'lumibot/resources/conf.yaml').write_text('fixture: true\n')
            (source / 'lumibot/resources/ThetaTerminal.jar').write_bytes(b'not a real jar; resource fixture')
            (source / 'lumibot/__init__.py').write_text('must_not_replace_wheel = True\n')
            (site / 'lumibot').mkdir(parents=True)
            (site / 'lumibot/__init__.py').write_text('installed_wheel = True\n')
            (source / 'personal_trading').mkdir()
            for path in (DOCKER.parent / 'personal_trading').glob('*.py'):
                (source / 'personal_trading' / path.name).write_bytes(path.read_bytes())
            records = module.install_overlay(source, site)
            self.assertTrue((site / 'lumibot/resources/ThetaTerminal.jar').is_file())
            self.assertEqual((site / 'lumibot/__init__.py').read_text(), 'installed_wheel = True\n')
            self.assertIn('personal_trading/__main__.py', {r['path'] for r in records})
            command = [sys.executable, '-I', '-c',
                       'import sys,runpy; sys.path.insert(0, sys.argv.pop(1)); runpy.run_module("personal_trading", run_name="__main__")',
                       str(site), '--help']
            result = subprocess.run(command, cwd=root, env={'PATH': '/usr/bin:/bin',
                                    'PYTHONDONTWRITEBYTECODE': '1', 'LUMIBOT_DISABLE_DOTENV': '1'},
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('offline validation only', result.stdout)
            for record in records:
                self.assertEqual(record['sha256'], hashlib.sha256((site / record['path']).read_bytes()).hexdigest())


    def test_build_recipe_pins_base_nonroot_offline_install_and_disarmed_entry(self):
        path = DOCKER / 'Dockerfile'
        self.assertTrue(path.is_file(), 'disarmed multistage Dockerfile missing')
        text = path.read_text()
        base = json.loads((DOCKER / 'packaging-base.json').read_text())
        reference = 'python@' + base['platform_manifest_digest']
        froms = [line for line in text.splitlines() if line.startswith('FROM ')]
        self.assertEqual(len(froms), 3)
        self.assertTrue(all(' --platform=linux/amd64 ' in line and reference in line for line in froms))
        self.assertIn('USER 10001:10001', text)
        self.assertIn('WORKDIR /opt/personal-trading', text)
        self.assertIn('LUMIBOT_DISABLE_DOTENV=1', text)
        self.assertIn('HOME=/tmp', text)
        self.assertIn('ENTRYPOINT ["/opt/venv/bin/python", "-m", "personal_trading"]', text)
        self.assertIn('CMD ["--help"]', text)
        self.assertNotIn('COPY . ', text)
        self.assertNotIn('COPY .\n', text)
        self.assertNotIn('HEALTHCHECK', text)
        self.assertNotIn('apt-get', text)
        self.assertIn('RUN --network=none python /input/docker/packaging_build.py build', text)
        runtime = text.split(' AS runtime', 1)[1]
        self.assertNotIn('pip install', runtime)
        self.assertNotIn('/downloads', runtime)
        ignore = (DOCKER / 'Dockerfile.dockerignore').read_text()
        self.assertTrue(ignore.startswith('# Exported context only'))
        self.assertIn('**', ignore)
        self.assertIn('!source/**', ignore)
        self.assertIn('!packaging-inventory.json', ignore)
        module = self.module()
        self.assertTrue(hasattr(module, 'build_environment'), 'isolated build child environment missing')
        environment = module.build_environment({'source_date_epoch': 1700000000})
        self.assertEqual(environment['SOURCE_DATE_EPOCH'], '1700000000')
        self.assertEqual(environment['LUMIBOT_DISABLE_DOTENV'], '1')
        self.assertEqual(environment['HOME'], '/tmp/packaging-home')
        self.assertFalse(any(k.startswith(('AWS_', 'APCA_', 'DOCKER_')) for k in environment))
        self.assertTrue(hasattr(module, 'assert_target'), 'pinned runtime assertion missing')
        with self.assertRaisesRegex(ValueError, 'Python'):
            module.assert_target(dict(base, python_version_from_config='0.0.0'))


    def test_local_wheel_hashes_and_install_recipe_never_reuse_scratch_wheel(self):
        module = self.module()
        self.assertTrue(hasattr(module, 'write_install_locks'), 'source-built wheel hash boundary missing')
        self.assertTrue(hasattr(module, 'install_recipe'), 'offline isolated installer recipe missing')
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            (work / 'wheels').mkdir()
            ibapi = work / 'wheels/ibapi-9.81.1.post1-py3-none-any.whl'
            framework = work / 'wheels/lumibot-4.6.64-py3-none-any.whl'
            ibapi.write_bytes(b'synthetic freshly built wheel hash fixture')
            framework.write_bytes(b'synthetic framework wheel hash fixture')
            records = module.write_install_locks(DOCKER, work)
            self.assertEqual({r['filename'] for r in records}, {ibapi.name, framework.name})
            published = (work / 'published.lock').read_text()
            self.assertEqual(len(published.splitlines()), 263)
            self.assertNotIn('ibapi', published)
            local = (work / 'local-build.lock').read_text()
            self.assertIn(hashlib.sha256(ibapi.read_bytes()).hexdigest(), local)
            self.assertNotIn('7cf006ac0a3de22a258d6d38ce3e3da51c718a70e05300e5d354d9d0481774a8', local)
            self.assertIn('lumibot==4.6.64', local)
        recipe = module.install_recipe(Path('/input'), Path('/downloads'), Path('/build'))
        self.assertEqual(recipe[0][1:4], ['-m', 'venv', '/build-tools'])
        install = [c for c in recipe if 'install' in c]
        self.assertEqual(len(install), 1)
        self.assertIn('--require-hashes', install[0])
        self.assertIn('--no-index', install[0])
        self.assertTrue(any(c[-2:] == ['--without-pip', '/opt/venv'] for c in recipe))
        wheels = [c for c in recipe if 'wheel' in c]
        self.assertEqual(len(wheels), 2)
        self.assertTrue(all('--no-build-isolation' in c and '--no-deps' in c and '--no-index' in c for c in wheels))
        self.assertTrue(any('/downloads/application/ibapi-9.81.1.post1.tar.gz' in c for c in wheels))
        self.assertTrue(any('/build/source' in c for c in wheels))


    def test_offline_probe_uses_installed_overlay_and_preserves_run_exit_three(self):
        module = self.module()
        self.assertTrue(hasattr(module, 'probe_cli'), 'installed offline CLI qualification recipe missing')
        import subprocess
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            venv = root / 'venv'
            result = subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(venv)],
                                    env={'PATH': '/usr/bin:/bin', 'HOME': str(root), 'PYTHONDONTWRITEBYTECODE': '1'},
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            site = venv / f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages'
            source = root / 'source'
            (source / 'personal_trading').mkdir(parents=True)
            (source / 'lumibot/resources').mkdir(parents=True)
            for path in (DOCKER.parent / 'personal_trading').glob('*.py'):
                (source / 'personal_trading' / path.name).write_bytes(path.read_bytes())
            module.install_overlay(source, site)
            results = module.probe_cli(venv / 'bin/python', root, module.build_environment({'source_date_epoch': 1700000000}))
            self.assertEqual([r['exit_code'] for r in results], [0, 0, 3, 3])
            self.assertTrue(all(r['status']['execution'] == 'DISARMED' for r in results))
            self.assertTrue(all(not r['status']['orders_enabled'] and not r['status']['broker_checked'] for r in results))


if __name__ == '__main__':
    unittest.main()
