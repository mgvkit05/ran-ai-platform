#!/usr/bin/env python3
"""Offline, unprivileged ClickHouse binary installation. Does not start a database.
Run using $HOME/ran-ai/venv/bin/python after transferring the full bundle.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def unpack_binary(package, output):
    # Read only the expected executable. Never extract paths, symlinks or install scripts.
    with tarfile.open(package, 'r:gz') as archive:
        matches = [item for item in archive.getmembers()
                   if re.fullmatch(r'clickhouse-common-static-[0-9.]+/usr/bin/clickhouse', item.name)]
        if len(matches) != 1 or not matches[0].isfile():
            raise ValueError('expected one regular ClickHouse binary in package')
        member = matches[0]
        if not 0 < member.size <= 3 * 1024 ** 3:
            raise ValueError('unexpected executable size')
        with archive.extractfile(member) as incoming, output.open('xb') as target:
            shutil.copyfileobj(incoming, target, 4 * 1024 * 1024)
    output.chmod(0o700)


def install(root, bundle, emit):
    if not sys.platform.startswith('linux') or os.geteuid() == 0:
        raise ValueError('run on the Linux AI server without sudo')
    root.resolve().relative_to(Path('/home'))
    bundle.resolve().relative_to((root / 'software').resolve())
    if root.stat().st_dev == Path('/').stat().st_dev:
        raise ValueError('project must not be on the root filesystem')
    metadata = json.loads((bundle / 'clickhouse-bundle.json').read_text(encoding='utf-8'))
    version = metadata['version']
    if metadata['schema'] != 1 or not re.fullmatch(r'v[0-9.]+-(stable|lts)', version):
        raise ValueError('unsupported bundle version')
    name = metadata['package']
    if not re.fullmatch(r'clickhouse-common-static-[0-9.]+-amd64\.tgz', name):
        raise ValueError('unsupported package name')
    package = bundle / name
    emit('Verifying package and installer checksums.')
    if sha256(package) != metadata['sha256'] or sha256(bundle / '09_install_clickhouse_offline.py') != metadata['installer_sha256']:
        raise ValueError('bundle checksum mismatch')
    if shutil.disk_usage(root).free < 5 * 1024 ** 3:
        raise ValueError('at least 5 GiB free is required')
    destination = root / 'software' / ('clickhouse-' + version)
    if destination.is_symlink():
        raise ValueError('runtime destination is a symlink')
    destination.mkdir(exist_ok=True)
    binary = destination / 'clickhouse'
    with tempfile.TemporaryDirectory(prefix='clickhouse-unpack-', dir=str(root / 'software')) as temporary:
        candidate = Path(temporary) / 'clickhouse'
        emit('Reading the standalone executable from the verified package.')
        unpack_binary(package, candidate)
        if binary.exists():
            if binary.is_symlink() or sha256(binary) != sha256(candidate):
                raise ValueError('existing binary differs; nothing overwritten')
            emit('Existing identical executable reused.')
        else:
            candidate.rename(binary)
    emit('Checking executable compatibility; no database server will start.')
    environment = dict(os.environ, TMPDIR=str(root / 'tmp'))
    result = subprocess.run([str(binary), 'client', '--version'], env=environment,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)
    emit(result.stdout.strip())
    result.check_returncode()
    config = {'binary': str(binary), 'version': version,
              'data': str(root / 'database' / 'clickhouse'), 'server_started': False}
    (root / 'config' / 'clickhouse-offline.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
    emit('SUCCESS: ClickHouse executable installed under the project software directory.')
    emit('No database service started. Existing Ollama instance is unchanged.')
    emit('Share the version output before the local database startup phase.')


def main():
    os.umask(0o077)
    root = Path.home() / 'ran-ai'
    bundle = Path(__file__).resolve().parent
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-%d' % os.getpid()
    try:
        with (root / 'logs' / ('09-clickhouse-install-' + stamp + '.log')).open('x', encoding='utf-8') as log:
            def emit(message):
                print(message, flush=True)
                log.write(message + '\n')
                log.flush()
            try:
                install(root, bundle, emit)
            except Exception as error:
                emit('FAILED: %s: %s' % (type(error).__name__, error))
                return 1
    except OSError as error:
        print('Cannot write project log: %s' % error, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
