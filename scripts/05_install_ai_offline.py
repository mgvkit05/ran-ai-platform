#!/usr/bin/env python3
"""Install the verified bundle into the prepared project. No internet or inference.
Run: "$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/software/ran-ai-offline-bundle/05_install_ai_offline.py"
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def within(root, relative):
    path = PurePosixPath(relative)
    if path.is_absolute() or '..' in path.parts or '\\' in relative:
        raise ValueError('unsafe bundle path')
    target = (root / relative).resolve()
    target.relative_to(root.resolve())
    return target


def extract_runtime(archive, destination):
    import zstandard
    links = []
    with archive.open('rb') as compressed:
        with zstandard.ZstdDecompressor().stream_reader(compressed) as reader:
            with tarfile.open(fileobj=reader, mode='r|') as source:
                total = 0
                count = 0
                for member in source:
                    count += 1
                    total += member.size
                    if total > 24 * 1024 ** 3 or count > 50000:
                        raise ValueError('runtime archive exceeds installation limits')
                    target = within(destination, member.name)
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif member.isfile():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with closing(source.extractfile(member)) as incoming, target.open('xb') as output:
                            shutil.copyfileobj(incoming, output, 1024 * 1024)
                        target.chmod(0o700 if member.mode & 0o111 else 0o600)
                    elif member.issym():
                        if Path(member.linkname).is_absolute():
                            raise ValueError('absolute runtime symlink')
                        linked = (target.parent / member.linkname).resolve()
                        linked.relative_to(destination.resolve())
                        links.append((target, member.linkname))
                    else:
                        raise ValueError('unsupported runtime archive entry')
    # Links are created last so file extraction cannot follow archive symlinks.
    for target, link in links:
        target.parent.mkdir(parents=True, exist_ok=True)
        (target.parent / link).resolve().relative_to(destination.resolve())
        target.symlink_to(link)


def install(bundle, root, emit):
    if os.geteuid() == 0:
        raise ValueError('run without sudo')
    if sys.version_info[:2] != (3, 9) or Path(sys.prefix).resolve() != (root / 'venv').resolve():
        raise ValueError('use the prepared Python 3.9 virtual environment')
    root.resolve().relative_to(Path('/home'))
    bundle.resolve().relative_to((root / 'software').resolve())
    if root.stat().st_dev == Path('/').stat().st_dev:
        raise ValueError('project must not be on the root filesystem')
    metadata = json.loads((bundle / 'bundle.json').read_text(encoding='utf-8'))
    version = metadata['ollama_version']
    if metadata['schema'] != 1 or metadata['model'] != 'gpt-oss:20b' or not re.fullmatch(r'v[0-9][0-9A-Za-z.\-]*', version):
        raise ValueError('unsupported bundle metadata')
    emit('Verifying all bundle checksums. This reads the full model and may take several minutes.')
    for item in metadata['files']:
        path = within(bundle, item['path'])
        if not path.is_file() or sha256(path) != item['sha256']:
            raise ValueError('bundle checksum failed: ' + item['path'])
    if shutil.disk_usage(root).free < 60 * 1024 ** 3:
        raise ValueError('at least 60 GiB free is required for runtime extraction and model copy')
    runtime = root / 'software' / ('ollama-' + version)
    models = root / 'models' / 'ollama'
    if runtime.is_symlink() or models.is_symlink():
        raise ValueError('installation destinations must not be symlinks')
    environment = dict(os.environ, TMPDIR=str(root / 'tmp'), PIP_CACHE_DIR=str(root / 'cache' / 'pip'),
                       PIP_NO_INDEX='1', PIP_DISABLE_PIP_VERSION_CHECK='1')
    emit('Installing only the offline archive decompressor into the project venv.')
    result = subprocess.run([sys.executable, '-m', 'pip', '--isolated', 'install', '--no-index', '--no-deps',
                             '--cache-dir', str(root / 'cache' / 'pip'),
                             '--find-links', str(bundle / 'wheels'), 'zstandard==0.23.0'],
                            env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    emit(result.stdout)
    result.check_returncode()
    importlib.invalidate_caches()
    if not runtime.exists():
        emit('Extracting Ollama into the project software directory.')
        with tempfile.TemporaryDirectory(prefix='ollama-unpack-', dir=str(root / 'software')) as temporary:
            extracted = Path(temporary) / 'runtime'
            extracted.mkdir()
            extract_runtime(bundle / 'ollama-linux-amd64.tar.zst', extracted)
            if not (extracted / 'bin' / 'ollama').is_file():
                raise ValueError('runtime binary missing from archive')
            extracted.rename(runtime)
    binary = runtime / 'bin' / 'ollama'
    emit('Checking Linux binary compatibility; no model is loaded.')
    environment.update(OLLAMA_HOST='127.0.0.1:11435', OLLAMA_NO_CLOUD='1', OLLAMA_MODELS=str(models))
    result = subprocess.run([str(binary), '--version'], env=environment,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)
    emit(result.stdout)
    result.check_returncode()
    emit('Copying verified model store into the project models directory.')
    for incoming in (bundle / 'models').rglob('*'):
        relative = incoming.relative_to(bundle / 'models')
        destination = within(models, relative.as_posix())
        if incoming.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif incoming.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if sha256(destination) != sha256(incoming):
                    raise ValueError('existing model file differs; nothing overwritten')
            else:
                partial = destination.with_name(destination.name + '.install-part')
                with incoming.open('rb') as source, partial.open('wb') as output:
                    shutil.copyfileobj(source, output, 4 * 1024 * 1024)
                if sha256(partial) != sha256(incoming):
                    raise ValueError('model copy checksum failed')
                partial.replace(destination)
    config = {'binary': str(binary), 'models': str(models), 'model': 'gpt-oss:20b',
              'host': '127.0.0.1:11435', 'inference_started': False}
    (root / 'config' / 'ollama-offline.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
    emit('SUCCESS: Ollama and GPT-OSS 20B are installed locally.')
    emit('No service or inference started. Share the version output before the bounded inference test.')


def main():
    os.umask(0o077)
    root = Path.home() / 'ran-ai'
    bundle = Path(__file__).resolve().parent
    log_path = root / 'logs' / ('05-ai-install-%s-%d.log' %
                               (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'), os.getpid()))
    try:
        with log_path.open('x', encoding='utf-8') as log:
            def emit(message):
                print(message, flush=True)
                log.write(message + '\n')
                log.flush()
            try:
                install(bundle, root, emit)
            except Exception as error:
                emit('FAILED: %s: %s' % (type(error).__name__, error))
                return 1
    except OSError as error:
        print('Cannot write project log. Confirm Phase 2 succeeded: %s' % error, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
