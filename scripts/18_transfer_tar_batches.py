#!/usr/bin/env python3
"""Collect on an intermediate Linux server; push complete batches to the AI server.

Intermediate server (Python 3.6+ and OpenSSH client):
  python3 "$HOME/ran-ai-transfer/18_transfer_tar_batches.py" init-config
  python3 "$HOME/ran-ai-transfer/18_transfer_tar_batches.py" plan
  python3 "$HOME/ran-ai-transfer/18_transfer_tar_batches.py" run
  python3 "$HOME/ran-ai-transfer/18_transfer_tar_batches.py" install-automation
  python3 "$HOME/ran-ai-transfer/18_transfer_tar_batches.py" automation-status
AI server (keep 14_ingest_vendor_tar.py beside this script):
  "$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/18_transfer_tar_batches.py" ingest

Watch collection/push on the intermediate server:
  tail -F "$HOME/ran-ai-transfer/spool/logs/scheduled-collector.log"
Watch confirmed intermediate cache deletions:
  tail -F "$HOME/ran-ai-transfer/spool/logs/deletions.jsonl"
Watch ClickHouse ingestion on the AI server:
  tail -F "$HOME/ran-ai/logs/scheduled-ingest.log"
Watch AI archive deletion after committed ingestion:
  tail -F "$HOME/ran-ai/logs/ingest-deletions.jsonl"
Recent downloaded TARs and successful push manifests (intermediate server):
  ls -lt "$HOME"/ran-ai-transfer/spool/cache/*/*.tar | head
  ls -lt "$HOME"/ran-ai-transfer/spool/sent/*.json | head
Logs/cache/manifests appear after their first scheduled activity. Ctrl+C stops
tail only; scheduled collection and ingestion continue. For --spool overrides,
use that spool directory instead of the default intermediate paths above.

Arguments: action {init-config,plan,run,ingest,install-automation,automation-status,
install-schedule,schedule-status,remove-schedule}; --config PATH (default
$HOME/ran-ai-transfer/config.json); --spool PATH (default $HOME/ran-ai-transfer/spool);
--inbox PATH (AI ingest only, default $HOME/ran-ai/data/landing/server-batches).
--role {collector,ingest} is required for the three local schedule commands.
install-automation installs a 15-minute collector on this server and a 1-minute
ingester on the AI server over configured SSH. Update this script on BOTH first.
Run as a normal user without sudo; existing crond must be active on both hosts.
Each endpoint accepts user/password or an optional identity_file. Passwords stay
in the private local config (chmod 600), never in manifests or command arguments.
Preverified known_hosts are required for both authentication methods.
Original source archives are never deleted. No model or new Python package needed.
Available producers are pushed without waiting for empty/unavailable producers.
Intermediate cached TARs are deleted after verified AI delivery, independent of
ClickHouse ingestion. Deletions are audited in spool/logs/deletions.jsonl.
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
VERSION = 'tar-transfer-v8'


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
    elif 'cron_daemon_not_active' in detail:
        code, hint = 'CRON_SERVICE', 'The administrator must enable/start crond on this server before scheduling.'
    elif 'remote_schedule_failed' in detail:
        code, hint = 'REMOTE_SCHEDULE', 'Update script 18 on the AI server; verify crond and the existing loader there. See the private log.'
    elif isinstance(error, RuntimeError) and re.fullmatch('[a-z_]+', str(error)):
        code, hint = 'SCHEDULE_OR_RUNTIME', str(error)
    print('Diagnostic: %s; stage=%s; code=%s; error=%s' % (VERSION, stage, code, type(error).__name__), flush=True)
    print(hint, flush=True)
    for frame in traceback.extract_tb(error.__traceback__)[-4:]:
        print('  at %s:%d in %s' % (Path(frame.filename).name, frame.lineno, frame.name), flush=True)

# Sent as fixed Python code over SSH. Requests are JSON on stdin, never shell code.
REMOTE = r'''
import fnmatch,hashlib,json,os,pathlib,re,stat,subprocess,sys,time
os.umask(0o077)
q=json.loads(sys.stdin.buffer.readline())
def checksum(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(4194304),b''): h.update(b)
    return h.hexdigest()
def sync_file(p):
    with p.open('r+b') as f: os.fsync(f.fileno())
def sync_directory(p):
    if os.name=='posix':
        fd=os.open(str(p),os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
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
elif mode=='schedule':
    if q['action'] not in ('install-schedule','schedule-status'): raise ValueError('invalid_schedule_action')
    result=subprocess.run([sys.executable,q['script'],q['action'],'--role','ingest','--inbox',q['directory']],
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
    print(json.dumps(dict(returncode=result.returncode,output=result.stdout,error=result.stderr)))
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
        if (batch/'.ingested').is_file() and not (batch/'.ingested').is_symlink():
            temporary.unlink()  # Identical delivery retry after ingestion/cleanup.
        elif final.exists() and checksum(final)==digest:
            temporary.unlink()
        elif (batch/'.ready').exists():
            raise ValueError('published_archive_changed')
        else:
            temporary.replace(final)
        sync_directory(folder);sync_directory(batch);sync_directory(root)
    else:
        manifest=q['manifest']
        if manifest['batch_id']!=q['batch_id']: raise ValueError('batch_mismatch')
        content=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
        manifest_path=batch/'manifest.json';ready=batch/'.ready';ingested=batch/'.ingested'
        if any(p.is_symlink() for p in (manifest_path,ready,ingested)): raise ValueError('manifest_symlink')
        if ingested.is_file() and ready.is_file() and manifest_path.is_file():
            if manifest_path.read_bytes()!=content or ready.read_text().strip()!=hashlib.sha256(content).hexdigest():
                raise ValueError('published_batch_changed')
            print('OK');sys.exit(0)  # Prior verified delivery was already ingested.
        expected=set(manifest['sources']);received=set()
        if not expected or not manifest['archives']: raise ValueError('empty_batch')
        for item in manifest['archives']:
            safe_id(item['source_id']);received.add(item['source_id'])
            if not re.fullmatch(r'[a-f0-9]{64}',item['sha256']): raise ValueError('invalid_hash')
            p=batch/item['source_id']/(item['sha256']+'.tar')
            if p.is_symlink() or p.parent.is_symlink(): raise ValueError('archive_symlink')
            if p.stat().st_size!=item['size'] or checksum(p)!=item['sha256']: raise ValueError('incomplete_batch')
            sync_file(p)
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
        sync_file(manifest_path);sync_file(ready);sync_directory(batch);sync_directory(root)
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
                  'fetch': 'archive download', 'put': 'archive upload', 'publish': 'batch verification/publication',
                  'schedule': 'AI scheduler setup/status'}
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
    state_path = spool / 'delivered.json'
    delivered = set(json.loads(state_path.read_text()) if state_path.exists() else [])
    collected = 0
    for item in items:
        validate_file(item)
        identity = dict(source_id=source['id'], host=source['host'], user=source['user'],
                        source_path=source['directory'].rstrip('/') + '/' + item['name'], sha256=item['sha256'])
        origin_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        if origin_id in delivered:
            save_json(cursor_path, dict(scope_hash=scope_hash, mtime_ns=item['mtime_ns'], name=item['name']))
            continue
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
        record_path = spool / 'records' / (origin_id + '.json')
        if not record_path.exists():
            record = dict(identity, origin_id=origin_id, size=item['size'], mtime_ns=item['mtime_ns'],
                          collected_utc=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
            save_json(record_path, record)
        # Advance only after both archive and origin record are durably retained.
        save_json(cursor_path, dict(scope_hash=scope_hash, mtime_ns=item['mtime_ns'], name=item['name']))
        collected += 1
    return collected


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
    if not records:
        return None
    available = {r['source_id'] for r in records}
    sources = [source for source in sources if source in available]
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
    confirmation = ssh_call(target, dict(operation='publish', batch_id=batch['batch_id'], manifest=batch), timeout)
    if confirmation.strip() != b'OK':
        raise RuntimeError('AI_delivery_confirmation_not_received')


def deletion_event(spool, event, audit_name='deletions.jsonl'):
    directory = spool / 'logs'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / audit_name
    if path.is_symlink():
        raise ValueError('deletion_audit_symlink')
    with path.open('a', encoding='utf-8') as output:
        path.chmod(0o600)
        output.write(json.dumps(dict(event, utc=datetime.now(timezone.utc).isoformat()), ensure_ascii=True) + '\n')
        output.flush()
        os.fsync(output.fileno())


def cleanup_delivered(spool, delivered, log):
    """Delete only registered cache TARs whose every origin has verified delivery."""
    pending = set()
    active_path = spool / 'active-batch.json'
    if active_path.exists():
        active = json.loads(active_path.read_text())
        validate_manifest(active)
        pending.update((r['source_id'], r['sha256']) for r in active['archives'])
    for record_path in (spool / 'records').glob('*.json'):
        record = json.loads(record_path.read_text())
        if record['origin_id'] not in delivered:
            pending.add((record['source_id'], record['sha256']))
    failed = False
    for manifest_path in sorted((spool / 'sent').glob('*.json')):
        batch = json.loads(manifest_path.read_text())
        validate_manifest(batch)
        marker = spool / 'cleaned' / (batch['batch_id'] + '.json')
        if marker.exists():
            continue
        if not all(item['origin_id'] in delivered for item in batch['archives']):
            continue
        complete = True
        seen = set()
        for item in batch['archives']:
            key = (item['source_id'], item['sha256'])
            if key in seen:
                continue
            seen.add(key)
            if key in pending:
                complete = False
                continue
            cache = spool / 'cache'
            path = cache / item['source_id'] / (item['sha256'] + '.tar')
            event = dict(batch_id=batch['batch_id'], source_id=item['source_id'], sha256=item['sha256'],
                         cache_file=str(path.absolute()), original_file=item['source_path'], bytes=item['size'],
                         reason='verified_AI_delivery')
            try:
                # Exact, nonrecursive deletion restricted to this registered cache path.
                if cache.is_symlink() or path.parent.is_symlink() or path.is_symlink():
                    raise ValueError('cache_cleanup_symlink')
                path.resolve().relative_to(cache.resolve())
                if not path.exists():
                    deletion_event(spool, dict(event, result='already_absent'))
                    continue
                if not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_size != item['size'] or digest(path) != item['sha256']:
                    raise ValueError('cache_cleanup_content_changed')
                deletion_event(spool, dict(event, result='delete_intent'))
                path.unlink()
                deletion_event(spool, dict(event, result='deleted'))
                print('DELETED intermediate TAR: source=%s; file=%s; bytes=%d; verified delivery complete.' %
                      (item['source_id'], path.name, item['size']), flush=True)
            except Exception:
                complete = False
                failed = True
                traceback.print_exc(file=log)
                log.flush()
                deletion_event(spool, dict(event, result='delete_failed'))
                print('Cleanup pending for source %s; transfer is confirmed and deletion will retry.' % item['source_id'], flush=True)
        if complete:
            save_json(marker, dict(batch_id=batch['batch_id'], completed_utc=datetime.now(timezone.utc).isoformat()))
    return failed


def transfer(config, spool, log):
    state_path = spool / 'delivered.json'
    delivered = set(json.loads(state_path.read_text()) if state_path.exists() else [])
    active = spool / 'active-batch.json'
    if active.exists():
        previous = json.loads(active.read_text())
        validate_manifest(previous)
        sent = spool / 'sent' / (previous['batch_id'] + '.json')
        if (all(item['origin_id'] in delivered for item in previous['archives']) and sent.exists()
                and json.loads(sent.read_text()) == previous):
            active.unlink()  # Recover a crash after delivery state was saved.
    failed = cleanup_delivered(spool, delivered, log)
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
    batch = json.loads(active.read_text()) if active.exists() else prepare_batch(config, spool, delivered)
    if batch is None:
        print('WAITING: no new available archives to push. Other producers do not block delivery.')
        return int(failed)
    push_batch(config, spool, batch)
    delivered.update(r['origin_id'] for r in batch['archives'])
    save_json(spool / 'sent' / (batch['batch_id'] + '.json'), batch)
    save_json(state_path, sorted(delivered))
    active.unlink()
    print('SUCCESS: complete batch pushed; %d sources, %d archive origins. AI copies verified; intermediate cleanup follows.' %
          (len(batch['sources']), len(batch['archives'])))
    failed = cleanup_delivered(spool, delivered, log) or failed
    return int(failed)


def verify_batch_commits(batch, db):
    for archive_hash in sorted({item['sha256'] for item in batch['archives']}):
        if not int(db.execute("SELECT count() FROM ran_ai.ingest_commits_v1 WHERE archive_sha256='%s'" % archive_hash)):
            raise ValueError('archive_not_committed_cleanup_refused')


def cleanup_ingested_batch(folder, batch, inbox, root, db, log):
    validate_manifest(batch)
    folder.resolve().relative_to(inbox.resolve())
    if folder.is_symlink() or not (folder / '.ingested').is_file() or (folder / '.ingested').is_symlink():
        raise ValueError('ingested_marker_required_for_cleanup')
    verify_batch_commits(batch, db)  # AI-side only; collector never queries ClickHouse.
    deleted, failures = 0, 0
    seen = set()
    for item in batch['archives']:
        key = (item['source_id'], item['sha256'])
        if key in seen:
            continue
        seen.add(key)
        path = folder / item['source_id'] / (item['sha256'] + '.tar')
        event = dict(batch_id=batch['batch_id'], source_id=item['source_id'], sha256=item['sha256'],
                     archive_file=str(path.absolute()), original_file=item['source_path'], bytes=item['size'],
                     reason='clickhouse_commit_verified')
        try:
            if path.parent.is_symlink() or path.is_symlink():
                raise ValueError('AI_archive_cleanup_symlink')
            path.resolve().relative_to(folder.resolve())
            if not path.exists():
                deletion_event(root, dict(event, result='already_absent'), 'ingest-deletions.jsonl')
                continue
            if not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_size != item['size'] or digest(path) != item['sha256']:
                raise ValueError('AI_archive_cleanup_content_changed')
            deletion_event(root, dict(event, result='delete_intent'), 'ingest-deletions.jsonl')
            path.unlink()
            deletion_event(root, dict(event, result='deleted'), 'ingest-deletions.jsonl')
            deleted += 1
            print('DELETED AI TAR after ClickHouse commit: source=%s; file=%s; bytes=%d' %
                  (item['source_id'], path.name, item['size']), flush=True)
        except Exception:
            failures += 1
            traceback.print_exc(file=log)
            log.flush()
            deletion_event(root, dict(event, result='delete_failed'), 'ingest-deletions.jsonl')
            print('AI archive cleanup will retry for source %s; committed data is unchanged.' % item['source_id'], flush=True)
    if not failures:
        save_json(folder / '.cleaned', dict(completed_utc=datetime.now(timezone.utc).isoformat(),
                                           manifest_sha256=digest(folder / 'manifest.json')))
    return deleted, failures


def ingest_batches(inbox, root, log, loader=None):
    inbox.resolve().relative_to((root / 'data/landing').resolve())
    if loader is None:
        module_path = Path(__file__).with_name('14_ingest_vendor_tar.py')
        spec = importlib.util.spec_from_file_location('vendor_loader', module_path)
        loader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loader)
    credentials = json.loads((root / 'config/clickhouse-server/credentials.json').read_text())
    db = loader.Database(credentials)
    totals = dict(batches=0, archives=0, already_loaded=0, rows=0, failures=0, deleted_archives=0, cleanup_failures=0)
    for ready in sorted(inbox.glob('*/.ready')):
        folder = ready.parent
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
            if (folder / '.cleaned').is_symlink() or (folder / '.ingested').is_symlink():
                raise ValueError('batch_marker_symlink')
            if (folder / '.cleaned').exists():
                cleaned = json.loads((folder / '.cleaned').read_text())
                if cleaned['manifest_sha256'] != hashlib.sha256(content).hexdigest():
                    raise ValueError('cleaned_manifest_changed')
                if not (folder / '.ingested').exists():
                    verify_batch_commits(batch, db)
                    save_json(folder / '.ingested', dict(completed_utc=datetime.now(timezone.utc).isoformat(), recovered=True))
                continue
            if (folder / '.ingested').exists():
                deleted, failures = cleanup_ingested_batch(folder, batch, inbox, root, db, log)
                totals['deleted_archives'] += deleted
                totals['cleanup_failures'] += failures
                totals['failures'] += int(bool(failures))
                continue
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
            deleted, failures = cleanup_ingested_batch(folder, batch, inbox, root, db, log)
            totals['deleted_archives'] += deleted
            totals['cleanup_failures'] += failures
            totals['failures'] += int(bool(failures))
        except Exception:
            traceback.print_exc(file=log)
            log.flush()
            totals['failures'] += 1
            print('Batch not completed; retry will preserve committed data and skip exact-TAR duplicates.', flush=True)
    print('Batch ingestion summary: ' + json.dumps(totals))
    return int(bool(totals['failures']))


def cron_command(arguments, text=None):
    environment = dict(os.environ, LC_ALL='C')
    return subprocess.run(arguments, input=text, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, env=environment)


def read_crontab():
    result = cron_command(['crontab', '-l'])
    if result.returncode == 0:
        return result.stdout
    if result.returncode == 1 and 'no crontab for' in result.stderr.lower():
        return ''
    raise RuntimeError('cannot_read_user_crontab')


def cron_active():
    if not shutil.which('crontab'):
        raise RuntimeError('crontab_client_not_installed')
    result = cron_command(['systemctl', 'is-active', 'crond'])
    return result.returncode == 0 and result.stdout.strip() == 'active'


def replace_schedule(existing, role, entry, script):
    begin, end = '# BEGIN RAN-AI-TAR ' + role, '# END RAN-AI-TAR ' + role
    lines = []
    inside = False
    for line in existing.splitlines():
        if line == begin:
            if inside:
                raise ValueError('malformed_managed_cron_block')
            inside = True
            continue
        if line == end:
            if not inside:
                raise ValueError('malformed_managed_cron_block')
            inside = False
            continue
        if inside:
            continue
        # Replace earlier hand-written entries for this exact script/role too.
        fields = line.split(None, 5)
        if len(fields) == 6 and not line.lstrip().startswith('#'):
            try:
                tokens = [os.path.expandvars(token) for token in shlex.split(fields[5])]
            except ValueError:
                tokens = []
            action = 'run' if role == 'collector' else 'ingest'
            if str(script) in tokens and action in tokens:
                continue
        lines.append(line)
    if inside:
        raise ValueError('malformed_managed_cron_block')
    if entry is not None:
        lines += [begin, entry, end]
    return '\n'.join(lines) + ('\n' if lines else '')


def manage_schedule(args, root):
    if args.role not in ('collector', 'ingest'):
        raise ValueError('schedule_commands_require_role')
    role = args.role
    script = Path(__file__).resolve()
    active = cron_active()
    if args.action == 'install-schedule' and not active:
        raise RuntimeError('cron_daemon_not_active')
    before = read_crontab()
    if args.action == 'schedule-status':
        marker = '# BEGIN RAN-AI-TAR ' + role
        installed = marker in before.splitlines()
        print('Schedule: role=%s; installed=%s; crond=%s; interval=%s' %
              (role, installed, 'active' if active else 'inactive', '15 minutes' if role == 'collector' else '1 minute'))
        return 0 if installed and active else 1
    base = args.spool if role == 'collector' else root
    (base / 'run').mkdir(parents=True, exist_ok=True)
    (base / 'logs').mkdir(exist_ok=True)
    entry = None
    if args.action == 'install-schedule':
        if role == 'collector':
            load_config(args.config)
            command = [sys.executable, '-u', str(script), 'run', '--config', str(args.config.resolve()), '--spool', str(args.spool.resolve())]
        else:
            if not script.with_name('14_ingest_vendor_tar.py').is_file():
                raise ValueError('install_loader_14_beside_script_18')
            if not (root / 'config/clickhouse-server/credentials.json').is_file():
                raise ValueError('complete_clickhouse_setup_before_scheduling')
            args.inbox.resolve().relative_to((root / 'data/landing').resolve())
            args.inbox.mkdir(parents=True, exist_ok=True)
            command = [sys.executable, '-u', str(script), 'ingest', '--inbox', str(args.inbox.resolve())]
        launcher = base / 'run' / ('scheduled-' + role + '.sh')
        output = base / 'logs' / ('scheduled-' + role + '.log')
        if launcher.is_symlink() or output.is_symlink():
            raise ValueError('schedule_path_symlink')
        content = '#!/bin/sh\numask 077\nPATH=/usr/local/bin:/usr/bin:/bin\nexport PATH\nexec ' + ' '.join(shlex.quote(x) for x in command)
        content += ' >> ' + shlex.quote(str(output.resolve())) + ' 2>&1\n'
        temporary = launcher.with_suffix('.tmp')
        if temporary.is_symlink():
            raise ValueError('schedule_path_symlink')
        temporary.write_text(content, encoding='utf-8')
        temporary.chmod(0o700)
        temporary.replace(launcher)
        interval = '*/15 * * * *' if role == 'collector' else '* * * * *'
        quoted = shlex.quote(str(launcher.resolve()))
        if '\n' in quoted or '\r' in quoted:
            raise ValueError('schedule_path_contains_newline')
        entry = interval + ' /bin/sh ' + quoted.replace('%', '\\%')
    after = replace_schedule(before, role, entry, script)
    if after != before:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-%d' % os.getpid()
        (base / 'run' / ('crontab-before-' + role + '-' + stamp + '.txt')).write_text(before, encoding='utf-8')
        if read_crontab() != before:
            raise RuntimeError('crontab_changed_during_setup_retry')
        if cron_command(['crontab', '-'], after).returncode:
            raise RuntimeError('cannot_install_user_crontab')
        if read_crontab() != after:
            raise RuntimeError('crontab_verification_failed_review_backup')
    print('SUCCESS: %s schedule %s; unrelated cron jobs preserved.' %
          (role, 'installed' if entry is not None else 'removed'))
    return 0


def automation(args, root):
    config = load_config(args.config)
    if args.action == 'install-automation':
        if not cron_active():
            raise RuntimeError('cron_daemon_not_active')
        read_crontab()  # Fail before remote changes if this account cannot manage cron.
        for source in config['sources']:
            print('Preflight: checking source %s...' % source['id'], flush=True)
            try:
                request, _, _ = inventory_request(source, args.spool, 'plan')
                json.loads(ssh_call(source, request, config.get('timeout_seconds', 1800)))
            except Exception as error:
                report_failure(error, 'preflight-' + source['id'])
                print('Source %s unavailable now; automation will retry it without blocking available producers.' % source['id'], flush=True)
    target = config['ai']
    default_script = str(Path(target['directory']).parent.parent.parent / 'app/18_transfer_tar_batches.py')
    request = dict(operation='schedule', script=target.get('ingest_script', default_script),
                   action='install-schedule' if args.action == 'install-automation' else 'schedule-status')
    print('Configuring AI ingestion...' if args.action == 'install-automation' else 'Checking AI ingestion...', flush=True)
    remote = json.loads(ssh_call(target, request, config.get('timeout_seconds', 1800)))
    print(remote['output'], end='', flush=True)
    if remote['returncode'] and args.action == 'install-automation':
        raise RuntimeError('remote_schedule_failed: ' + remote.get('error', ''))
    local = argparse.Namespace(**vars(args))
    local.role = 'collector'
    local.action = 'install-schedule' if args.action == 'install-automation' else 'schedule-status'
    result = manage_schedule(local, root)
    if result == 0 and remote['returncode'] == 0:
        print('Automation ready: collect/push every 15 minutes; AI checks complete batches every minute.')
    return int(bool(result or remote['returncode']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=VERSION)
    parser.add_argument('action', choices=('init-config', 'plan', 'run', 'ingest', 'install-automation', 'automation-status',
                                         'install-schedule', 'schedule-status', 'remove-schedule'))
    parser.add_argument('--role', choices=('collector', 'ingest'))
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
    if args.action in ('install-schedule', 'schedule-status', 'remove-schedule', 'install-automation', 'automation-status'):
        try:
            if args.action in ('install-automation', 'automation-status'):
                return automation(args, root)
            return manage_schedule(args, root)
        except Exception as error:
            report_failure(error, args.action)
            print('Schedule setup/status failed; check the diagnostic above. No source data deleted.')
            return 1
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
