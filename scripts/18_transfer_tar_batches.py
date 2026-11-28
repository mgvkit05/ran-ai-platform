#!/usr/bin/env python3
"""Collect on an intermediate Linux server; push complete batches to the AI server.

Intermediate server (Python 3.6+ and OpenSSH client):
  python3 "$HOME/ran-ai-transfer/18_transfer_tar_batches.py" init-config
  python3 "$HOME/ran-ai-transfer/18_transfer_tar_batches.py" plan
  python3 "$HOME/ran-ai-transfer/18_transfer_tar_batches.py" run
AI server (keep 14_ingest_vendor_tar.py beside this script):
  "$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/18_transfer_tar_batches.py" ingest

Arguments: action {init-config,plan,run,ingest}; --config PATH (default
$HOME/ran-ai-transfer/config.json); --spool PATH (default $HOME/ran-ai-transfer/spool);
--inbox PATH (AI ingest only, default $HOME/ran-ai/data/landing/server-batches).
Run as a normal user, without sudo. Configure cron separately for 15-minute runs.
Each endpoint accepts user/password or an optional identity_file. Passwords stay
in the private local config (chmod 600), never in manifests or command arguments.
Preverified known_hosts are required for both authentication methods.
Original source archives are never deleted. No model or new Python package needed.
Collection selects only the newest eligible TAR per producer (mtime, then filename
for ties). Per-source cursors prevent historical backfill after the initial pull.
"""
import argparse
import contextlib
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid

SUFFIXES = ('.tar', '.tgz', '.tar.gz', '.tar.bz2', '.tar.xz')
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')
HASH = re.compile(r'[a-f0-9]{64}')
PROVENANCE = 'ran_ai.archive_sources_v1'
VERSION = 'tar-transfer-v5'


def report_failure(error, stage):
    """Print only fixed descriptions and code locations, never exception payloads."""
    detail = str(error).lower()
    code, hint = 'UNCLASSIFIED', 'Review the private log for the underlying error.'
    if isinstance(error, json.JSONDecodeError):
        if stage == 'read-config':
            code, hint = 'CONFIG_JSON', 'Invalid JSON at line %d, column %d. Check commas, braces and quoting.' % (error.lineno, error.colno)
        else:
            code, hint = 'REMOTE_OUTPUT', 'Remote command returned non-JSON output. Check remote Python and shell startup/banner output in the private log.'
    elif 'password_config_must_be_owned' in detail:
        code, hint = 'CONFIG_PERMISSIONS', 'Run chmod 600 on config.json and use the account that owns it.'
    elif 'host key verification failed' in detail or 'remote host identification has changed' in detail:
        code, hint = 'SSH_HOST_KEY', 'Verify this host fingerprint and populate known_hosts for the collector account.'
    elif 'permission denied' in detail and ('publickey' in detail or 'password' in detail):
        code, hint = 'SSH_AUTH', 'Verify the endpoint user/password and that SSH permits password authentication.'
    elif 'could not resolve hostname' in detail:
        code, hint = 'SSH_HOSTNAME', 'Check the configured hostname/address and name resolution.'
    elif any(text in detail for text in ('connection refused', 'no route to host', 'network is unreachable', 'connection timed out')):
        code, hint = 'SSH_CONNECTION', 'Check network reachability and the configured SSH port from the intermediate server.'
    elif 'command not found' in detail or 'python3: not found' in detail:
        code, hint = 'REMOTE_PYTHON', 'Set the endpoint python field to its installed Python 3 executable.'
    elif 'filenotfounderror' in detail and 'traceback' in detail:
        code, hint = 'REMOTE_PATH', 'Check that the producer directory exists and the endpoint python path is valid.'
    elif 'permissionerror' in detail and 'traceback' in detail:
        code, hint = 'REMOTE_PERMISSIONS', 'Check producer directory/file read permissions for the configured user.'
    elif isinstance(error, FileNotFoundError):
        code, hint = 'LOCAL_FILE_MISSING', 'Check config/script paths and that the ssh client is installed.'
    elif isinstance(error, ValueError) and re.fullmatch('[a-z_]+', str(error)):
        code, hint = 'CONFIG_OR_VALIDATION', str(error)
    print('Diagnostic: %s; stage=%s; code=%s; error=%s' % (VERSION, stage, code, type(error).__name__), flush=True)
    print(hint, flush=True)
    for frame in traceback.extract_tb(error.__traceback__)[-4:]:
        print('  at %s:%d in %s' % (Path(frame.filename).name, frame.lineno, frame.name), flush=True)

# Sent as fixed Python code over SSH. Requests are JSON on stdin, never shell code.
REMOTE = r'''
import fnmatch,hashlib,json,os,pathlib,re,stat,sys,time
os.umask(0o077)
q=json.loads(sys.stdin.buffer.readline())
def checksum(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(4194304),b''): h.update(b)
    return h.hexdigest()
def safe_id(s):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',s): raise ValueError('invalid_id')
def snapshot(p):
    s=p.lstat()
    if not stat.S_ISREG(s.st_mode): raise ValueError('not_regular_file')
    return [s.st_size,s.st_mtime_ns,s.st_ino]
mode=q['operation']
if mode in ('list','plan','fetch'):
    root=pathlib.Path(q['directory']).resolve(strict=True)
    if mode in ('list','plan'):
        result=[]
        newest=None
        after=q.get('after')
        for p in root.iterdir():
            if p.is_symlink() or not p.is_file(): continue
            if not p.name.lower().endswith(('.tar','.tgz','.tar.gz','.tar.bz2','.tar.xz')): continue
            if not any(fnmatch.fnmatchcase(p.name,pattern) for pattern in q['patterns']): continue
            before=snapshot(p)
            if int(time.time()*1000000000)-before[1]<q['min_age_seconds']*1000000000: continue
            key=(before[1],p.name)
            if after is not None and key<=(after['mtime_ns'],after['name']): continue
            if newest is None or key>newest[0]: newest=(key,p,before)
        if newest is not None:
            key,p,before=newest
            if before[0]>q['max_archive_bytes']: raise ValueError('archive_size_limit')
            digest=checksum(p) if mode=='list' else None
            if snapshot(p)!=before: raise ValueError('source_changed_during_scan')
            result.append(dict(name=p.name,sha256=digest,size=before[0],mtime_ns=before[1],inode=before[2]))
        print(json.dumps(result))
    else:
        name=q['file']['name']
        if pathlib.Path(name).name!=name or '/' in name or '\\' in name: raise ValueError('invalid_name')
        p=root/name
        expected=[q['file']['size'],q['file']['mtime_ns'],q['file']['inode']]
        if snapshot(p)!=expected: raise ValueError('source_changed_before_copy')
        h=hashlib.sha256()
        copied=0
        with p.open('rb') as f:
            for b in iter(lambda:f.read(4194304),b''):
                copied+=len(b)
                if copied>q['file']['size']: raise ValueError('source_grew_during_copy')
                h.update(b);sys.stdout.buffer.write(b)
        sys.stdout.buffer.flush()
        if snapshot(p)!=expected or h.hexdigest()!=q['file']['sha256']: raise ValueError('source_changed_during_copy')
elif mode in ('put','publish'):
    root=pathlib.Path(q['directory']).resolve(strict=True)
    safe_id(q['batch_id'])
    batch=root/q['batch_id']
    if batch.is_symlink(): raise ValueError('batch_symlink')
    batch.mkdir(mode=0o700,exist_ok=True)
    if mode=='put':
        safe_id(q['source_id'])
        digest=q['sha256']
        if not re.fullmatch(r'[a-f0-9]{64}',digest): raise ValueError('invalid_hash')
        folder=batch/q['source_id']
        if folder.is_symlink(): raise ValueError('source_symlink')
        folder.mkdir(mode=0o700,exist_ok=True)
        final=folder/(digest+'.tar');temporary=folder/(digest+'.part')
        if final.is_symlink() or temporary.is_symlink(): raise ValueError('archive_symlink')
        h=hashlib.sha256();size=0
        with temporary.open('wb') as f:
            while True:
                b=sys.stdin.buffer.read(4194304)
                if not b: break
                size+=len(b)
                if size>q['size']: raise ValueError('size_exceeded')
                h.update(b);f.write(b)
            f.flush();os.fsync(f.fileno())
        if size!=q['size'] or h.hexdigest()!=digest: raise ValueError('copy_checksum_mismatch')
        if final.exists() and checksum(final)==digest:
            temporary.unlink()
        elif (batch/'.ready').exists():
            raise ValueError('published_archive_changed')
        else:
            temporary.replace(final)
    else:
        manifest=q['manifest']
        if manifest['batch_id']!=q['batch_id']: raise ValueError('batch_mismatch')
        expected=set(manifest['sources']);received=set()
        if not expected or not manifest['archives']: raise ValueError('empty_batch')
        for item in manifest['archives']:
            safe_id(item['source_id']);received.add(item['source_id'])
            if not re.fullmatch(r'[a-f0-9]{64}',item['sha256']): raise ValueError('invalid_hash')
            p=batch/item['source_id']/(item['sha256']+'.tar')
            if p.is_symlink() or p.parent.is_symlink(): raise ValueError('archive_symlink')
            if p.stat().st_size!=item['size'] or checksum(p)!=item['sha256']: raise ValueError('incomplete_batch')
        if expected!=received: raise ValueError('missing_source')
        content=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
        manifest_path=batch/'manifest.json';ready=batch/'.ready'
        if manifest_path.is_symlink() or ready.is_symlink(): raise ValueError('manifest_symlink')
        if ready.exists():
            if manifest_path.read_bytes()!=content: raise ValueError('published_batch_changed')
        else:
            temporary=batch/'manifest.tmp'
            if temporary.is_symlink(): raise ValueError('manifest_symlink')
            temporary.write_bytes(content);temporary.replace(manifest_path)
            marker=batch/'.ready.tmp'
            if marker.is_symlink(): raise ValueError('manifest_symlink')
            marker.write_text(hashlib.sha256(content).hexdigest());marker.replace(ready)
    print('OK')
else: raise ValueError('unknown_operation')
'''


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(4194304), b''):
            h.update(chunk)
    return h.hexdigest()


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as output:
        json.dump(value, output, sort_keys=True, separators=(',', ':'))
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def endpoint(value):
    for field in ('host', 'user', 'directory'):
        if not isinstance(value.get(field), str) or not value[field]:
            raise ValueError('endpoint_requires_host_user_directory')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]*', value['host']):
        raise ValueError('invalid_ssh_host')
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', value['user']):
        raise ValueError('invalid_ssh_user')
    if not value['directory'].startswith('/'):
        raise ValueError('directory_must_be_absolute')
    if type(value.get('port', 22)) is not int or not 1 <= value.get('port', 22) <= 65535:
        raise ValueError('invalid_ssh_port')
    if 'private_key' in value:
        raise ValueError('use_identity_file_not_embedded_private_keys')
    if 'password' in value:
        password = value['password']
        if not isinstance(password, str) or not password or any(char in password for char in '\r\n\0'):
            raise ValueError('password_must_be_nonempty_single_line_text')
        if value.get('identity_file'):
            raise ValueError('choose_password_or_identity_file_for_each_endpoint')
    if not re.fullmatch(r'[A-Za-z0-9_/][A-Za-z0-9_./-]*', value.get('python', 'python3')):
        raise ValueError('invalid_remote_python_executable')


def load_config(path):
    config = json.loads(path.read_text(encoding='utf-8'))
    if sys.platform.startswith('linux') and any('password' in item for item in [config['ai']] + config['sources']):
        details = path.stat()
        if details.st_uid != os.geteuid() or stat.S_IMODE(details.st_mode) & 0o077:
            raise ValueError('password_config_must_be_owned_by_current_user_and_chmod_600')
    endpoint(config['ai'])
    sources = config['sources']
    if not sources:
        raise ValueError('configure_at_least_one_source')
    seen = set()
    for source in sources:
        endpoint(source)
        if not ID.fullmatch(source.get('id', '')) or source['id'] in seen:
            raise ValueError('source_ids_must_be_unique_safe_names')
        seen.add(source['id'])
        patterns = source.get('patterns', ['*.tar'])
        if not isinstance(patterns, list) or not patterns or not all(isinstance(x, str) and x for x in patterns):
            raise ValueError('invalid_patterns')
        for key, default, minimum in (('min_age_seconds', 120, 0), ('max_archive_bytes', 10737418240, 1)):
            if type(source.get(key, default)) is not int or source.get(key, default) < minimum:
                raise ValueError('invalid_source_limits')
    if type(config.get('timeout_seconds', 1800)) is not int or config.get('timeout_seconds', 1800) < 1:
        raise ValueError('invalid_timeout')
    return config


@contextlib.contextmanager
def ssh_auth(target, helper_root=None):
    """Create a secret-free askpass launcher; password exists only in child env."""
    environment = os.environ.copy()
    for name in ('RAN_AI_SSH_PASSWORD', 'RAN_AI_ASKPASS_ACTIVE'):
        environment.pop(name, None)
    if 'password' not in target:
        yield ['-o', 'BatchMode=yes'], environment, False
        return
    helper_root = helper_root or Path.home() / '.cache/ran-ai-transfer'
    helper_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='askpass-', dir=str(helper_root)) as temporary:
        helper = Path(temporary) / 'askpass'
        helper.write_text('#!/bin/sh\nexec %s %s --_ssh-askpass "$@"\n' %
                          (shlex.quote(sys.executable), shlex.quote(str(Path(__file__).resolve()))), encoding='utf-8')
        helper.chmod(0o700)
        environment.update(SSH_ASKPASS=str(helper), SSH_ASKPASS_REQUIRE='force',
                           DISPLAY=environment.get('DISPLAY') or 'ran-ai-askpass:0',
                           RAN_AI_SSH_PASSWORD=target['password'], RAN_AI_ASKPASS_ACTIVE='1')
        yield ['-o', 'BatchMode=no', '-o', 'PreferredAuthentications=password,keyboard-interactive',
               '-o', 'PubkeyAuthentication=no', '-o', 'NumberOfPasswordPrompts=1'], environment, True


def askpass(prompt, environment):
    if environment.get('RAN_AI_ASKPASS_ACTIVE') != '1' or 'RAN_AI_SSH_PASSWORD' not in environment:
        return 1
    # Never answer host-key, passphrase, OTP or other unexpected interactive prompts.
    if not re.search(r'password', prompt, re.I):
        return 1
    print(environment['RAN_AI_SSH_PASSWORD'])
    return 0


def ssh_call(target, request, timeout, incoming=None, outgoing=None):
    with ssh_auth(target) as (auth_options, environment, detached):
        return ssh_exchange(target, request, timeout, incoming, outgoing, auth_options, environment, detached)


def ssh_exchange(target, request, timeout, incoming, outgoing, auth_options, environment, detached):
    command = ['ssh', '-T', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'ConnectTimeout=20', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
               '-p', str(target.get('port', 22))] + auth_options
    if target.get('identity_file'):
        command += ['-i', str(Path(target['identity_file']).expanduser())]
    command += [target['user'] + '@' + target['host'], shlex.quote(target.get('python', 'python3')) + ' -c ' + shlex.quote(REMOTE)]
    request = dict(request, directory=target['directory'])
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                   stdout=outgoing if outgoing is not None else subprocess.PIPE, stderr=errors,
                                   env=environment, start_new_session=detached)
        expired = threading.Event()
        def terminate():
            expired.set()
            process.kill()
        timer = threading.Timer(timeout, terminate)
        timer.start()
        stopped = threading.Event()
        started = time.monotonic()
        labels = {'plan': 'remote directory listing', 'list': 'remote inventory/checksums',
                  'fetch': 'archive download', 'put': 'archive upload', 'publish': 'batch verification/publication'}
        label = labels.get(request.get('operation'), 'SSH operation')
        def heartbeat():
            while not stopped.wait(15):
                print('[%ds elapsed: waiting for %s; timeout=%ds]' %
                      (int(time.monotonic() - started), label, timeout), flush=True)
        threading.Thread(target=heartbeat, daemon=True).start()
        try:
            process.stdin.write(json.dumps(request).encode() + b'\n')
            if incoming is not None:
                shutil.copyfileobj(incoming, process.stdin, 4194304)
            process.stdin.close()
            result = process.stdout.read() if outgoing is None else b''
            process.wait()
            if process.returncode or expired.is_set():
                errors.seek(0)
                detail = errors.read(8192).decode(errors='replace')
                if target.get('password'):
                    detail = detail.replace(target['password'], '[REDACTED]')
                raise RuntimeError('ssh_failed_or_timed_out: ' + detail)
            return result
        finally:
            stopped.set()
            timer.cancel()
            if process.poll() is None:
                process.kill()
            process.wait()


def validate_file(item):
    name = item['name']
    if not isinstance(name, str) or Path(name).name != name or '/' in name or '\\' in name or not name.lower().endswith(SUFFIXES):
        raise ValueError('invalid_source_filename')
    if not HASH.fullmatch(item['sha256']) or type(item['size']) is not int or item['size'] < 0:
        raise ValueError('invalid_source_file_metadata')


def inventory_request(source, spool, operation):
    scope = {key: source[key] for key in ('host', 'user', 'directory')}
    scope['patterns'] = source.get('patterns', ['*.tar'])
    scope_hash = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
    request = dict(operation=operation, patterns=scope['patterns'],
                   min_age_seconds=source.get('min_age_seconds', 120),
                   max_archive_bytes=source.get('max_archive_bytes', 10737418240))
    cursor_path = spool / 'cursors' / (source['id'] + '.json')
    if cursor_path.exists():
        cursor = json.loads(cursor_path.read_text())
        if cursor['scope_hash'] == scope_hash:
            request['after'] = dict(mtime_ns=cursor['mtime_ns'], name=cursor['name'])
    return request, cursor_path, scope_hash


def collect_source(source, spool, timeout):
    request, cursor_path, scope_hash = inventory_request(source, spool, 'list')
    items = json.loads(ssh_call(source, request, timeout))
    if len(items) > 1:
        raise ValueError('newest_only_inventory_returned_multiple_files')
    for item in items:
        validate_file(item)
        directory = spool / 'cache' / source['id']
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (item['sha256'] + '.tar')
        if not path.exists() or digest(path) != item['sha256']:
            temporary = path.with_suffix('.part')
            with temporary.open('wb') as output:
                ssh_call(source, {'operation': 'fetch', 'file': item}, timeout, outgoing=output)
                output.flush()
                os.fsync(output.fileno())
            if temporary.stat().st_size != item['size'] or digest(temporary) != item['sha256']:
                raise ValueError('download_checksum_mismatch')
            temporary.replace(path)
        identity = dict(source_id=source['id'], host=source['host'], user=source['user'],
                        source_path=source['directory'].rstrip('/') + '/' + item['name'], sha256=item['sha256'])
        origin_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        record_path = spool / 'records' / (origin_id + '.json')
        if not record_path.exists():
            record = dict(identity, origin_id=origin_id, size=item['size'], mtime_ns=item['mtime_ns'],
                          collected_utc=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
            save_json(record_path, record)
        # Advance only after both archive and origin record are durably retained.
        save_json(cursor_path, dict(scope_hash=scope_hash, mtime_ns=item['mtime_ns'], name=item['name']))
    return len(items)


def validate_manifest(manifest):
    if manifest.get('version') != 1 or not ID.fullmatch(manifest['batch_id']):
        raise ValueError('invalid_batch_manifest')
    sources = manifest['sources']
    if not sources or len(sources) != len(set(sources)) or not all(ID.fullmatch(s) for s in sources):
        raise ValueError('invalid_batch_sources')
    records = manifest['archives']
    if not records or len(records) > 10000:
        raise ValueError('invalid_batch_archive_count')
    seen = set()
    for item in records:
        if item['source_id'] not in sources or not HASH.fullmatch(item['sha256']) or not HASH.fullmatch(item['origin_id']):
            raise ValueError('invalid_batch_record')
        if item['origin_id'] in seen or type(item['size']) is not int or item['size'] < 0:
            raise ValueError('invalid_batch_record')
        seen.add(item['origin_id'])
    if set(sources) != {r['source_id'] for r in records}:
        raise ValueError('batch_missing_source')


def prepare_batch(config, spool, delivered):
    sources = [s['id'] for s in config['sources']]
    records = []
    for path in sorted((spool / 'records').glob('*.json')):
        item = json.loads(path.read_text())
        if item['origin_id'] not in delivered and item['source_id'] in sources:
            records.append(item)
    if set(sources) != {r['source_id'] for r in records}:
        return None
    batch = dict(version=1, batch_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:12],
                 sources=sources, archives=records)
    validate_manifest(batch)
    save_json(spool / 'active-batch.json', batch)
    return batch


def push_batch(config, spool, batch):
    validate_manifest(batch)
    target = config['ai']
    timeout = config.get('timeout_seconds', 1800)
    sent = set()
    for item in batch['archives']:
        key = (item['source_id'], item['sha256'])
        if key in sent:
            continue
        path = spool / 'cache' / item['source_id'] / (item['sha256'] + '.tar')
        if path.stat().st_size != item['size'] or digest(path) != item['sha256']:
            raise ValueError('cached_archive_changed')
        print('Pushing batch archive %d...' % (len(sent) + 1), flush=True)
        with path.open('rb') as incoming:
            ssh_call(target, dict(operation='put', batch_id=batch['batch_id'], source_id=item['source_id'],
                                 sha256=item['sha256'], size=item['size']), timeout, incoming=incoming)
        sent.add(key)
    ssh_call(target, dict(operation='publish', batch_id=batch['batch_id'], manifest=batch), timeout)


def transfer(config, spool, log):
    failed = False
    for source in config['sources']:
        try:
            print('Collecting source %s...' % source['id'], flush=True)
            count = collect_source(source, spool, config.get('timeout_seconds', 1800))
            print('Source %s: %d completed archive(s) verified.' % (source['id'], count), flush=True)
        except Exception as error:
            failed = True
            traceback.print_exc(file=log)
            log.flush()
            print('Source %s unavailable or incomplete; retained files remain queued.' % source['id'], flush=True)
            report_failure(error, 'collect-' + source['id'])
    state_path = spool / 'delivered.json'
    delivered = set(json.loads(state_path.read_text()) if state_path.exists() else [])
    active = spool / 'active-batch.json'
    if failed and not active.exists():
        print('WAITING: a source collection failed. No new batch published; all collected files retained for retry.')
        return 1
    batch = json.loads(active.read_text()) if active.exists() else prepare_batch(config, spool, delivered)
    if batch is None:
        print('WAITING: at least one new archive from every configured source is required. No batch pushed.')
        return int(failed)
    push_batch(config, spool, batch)
    delivered.update(r['origin_id'] for r in batch['archives'])
    save_json(state_path, sorted(delivered))
    save_json(spool / 'sent' / (batch['batch_id'] + '.json'), batch)
    active.unlink()
    print('SUCCESS: complete batch pushed; %d sources, %d archive origins. Originals and local cache retained.' %
          (len(batch['sources']), len(batch['archives'])))
    return int(failed)


def ingest_batches(inbox, root, log, loader=None):
    inbox.resolve().relative_to((root / 'data/landing').resolve())
    if loader is None:
        module_path = Path(__file__).with_name('14_ingest_vendor_tar.py')
        spec = importlib.util.spec_from_file_location('vendor_loader', module_path)
        loader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loader)
    credentials = json.loads((root / 'config/clickhouse-server/credentials.json').read_text())
    db = loader.Database(credentials)
    totals = dict(batches=0, archives=0, already_loaded=0, rows=0, failures=0)
    for ready in sorted(inbox.glob('*/.ready')):
        folder = ready.parent
        if (folder / '.ingested').exists():
            continue
        try:
            if folder.is_symlink() or ready.is_symlink() or (folder / 'manifest.json').is_symlink():
                raise ValueError('batch_symlink')
            content = (folder / 'manifest.json').read_bytes()
            if hashlib.sha256(content).hexdigest() != ready.read_text().strip():
                raise ValueError('manifest_checksum_mismatch')
            batch = json.loads(content)
            validate_manifest(batch)
            if folder.name != batch['batch_id']:
                raise ValueError('batch_directory_mismatch')
            # Verify the complete batch before starting any archive ingestion.
            for item in batch['archives']:
                path = folder / item['source_id'] / (item['sha256'] + '.tar')
                if path.parent.is_symlink() or path.is_symlink() or path.stat().st_size != item['size'] or digest(path) != item['sha256']:
                    raise ValueError('batch_archive_checksum_mismatch')
            loader.initialize(db)
            db.execute('''CREATE TABLE IF NOT EXISTS ran_ai.archive_sources_v1 (
              origin_id FixedString(64),archive_sha256 FixedString(64),source_id String,
              source_host String,source_user String,source_path String,source_mtime_ns UInt64,
              size_bytes UInt64,collected_utc DateTime('UTC'),batch_id String
              ) ENGINE=MergeTree ORDER BY (archive_sha256,source_id,origin_id)''')
            db.execute('''CREATE VIEW IF NOT EXISTS ran_ai.archive_sources AS
              SELECT * FROM ran_ai.archive_sources_v1
              WHERE archive_sha256 IN (SELECT archive_sha256 FROM ran_ai.ingest_commits_v1)''')
            for number, item in enumerate(batch['archives'], 1):
                print('Ingesting batch archive %d of %d; source=%s...' % (number, len(batch['archives']), item['source_id']), flush=True)
                if not int(db.execute("SELECT count() FROM %s WHERE origin_id='%s'" % (PROVENANCE, item['origin_id']))):
                    writer = loader.Batch(db, PROVENANCE)
                    writer.add(dict(origin_id=item['origin_id'], archive_sha256=item['sha256'], source_id=item['source_id'],
                                    source_host=item['host'], source_user=item['user'], source_path=item['source_path'],
                                    source_mtime_ns=item['mtime_ns'], size_bytes=item['size'], collected_utc=item['collected_utc'],
                                    batch_id=batch['batch_id']))
                    writer.flush()
                path = folder / item['source_id'] / (item['sha256'] + '.tar')
                result = loader.ingest(path, db, lambda message: print(message, flush=True))
                totals['archives'] += 1
                totals['already_loaded'] += int(result['already_committed'])
                totals['rows'] += result['rows']
            save_json(folder / '.ingested', dict(completed_utc=datetime.now(timezone.utc).isoformat()))
            totals['batches'] += 1
        except Exception:
            traceback.print_exc(file=log)
            log.flush()
            totals['failures'] += 1
            print('Batch not completed; retry will preserve committed data and skip exact-TAR duplicates.', flush=True)
    print('Batch ingestion summary: ' + json.dumps(totals))
    return int(bool(totals['failures']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=VERSION)
    parser.add_argument('action', choices=('init-config', 'plan', 'run', 'ingest'))
    parser.add_argument('--config', type=Path, default=Path.home() / 'ran-ai-transfer/config.json')
    parser.add_argument('--spool', type=Path, default=Path.home() / 'ran-ai-transfer/spool')
    parser.add_argument('--inbox', type=Path, default=Path.home() / 'ran-ai/data/landing/server-batches')
    args = parser.parse_args()
    print('=== %s; action=%s ===' % (VERSION, args.action), flush=True)
    os.umask(0o077)
    if not sys.platform.startswith('linux'):
        parser.error('run on the intermediate Linux server or AI Linux server')
    if os.geteuid() == 0:
        parser.error('run as a normal user, without sudo')
    import fcntl
    root = Path.home() / 'ran-ai'
    if args.action == 'init-config':
        example = dict(timeout_seconds=1800,
                       ai=dict(host='AI_HOST', user='sysadmin', directory='/home/sysadmin/ran-ai/data/landing/server-batches',
                               password='REPLACE_WITH_AI_PASSWORD',
                               python='/home/sysadmin/ran-ai/venv/bin/python'),
                       sources=[dict(id='source-1', host='SOURCE_HOST', user='SOURCE_USER', password='REPLACE_WITH_SOURCE_PASSWORD', directory='/log/pm/oss/raw/nr',
                                     patterns=['*.tar'], min_age_seconds=120)])
        args.config.parent.mkdir(parents=True, exist_ok=True)
        with args.config.open('x', encoding='utf-8') as output:
            json.dump(example, output, indent=2)
        print('Created private config template: %s. Edit all placeholders and add every source.' % args.config)
        return 0
    base = root if args.action == 'ingest' else args.spool
    (base / 'run').mkdir(parents=True, exist_ok=True)
    (base / 'logs').mkdir(exist_ok=True)
    lock_path = root / 'run/vendor-ingest.lock' if args.action == 'ingest' else base / 'run/transfer.lock'
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another collection/ingestion is running; this scheduled invocation skipped.')
            return 0
        with (base / 'logs' / ('18-transfer-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-%d.log' % os.getpid())).open('x') as log:
            stage = 'read-config'
            try:
                if args.action == 'ingest':
                    csv.field_size_limit(1024 * 1024)
                    return ingest_batches(args.inbox, root, log)
                config = load_config(args.config)
                if args.action == 'plan':
                    for source in config['sources']:
                        stage = 'plan-' + source['id']
                        print('Checking source %s...' % source['id'], flush=True)
                        print('Selecting newest eligible file only; archive checksums are deferred to run.', flush=True)
                        request, _, _ = inventory_request(source, args.spool, 'plan')
                        items = json.loads(ssh_call(source, request, config.get('timeout_seconds', 1800)))
                        print('Source %s: %d newest completed archive(s) eligible; older history ignored; nothing downloaded or pushed.' % (source['id'], len(items)))
                    return 0
                return transfer(config, args.spool, log)
            except Exception as error:
                traceback.print_exc(file=log)
                report_failure(error, stage)
                print('FAILED: retained files are available for retry. Review the private log under %s.' % (base / 'logs'))
                return 1


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == '--_ssh-askpass':
        raise SystemExit(askpass(sys.argv[2] if len(sys.argv) == 3 else '', os.environ))
    raise SystemExit(main())
