"""Export committed reviewed blobs only; no live-checkout copies or Git writes."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

PACKAGING_FILES = (
    'docker/Dockerfile', 'docker/Dockerfile.dockerignore',
    'docker/requirements.linux-amd64.txt', 'docker/dependency-build-tools.txt',
    'docker/dependency-provenance.json', 'docker/dependency_candidate.py',
    'docker/dependency-README.md', 'docker/dependency_tests/test_dependency_candidate.py',
    'docker/packaging_context.py', 'docker/packaging_build.py',
    'docker/packaging-inputs.json', 'docker/packaging-base.json', 'docker/packaging-artifacts.json',
    'docker/packaging-README.md', 'docker/packaging_tests/test_packaging.py',
    'docker/packaging_tests/test_build.py',
)
REQUIRED_SOURCE = {'setup.py', 'README.md', 'LICENSE', 'MANIFEST.in', 'setup.cfg',
                   'pyproject.toml', 'personal_release/VERSION', 'lumibot/__init__.py',
                   'lumibot/resources/conf.yaml', 'personal_trading/__init__.py',
                   'personal_trading/__main__.py', 'personal_trading/cli.py'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode()


def git(repo, *args):
    environment = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C.UTF-8',
                   'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                   'GIT_OPTIONAL_LOCKS': '0'}
    return subprocess.run(['/usr/bin/git', '--no-replace-objects', '-c', 'core.fsmonitor=false',
                           '-C', str(repo), *args], env=environment,
                          check=True, capture_output=True).stdout


def safe_path(name):
    if (not isinstance(name, str) or not name or name.startswith('/') or '\\' in name
            or any(part in ('', '.', '..') or part.startswith('.')
                   for part in name.split('/')) or any(ord(c) < 32 for c in name)):
        raise ValueError('unsafe input path')
    return PurePosixPath(name)


def export_context(repo, destination):
    repo, destination = Path(repo).resolve(), Path(destination).resolve()
    if git(repo, 'status', '--porcelain=v1', '--untracked-files=all'):
        raise ValueError('export requires a clean committed source tree')
    if Path(git(repo, 'rev-parse', '--show-toplevel').decode().strip()) != repo:
        raise ValueError('source must be repository root')
    if destination == repo or repo in destination.parents or destination in repo.parents:
        raise ValueError('destination must be outside repository')
    if destination.exists():
        raise ValueError('destination must not exist')
    commit = git(repo, 'rev-parse', '--verify', 'HEAD^{commit}').decode().strip()
    tree = {}
    for record in git(repo, 'ls-tree', '-rz', '--full-tree', commit).split(b'\0'):
        if record:
            metadata, path = record.split(b'\t', 1)
            mode, kind, oid = metadata.decode('ascii').split()
            tree[path.decode('utf-8')] = (mode, kind, oid)
    def blob(name):
        safe_path(name)
        if name not in tree or tree[name][0] not in ('100644', '100755') or tree[name][1] != 'blob':
            raise ValueError('required regular committed input missing or linked: ' + name)
        return git(repo, 'cat-file', 'blob', tree[name][2])
    policy = json.loads(blob('docker/packaging-inputs.json'))
    if set(policy) != {'schema_version', 'source'} or type(policy['schema_version']) is not int or policy['schema_version'] != 1:
        raise ValueError('invalid input policy')
    approved = {}
    for entry in policy['source']:
        if set(entry) != {'path', 'sha256'} or not re.fullmatch('[0-9a-f]{64}', entry['sha256']):
            raise ValueError('invalid source record')
        name = entry['path']
        safe_path(name)
        if name in approved or not (name in REQUIRED_SOURCE or name.startswith(('lumibot/', 'personal_trading/'))):
            raise ValueError('unapproved source path')
        approved[name] = entry['sha256']
    if not REQUIRED_SOURCE <= approved.keys():
        raise ValueError('required source inputs missing')
    # New framework/overlay files require explicit review, not glob inclusion.
    actual = {name for name in tree if name.startswith(('lumibot/', 'personal_trading/'))
              and not name.endswith('/.gitignore')}
    if actual != {name for name in approved if name.startswith(('lumibot/', 'personal_trading/'))}:
        raise ValueError('unreviewed framework or overlay paths')
    content = {}
    records = []
    for name in sorted(set(approved) | set(PACKAGING_FILES)):
        data = blob(name)
        if name in approved and digest(data) != approved[name]:
            raise ValueError('reviewed source hash mismatch: ' + name)
        target = name if name in PACKAGING_FILES else 'source/' + name
        content[target] = data
        records.append({'path': target, 'git_path': name, 'git_blob': tree[name][2],
                        'git_mode': tree[name][0], 'sha256': digest(data), 'size': len(data)})
    if digest(Path(__file__).read_bytes()) != digest(content['docker/packaging_context.py']):
        raise ValueError('executing exporter differs from committed exporter')
    # Recheck before materialization; only immutable OIDs were read.
    if git(repo, 'rev-parse', 'HEAD').decode().strip() != commit or git(repo, 'status', '--porcelain=v1', '--untracked-files=all'):
        raise ValueError('source must remain clean and unchanged')
    epoch = int(git(repo, 'show', '-s', '--format=%ct', commit))
    inventory = {'schema_version': 1, 'kind': 'committed-image-inputs', 'source_mode': 'committed',
                 'source_commit': commit, 'source_date_epoch': epoch,
                 'personal_version': content['source/personal_release/VERSION'].decode().strip(),
                 'exporter_sha256': digest(content['docker/packaging_context.py']), 'files': records}
    inventory_bytes = canonical(inventory)
    destination.mkdir(mode=0o700)
    for name, data in dict(content, **{'packaging-inventory.json': inventory_bytes}).items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o644)
        os.utime(path, (epoch, epoch))
    return digest(inventory_bytes)


def verify_context(root, expected_digest):
    """Verify exported inputs against an independently retained inventory digest."""
    root = Path(root)
    if not re.fullmatch('[0-9a-f]{64}', expected_digest):
        raise ValueError('external context digest is required')
    path = root / 'packaging-inventory.json'
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('inventory must be a bounded regular file')
    raw = path.read_bytes()
    if digest(raw) != expected_digest:
        raise ValueError('context digest mismatch')
    try:
        value = json.loads(raw)
        if canonical(value) != raw:
            raise ValueError('inventory must be canonical JSON without duplicate keys')
    except (RecursionError, json.JSONDecodeError) as exc:
        raise ValueError('invalid inventory JSON') from exc
    fields = {'schema_version', 'kind', 'source_mode', 'source_commit', 'source_date_epoch',
              'personal_version', 'exporter_sha256', 'files'}
    if (set(value) != fields or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['kind'] != 'committed-image-inputs' or value['source_mode'] != 'committed'
            or not re.fullmatch('[0-9a-f]{40}', value['source_commit'])
            or type(value['source_date_epoch']) is not int or value['source_date_epoch'] < 0
            or not isinstance(value['files'], list) or not 1 <= len(value['files']) <= 4096):
        raise ValueError('invalid inventory metadata')
    names = set()
    for entry in value['files']:
        if set(entry) != {'path', 'git_path', 'git_blob', 'git_mode', 'sha256', 'size'}:
            raise ValueError('invalid inventory record')
        name = entry['path']
        safe_path(name)
        safe_path(entry['git_path'])
        expected_name = entry['git_path'] if entry['git_path'] in PACKAGING_FILES else 'source/' + entry['git_path']
        if (name != expected_name or name in names or entry['git_mode'] not in ('100644', '100755')
                or not re.fullmatch('[0-9a-f]{40}', entry['git_blob'])
                or not re.fullmatch('[0-9a-f]{64}', entry['sha256'])
                or type(entry['size']) is not int or not 0 <= entry['size'] <= 512 * 1024 * 1024):
            raise ValueError('invalid inventory identity or size')
        names.add(name)
        target = root / name
        if target.is_symlink() or not target.is_file():
            raise ValueError('input must be a regular file, not link')
        if target.stat().st_size != entry['size']:
            raise ValueError('input size mismatch')
        if digest(target.read_bytes()) != entry['sha256']:
            raise ValueError('input hash mismatch')
    actual = set()
    for target in root.rglob('*'):
        if target.is_symlink() or not (target.is_file() or target.is_dir()):
            raise ValueError('linked or nonregular input')
        if target.is_file():
            actual.add(target.relative_to(root).as_posix())
    if actual != names | {'packaging-inventory.json'}:
        raise ValueError('files do not match inventory')
    if not set(PACKAGING_FILES) <= names or not {'source/' + n for n in REQUIRED_SOURCE} <= names:
        raise ValueError('required inputs absent from inventory')
    if value['exporter_sha256'] != digest((root / 'docker/packaging_context.py').read_bytes()):
        raise ValueError('exporter hash mismatch')
    if value['personal_version'] != (root / 'source/personal_release/VERSION').read_text().strip():
        raise ValueError('personal version mismatch')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = export_context(args.repo, args.output)
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(2, 'packaging export refused: ' + str(exc) + '\n')
    print(result)


if __name__ == '__main__':
    main()
