"""Small public-fetch/offline-build adapter. Only invoked in Docker build stages."""
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from packaging_context import digest


def retain_artifact(stream, item, directory):
    """Publish only fully hash-verified public archive bytes; no decompression."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / item['filename']
    if target.exists():
        raise ValueError('artifact destination already exists')
    partial = target.with_name(target.name + '.partial')
    hasher = hashlib.sha256()
    size = 0
    try:
        with partial.open('xb') as output:
            while True:
                chunk = stream.read(min(1024 * 1024, item['size'] - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > item['size']:
                    raise ValueError('public artifact size exceeded')
                hasher.update(chunk)
                output.write(chunk)
        if size != item['size'] or hasher.hexdigest() != item['sha256']:
            raise ValueError('public artifact size or hash mismatch')
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return target



def install_overlay(source, site):
    """setup.py excludes personal_trading; copy it and every reviewed data resource explicitly."""
    source, site = Path(source), Path(site)
    records = []
    paths = sorted((source / 'personal_trading').rglob('*'))
    paths += sorted(p for p in (source / 'lumibot').rglob('*') if p.suffix != '.py')
    for path in paths:
        if path.is_symlink():
            raise ValueError('overlay/resource links forbidden')
        if not path.is_file():
            continue
        name = path.relative_to(source).as_posix()
        from packaging_context import safe_path
        safe_path(name)
        target = site / name
        target.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes()
        target.write_bytes(data)
        target.chmod(0o644)
        records.append({'path': name, 'sha256': digest(data), 'size': len(data)})
    return records


def build_environment(inventory):
    return {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/tmp/packaging-home',
            'TMPDIR': '/tmp', 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
            'PYTHONNOUSERSITE': '1', 'LUMIBOT_DISABLE_DOTENV': '1',
            'SOURCE_DATE_EPOCH': str(inventory['source_date_epoch']),
            'MPLCONFIGDIR': '/tmp/matplotlib', 'XDG_CACHE_HOME': '/tmp/cache'}


def assert_target(base):
    import platform
    if platform.python_version() != base['python_version_from_config']:
        raise ValueError('Python does not match pinned base config')
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise ValueError('requires Linux/amd64')
    libc, version = platform.libc_ver()
    if libc != 'glibc' or tuple(map(int, version.split('.'))) < (2, 36):
        raise ValueError('requires glibc >=2.36')


def write_install_locks(docker, work):
    docker, work = Path(docker), Path(work)
    locked = read_lock((docker / 'requirements.linux-amd64.txt').read_text())
    (work / 'published.lock').write_text('\n'.join(v['line'] for k, v in locked.items() if k != 'ibapi') + '\n')
    records, lines = [], []
    for name, version in [('ibapi', '9.81.1.post1'), ('lumibot', '4.6.64')]:
        filename = f'{name}-{version}-py3-none-any.whl'
        data = (work / 'wheels' / filename).read_bytes()
        sha = digest(data)
        records.append({'filename': filename, 'sha256': sha, 'size': len(data),
                        'origin': 'built-offline-in-this-build', 'published_wheel': False})
        lines.append(f'{name}=={version} --hash=sha256:{sha}')
    (work / 'local-build.lock').write_text('\n'.join(lines) + '\n')
    return records


def install_recipe(root, downloads, work):
    """Initial offline commands; runtime installation uses freshly recorded local wheel hashes."""
    import sys
    root, downloads, work = Path(root), Path(downloads), Path(work)
    pip = ['/build-tools/bin/python', '-m', 'pip', '--isolated', '--disable-pip-version-check']
    wheel = pip + ['wheel', '--no-index', '--no-deps', '--no-build-isolation', '--no-cache-dir',
                   '--wheel-dir', str(work / 'wheels')]
    return [
        [sys.executable, '-m', 'venv', '/build-tools'],
        pip + ['install', '--no-index', '--no-deps', '--no-cache-dir', '--require-hashes',
               '--only-binary=:all:', '--find-links', str(downloads / 'tools'),
               '-r', str(root / 'docker/dependency-build-tools.txt')],
        [sys.executable, '-m', 'venv', '--without-pip', '/opt/venv'],
        wheel + [str(downloads / 'application/ibapi-9.81.1.post1.tar.gz')],
        wheel + [str(work / 'source')],
    ]


def probe_cli(python, work, environment):
    """Synthetic offline build-stage check, never trader/process/account health."""
    import subprocess
    config = {'paper': True, 'endpoint': 'https://paper-api.alpaca.markets', 'armed': False,
              'expected_account_id': '00000000-0000-4000-8000-000000000001', 'policy_sha256': 'a' * 64,
              'risk': {'symbols': ['TEST'], 'asset_types': ['stock'], 'max_order_notional': '10.00',
                       'max_gross_exposure': '20.00', 'max_daily_loss': '5.00',
                       'max_open_orders': 1, 'max_option_contracts': 0}}
    work = Path(work)
    path = work / 'synthetic-offline-config.json'
    records = []
    for command, armed, expected in [('validate', False, 0), ('readiness', False, 0),
                                      ('run', False, 3), ('run', True, 3)]:
        config['armed'] = armed
        path.write_text(json.dumps(config))
        result = subprocess.run([str(python), '-I', '-m', 'personal_trading', command, '--config', str(path)],
                                cwd=work, env=environment, capture_output=True, text=True, timeout=30)
        if result.returncode != expected:
            raise ValueError('offline CLI exit contract failed: ' + command)
        status = json.loads(result.stdout)
        if status['execution'] != 'DISARMED' or status['orders_enabled'] is not False or status['broker_checked'] is not False:
            raise ValueError('offline CLI disarmed contract failed')
        records.append({'command': command, 'fixture_armed': armed, 'exit_code': result.returncode, 'status': status})
    path.unlink()
    return records


def read_lock(text):
    result = {}
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([a-z0-9][a-z0-9-]*)(?:\[[a-z0-9,-]+\])?==([^ ]+) ((?:--hash=sha256:[0-9a-f]{64} ?)+)', line)
        if not match or match[1] in result:
            raise ValueError('invalid or duplicate hash-locked dependency')
        result[match[1]] = {'version': match[2], 'hashes': set(re.findall(r'sha256:([0-9a-f]{64})', match[3])), 'line': line}
    return result


def validate_plan(docker):
    docker = Path(docker)
    lock_data = (docker / 'requirements.linux-amd64.txt').read_bytes()
    lock = read_lock(lock_data.decode())
    tool_lock = read_lock((docker / 'dependency-build-tools.txt').read_text())
    provenance = json.loads((docker / 'dependency-provenance.json').read_text())
    base = json.loads((docker / 'packaging-base.json').read_text())
    plan = json.loads((docker / 'packaging-artifacts.json').read_text())
    expected_hash = provenance['result']['lock_sha256']
    if digest(lock_data) != expected_hash or base['marker_replay']['candidate_lock_sha256'] != expected_hash:
        raise ValueError('candidate lock hash mismatch')
    if (len(lock) != 264 or lock.get('alpaca-py', {}).get('version') != '0.42.0'
            or lock.get('websockets', {}).get('version') != '15.0.1'
            or set(tool_lock) != {'pip', 'setuptools', 'wheel'}
            or set(plan) != {'schema_version', 'application', 'tools'}
            or type(plan['schema_version']) is not int or plan['schema_version'] != 1):
        raise ValueError('invalid full-graph packaging plan')
    for group, locked in [('application', lock), ('tools', tool_lock)]:
        seen = set()
        for item in plan[group]:
            if set(item) != {'name', 'version', 'kind', 'filename', 'url', 'sha256', 'size'}:
                raise ValueError('invalid artifact shape')
            name = item['name']
            url = urlparse(item['url'])
            filename = item['filename']
            if (name in seen or name not in locked or locked[name]['version'] != item['version']
                    or item['sha256'] not in locked[name]['hashes']
                    or url.scheme != 'https' or url.netloc != 'files.pythonhosted.org'
                    or not url.path.startswith('/packages/') or url.query or url.fragment
                    or not re.fullmatch('[A-Za-z0-9_.+-]+', filename)
                    or url.path.rsplit('/', 1)[-1] != filename
                    or type(item['size']) is not int or not 0 < item['size'] < 512 * 1024 * 1024):
                raise ValueError('artifact differs from public hash-locked inputs')
            expected_kind = 'sdist' if group == 'application' and name == 'ibapi' else 'wheel'
            suffix = '.tar.gz' if expected_kind == 'sdist' else '.whl'
            if item['kind'] != expected_kind or not filename.endswith(suffix):
                raise ValueError('only ibapi may use source; all others require published wheels')
            if name == 'ibapi' and item['sha256'] != provenance['ibapi']['published_sdist_sha256']:
                raise ValueError('ibapi source hash mismatch')
            seen.add(name)
        if seen != set(locked):
            raise ValueError('incomplete artifact plan')
    return plan


def fetch_artifacts(plan):
    """The sole build-stage network action; no metadata execution or package installation."""
    import urllib.request
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError('public artifact redirects are not permitted')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for group in ('application', 'tools'):
        for item in plan[group]:
            with opener.open(item['url'], timeout=120) as response:
                retain_artifact(response, item, Path('/downloads') / group)


def build_image_files(root, inventory, plan, context_digest):
    """Executed only in the network-disabled Docker builder, never on the source host."""
    import platform
    import shutil
    import subprocess
    from packaging_context import canonical
    root = Path(root)
    work = Path('/build')
    work.mkdir()
    environment = build_environment(inventory)
    Path(environment['HOME']).mkdir(mode=0o700, parents=True, exist_ok=True)
    # Revalidate retained downloads before any setup or wheel code executes.
    for group in ('application', 'tools'):
        for item in plan[group]:
            path = Path('/downloads') / group / item['filename']
            if path.is_symlink() or path.stat().st_size != item['size'] or digest(path.read_bytes()) != item['sha256']:
                raise ValueError('download changed before offline build')
    shutil.copytree(root / 'source', work / 'source')
    def run(command, cwd=work):
        print('BUILD_COMMAND ' + json.dumps(command), flush=True)
        result = subprocess.run(command, cwd=cwd, env=environment, capture_output=True, text=True, timeout=600)
        print(result.stdout, end='', flush=True)
        if result.returncode:
            print(result.stderr, end='', flush=True)
            raise ValueError('offline build command failed')
        return result.stdout
    for command in install_recipe(root, Path('/downloads'), work):
        run(command, work / 'source')
    records = write_install_locks(root / 'docker', work)
    pip = ['/build-tools/bin/python', '-m', 'pip', '--isolated', '--disable-pip-version-check',
           '--python', '/opt/venv']
    run(pip + ['install', '--no-index', '--no-deps', '--no-cache-dir', '--require-hashes',
               '--only-binary=:all:', '--no-compile', '--find-links', '/downloads/application',
               '--find-links', '/build/wheels', '-r', str(work / 'published.lock'),
               '-r', str(work / 'local-build.lock')])
    site = Path('/opt/venv/lib/python3.12/site-packages')
    overlay = install_overlay(root / 'source', site)
    pip_check = run(pip + ['check'])
    # Import origin and representative native module checks are NOT a broker check.
    probe = '''import importlib,importlib.metadata,json,pathlib,sys
names=['lumibot','personal_trading','alpaca','numpy','pandas','polars','pyarrow','scipy','yappi','bcrypt','matplotlib','duckdb','cryptography','psutil']
origins={name:str(pathlib.Path(importlib.import_module(name).__file__).resolve()) for name in names}
assert all(path.startswith('/opt/venv/') for path in origins.values()),origins
assert importlib.metadata.version('lumibot')=='4.6.64'
assert importlib.metadata.version('alpaca-py')=='0.42.0'
assert pathlib.Path('/opt/venv/lib/python3.12/site-packages/lumibot/resources/conf.yaml').is_file()
print(json.dumps(origins,sort_keys=True))
'''
    imports = run(['/opt/venv/bin/python', '-I', '-c', probe])
    cli = probe_cli(Path('/opt/venv/bin/python'), work, environment)
    output = Path('/opt/personal-trading')
    output.mkdir(parents=True)
    # Preserve pristine original inputs, not setup.py-mutated build/source.
    shutil.copytree(root, output / 'inputs')
    provenance = {'schema_version': 1, 'kind': 'offline-build-record',
                  'context_sha256': context_digest, 'source_commit': inventory['source_commit'],
                  'personal_version': inventory['personal_version'], 'framework_version': '4.6.64',
                  'source_date_epoch': inventory['source_date_epoch'],
                  'python_version': platform.python_version(), 'libc_observed': list(platform.libc_ver()),
                  'base': json.loads((root / 'docker/packaging-base.json').read_text()),
                  'built_wheels': records, 'overlay_and_resources': overlay, 'pip_check_stdout': pip_check,
                  'import_probe_stdout': imports, 'offline_cli_checks': cli,
                  'qualification_scope': 'network-disabled build-stage only; final nonroot container pending',
                  'execution': 'DISARMED', 'orders_enabled': False}
    (output / 'build-provenance.json').write_bytes(canonical(provenance))
    shutil.copy2(work / 'published.lock', output / 'published-install.lock')
    shutil.copy2(work / 'local-build.lock', output / 'local-build-install.lock')


def main():
    import argparse
    import subprocess
    from packaging_context import verify_context
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('verify', 'fetch', 'build'))
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--digest', required=True)
    args = parser.parse_args()
    try:
        inventory = verify_context(args.root, args.digest)
        plan = validate_plan(args.root / 'docker')
        if args.command != 'verify':
            assert_target(json.loads((args.root / 'docker/packaging-base.json').read_text()))
            if args.command == 'fetch':
                fetch_artifacts(plan)
            else:
                build_image_files(args.root, inventory, plan, args.digest)
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        parser.exit(2, 'packaging refused: ' + str(exc) + '\n')
    print(json.dumps({'command': args.command, 'context_sha256': args.digest, 'execution': 'DISARMED'}))


if __name__ == '__main__':
    main()
