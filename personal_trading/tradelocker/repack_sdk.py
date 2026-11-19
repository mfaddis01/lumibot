"""Build an explicitly LOCAL metadata-only SDK wheel; never publish as upstream.

Input is an exact hash-pinned public wheel. All SDK sources and license stay
unchanged. Only distribution Version, JWT requirement, provenance header and
wheel RECORD change. No installation, network requests or setup hooks execute.
"""
import argparse
import base64
import csv
import hashlib
import io
from pathlib import Path
import zipfile

UPSTREAM_FILENAME = 'tradelocker-0.56.2-py3-none-any.whl'
UPSTREAM_SHA256 = '4463bfd66f49c5cb957418925f25f93372310eb2411073c7d01a156d1a360759'
LOCAL_VERSION = '0.56.2+lumibot.jwt2151.1'
LOCAL_FILENAME = f'tradelocker-{LOCAL_VERSION}-py3-none-any.whl'


def repack(upstream: Path, output_dir: Path) -> Path:
    original = upstream.read_bytes()
    if hashlib.sha256(original).hexdigest() != UPSTREAM_SHA256:
        raise ValueError('Unqualified upstream wheel')
    old = 'tradelocker-0.56.2.dist-info/'
    new = f'tradelocker-{LOCAL_VERSION}.dist-info/'
    entries = {}
    with zipfile.ZipFile(io.BytesIO(original)) as source:
        for name in source.namelist():
            if name == old + 'RECORD':
                continue
            data = source.read(name)
            if name == old + 'METADATA':
                text = data.decode('utf-8')
                assert text.count('Version: 0.56.2\n') == 1
                assert text.count('Requires-Dist: PyJWT (==2.8.0)\n') == 1
                text = text.replace('Version: 0.56.2\n', f'Version: {LOCAL_VERSION}\n')
                text = text.replace('Requires-Dist: PyJWT (==2.8.0)\n', 'Requires-Dist: PyJWT[crypto]==2.15.1\n')
                text = text.replace('\n\n', '\nX-Local-Packaging-Override: metadata-only; upstream-sha256=' + UPSTREAM_SHA256 + '\n\n', 1)
                data = text.encode('utf-8')
            entries[name.replace(old, new, 1)] = data
    record = io.StringIO(newline='')
    writer = csv.writer(record, lineterminator='\n')
    for name, data in entries.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        writer.writerow((name, 'sha256=' + digest, str(len(data))))
    writer.writerow((new + 'RECORD', '', ''))
    entries[new + 'RECORD'] = record.getvalue().encode()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / LOCAL_FILENAME
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as wheel:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name, date_time=(2024, 12, 18, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            wheel.writestr(info, data)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('upstream', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    path = repack(args.upstream, args.output_dir)
    print(path)
    print('sha256=' + hashlib.sha256(path.read_bytes()).hexdigest())
