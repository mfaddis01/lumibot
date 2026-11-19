"""Build a full-code local wheel without running destructive upstream setup hooks.

No dependency reduction, stub modules, source rewrite or public-release identity.
All regular package files (except bytecode) are included byte-for-byte.
"""
import argparse
import ast
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import zipfile

VERSION = '4.6.64+source8dbcf8f.1'
DIST = f'lumibot-{VERSION}.dist-info'


def build(source: Path, output: Path) -> Path:
    source = source.resolve()
    tree = ast.parse((source / 'setup.py').read_text())
    setup = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == 'setup')
    fields = {k.arg: k.value for k in setup.keywords}
    if ast.literal_eval(fields['version']) != '4.6.64':
        raise ValueError('Unexpected framework version; requalify')
    requirements = ast.literal_eval(fields['install_requires'])
    extras = ast.literal_eval(fields['extras_require'])
    payload = {}
    for path in sorted((source / 'lumibot').rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlink in source package; refuse')
        if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.pyc', '.pyo'):
            payload[path.relative_to(source).as_posix()] = path.read_bytes()
    if len(payload) < 100:
        raise ValueError('Incomplete framework source')
    identity = {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}
    for name in ('setup.py', 'setup.cfg', 'pyproject.toml', 'LICENSE', 'README.md'):
        identity[name] = hashlib.sha256((source / name).read_bytes()).hexdigest()
    pinned = json.loads((Path(__file__).with_name('framework-source-manifest.json')).read_text())
    if identity != pinned['files']:
        raise ValueError('Framework source mismatch; requalify before assigning pinned identity')
    metadata = [
        'Metadata-Version: 2.1', 'Name: lumibot', f'Version: {VERSION}',
        'Summary: Local full-source LumiBot qualification build; not a published upstream wheel',
        'License: GPL-3.0', 'Requires-Python: >=3.10',
        'X-Local-Packaging-Disposition: full unchanged source; static setup metadata; no setup hooks executed',
    ]
    metadata += [f'Requires-Dist: {req}' for req in requirements]
    # Preserve optional declarations as well; none are enabled for this qualification.
    for extra, deps in extras.items():
        metadata.append(f'Provides-Extra: {extra}')
        metadata += [f'Requires-Dist: {dep}; extra == "{extra}"' for dep in deps]
    payload[f'{DIST}/METADATA'] = ('\n'.join(metadata) + '\n\nLocal source qualification build.\n').encode()
    payload[f'{DIST}/WHEEL'] = b'Wheel-Version: 1.0\nGenerator: inspected-static-local-builder\nRoot-Is-Purelib: true\nTag: py3-none-any\n'
    payload[f'{DIST}/entry_points.txt'] = b'[console_scripts]\nlumibot=lumibot.cli:main\n'
    payload[f'{DIST}/LICENSE'] = (source / 'LICENSE').read_bytes()
    payload[f'{DIST}/source-manifest.json'] = (json.dumps({
        'owner_supplied_commit_label': '8dbcf8fab2b89f1b596fa64ec07cd47c41e284f4',
        'version': VERSION, 'files': identity,
        'disposition': 'local full-code packaging derivative; upstream setup.py never executed',
    }, indent=2, sort_keys=True) + '\n').encode()
    record = io.StringIO(newline='')
    rows = csv.writer(record, lineterminator='\n')
    for name, data in sorted(payload.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        rows.writerow((name, f'sha256={digest}', len(data)))
    rows.writerow((f'{DIST}/RECORD', '', ''))
    payload[f'{DIST}/RECORD'] = record.getvalue().encode()
    output.mkdir(parents=True, exist_ok=True)
    wheel = output / f'lumibot-{VERSION}-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(payload.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return wheel


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(build(args.source, args.output))
