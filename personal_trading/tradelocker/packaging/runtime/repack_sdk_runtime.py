"""Experimental LOCAL metadata derivative for full runtime qualification only.

Do not publish the derived wheel as upstream. The adapter accepts this exact
local identity only with pinned installed edges and all preserved SDK hashes.
Preserves all SDK code/license bytes. Changes Version, PyJWT and requests edges,
explicit provenance and RECORD only. Does not install anything.
"""
import argparse
import base64
import csv
import hashlib
import io
from pathlib import Path
import zipfile

UPSTREAM_SHA256 = '4463bfd66f49c5cb957418925f25f93372310eb2411073c7d01a156d1a360759'
VERSION = '0.56.2+lumibot.runtime.1'


def repack(upstream: Path, output: Path) -> Path:
    original = upstream.read_bytes()
    if hashlib.sha256(original).hexdigest() != UPSTREAM_SHA256:
        raise ValueError('Unqualified original SDK wheel')
    old = 'tradelocker-0.56.2.dist-info/'
    new = f'tradelocker-{VERSION}.dist-info/'
    entries = {}
    with zipfile.ZipFile(io.BytesIO(original)) as archive:
        for name in archive.namelist():
            if name == old + 'RECORD':
                continue
            data = archive.read(name)
            if name == old + 'METADATA':
                text = data.decode()
                for before, after in (
                    ('Version: 0.56.2\n', f'Version: {VERSION}\n'),
                    ('Requires-Dist: PyJWT (==2.8.0)\n', 'Requires-Dist: PyJWT[crypto]==2.15.1\n'),
                    ('Requires-Dist: requests (==2.32.2)\n', 'Requires-Dist: requests==2.34.2\n'),
                ):
                    if text.count(before) != 1:
                        raise ValueError('Unexpected SDK metadata')
                    text = text.replace(before, after)
                text = text.replace('\n\n', '\nX-Local-Packaging-Override: metadata-only JWT+requests; upstream-sha256=' + UPSTREAM_SHA256 + '\n\n', 1)
                data = text.encode()
            entries[name.replace(old, new, 1)] = data
    record = io.StringIO(newline='')
    rows = csv.writer(record, lineterminator='\n')
    for name, data in sorted(entries.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        rows.writerow((name, f'sha256={digest}', len(data)))
    rows.writerow((new + 'RECORD', '', ''))
    entries[new + 'RECORD'] = record.getvalue().encode()
    output.mkdir(parents=True, exist_ok=True)
    target = output / f'tradelocker-{VERSION}-py3-none-any.whl'
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('upstream', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = repack(args.upstream, args.output)
    print(result)
    print('sha256=' + hashlib.sha256(result.read_bytes()).hexdigest())
