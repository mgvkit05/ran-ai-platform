#!/usr/bin/env python3
"""Project-local ClickHouse: start, status, test, stop. No sudo or downloads.
Run: "$HOME/ran-ai/venv/bin/python" 10_manage_clickhouse.py start
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from xml.sax.saxutils import escape

PORT = 18123
SERVER_MEMORY = 64 * 1024 ** 3
QUERY_MEMORY = 8 * 1024 ** 3


def identity(pid):
    try:
        fields = (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
        return None if fields[0] == 'Z' else fields[19]
    except (OSError, IndexError):
        return None


def owned(state):
    try:
        return (state['pid'] > 1 and state['start_ticks'] is not None
                and identity(state['pid']) == state['start_ticks']
                and os.getpgid(state['pid']) == state['pid']
                and (Path('/proc') / str(state['pid']) / 'exe').resolve() == Path(state['binary']).resolve())
    except (KeyError, OSError, TypeError, ValueError):
        return False


def read_state(path):
    return json.loads(path.read_text()) if path.exists() else None


def settings(root, password, default_password):
    """Build a self-contained configuration; no system config or default ports."""
    data = root / 'database' / 'clickhouse'
    def path(value):
        return escape(str(value) + '/')
    users = '''<clickhouse><profiles><ran_ai>
      <max_threads>8</max_threads><max_memory_usage>%d</max_memory_usage>
      <max_execution_time>60</max_execution_time><log_queries>0</log_queries>
      </ran_ai><default><max_threads>8</max_threads><max_memory_usage>%d</max_memory_usage></default>
      </profiles><users>
      <ran_ai><password_sha256_hex>%s</password_sha256_hex><networks><ip>127.0.0.1</ip></networks>
      <profile>ran_ai</profile><quota>default</quota></ran_ai>
      <default><password_sha256_hex>%s</password_sha256_hex><networks><ip>127.0.0.1</ip></networks>
      <profile>default</profile><quota>default</quota></default>
      </users><quotas><default/></quotas></clickhouse>''' % (
          QUERY_MEMORY, QUERY_MEMORY, hashlib.sha256(password.encode()).hexdigest(),
          hashlib.sha256(default_password.encode()).hexdigest())
    server = '''<clickhouse>
      <logger><level>information</level><console>true</console></logger>
      <listen_host>127.0.0.1</listen_host><http_port>%d</http_port>
      <path>%s</path><tmp_path>%s</tmp_path><user_files_path>%s</user_files_path>
      <format_schema_path>%s</format_schema_path><access_control_path>%s</access_control_path>
      <users_config>%s</users_config><default_profile>ran_ai</default_profile>
      <max_server_memory_usage>%d</max_server_memory_usage>
      <mark_cache_size>268435456</mark_cache_size><uncompressed_cache_size>67108864</uncompressed_cache_size>
      <background_pool_size>16</background_pool_size><background_schedule_pool_size>16</background_schedule_pool_size>
      <background_buffer_flush_schedule_pool_size>4</background_buffer_flush_schedule_pool_size>
      <background_message_broker_schedule_pool_size>4</background_message_broker_schedule_pool_size>
      <background_distributed_schedule_pool_size>4</background_distributed_schedule_pool_size>
      <max_thread_pool_size>1024</max_thread_pool_size>
      <max_concurrent_queries>4</max_concurrent_queries>
      <timezone>UTC</timezone><mlock_executable>false</mlock_executable>
    </clickhouse>''' % (PORT, path(data), path(data / 'tmp'), path(data / 'user_files'),
                       path(data / 'format_schemas'), path(data / 'access'),
                       escape(str(root / 'config' / 'clickhouse-server' / 'users.xml')), SERVER_MEMORY)
    return server, users


def configure(root):
    directory = root / 'config' / 'clickhouse-server'
    directory.mkdir(exist_ok=True)
    credentials_path = directory / 'credentials.json'
    server_path = directory / 'config.xml'
    users_path = directory / 'users.xml'
    present = [path.exists() for path in (credentials_path, server_path, users_path)]
    if all(present):
        return server_path, json.loads(credentials_path.read_text())
    if any(present):
        raise ValueError('partial ClickHouse configuration exists; share the error before replacing any files')
    credentials = {'user': 'ran_ai', 'password': secrets.token_urlsafe(32)}
    server, users = settings(root, credentials['password'], secrets.token_urlsafe(32))
    for target, text in ((credentials_path, json.dumps(credentials)), (server_path, server), (users_path, users)):
        with target.open('x', encoding='utf-8') as output:
            output.write(text)
        target.chmod(0o600)
    data = root / 'database' / 'clickhouse'
    for name in ('tmp', 'user_files', 'format_schemas', 'access'):
        (data / name).mkdir(parents=True, exist_ok=True)
    return server_path, credentials


def query(credentials, sql, timeout=70):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request('http://127.0.0.1:%d/' % PORT, data=sql.encode('utf-8'),
                                     headers={'X-ClickHouse-User': credentials['user'],
                                              'X-ClickHouse-Key': credentials['password']})
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.read().decode('utf-8').strip()
    except urllib.error.HTTPError as error:
        detail = error.read(8192).decode('utf-8', errors='replace')
        raise RuntimeError('ClickHouse HTTP %d: %s' % (error.code, detail)) from None


def smoke_test(credentials, execute=query):
    table = 'ran_ai.setup_test_' + uuid.uuid4().hex
    execute(credentials, 'CREATE DATABASE IF NOT EXISTS ran_ai')
    created = False
    try:
        execute(credentials, 'CREATE TABLE %s (cell_id String, successes UInt64, attempts UInt64) ENGINE=MergeTree ORDER BY cell_id' % table)
        created = True
        execute(credentials, "INSERT INTO %s VALUES ('synthetic_A',98,100),('synthetic_B',95,100)" % table)
        answer = execute(credentials, 'SELECT count() AS rows, sum(successes) AS successes, sum(attempts) AS attempts, round(100.0*sum(successes)/sum(attempts),2) AS success_rate FROM %s FORMAT JSON' % table)
        result = json.loads(answer)['data'][0]
        if not (int(result['rows']) == 2 and int(result['successes']) == 193
                and int(result['attempts']) == 200 and float(result['success_rate']) == 96.5):
            raise RuntimeError('synthetic query result differs from expected values')
        return result
    finally:
        if created:
            execute(credentials, 'DROP TABLE IF EXISTS ' + table)


def terminate(state, child=None):
    if not owned(state):
        raise RuntimeError('process identity does not match; no process signalled')
    pid = state['pid']
    os.killpg(pid, signal.SIGTERM)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if child is not None:
            child.poll()  # Reap our own child so a zombie cannot keep its group visible.
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    # Database shutdown must not be force-killed automatically.
    raise RuntimeError('graceful shutdown still in progress; inspect status/logs before retrying')


def start(root, state_path, config, credentials):
    existing = read_state(state_path)
    if existing and owned(existing):
        print('Managed ClickHouse is already running. Use status or test.')
        return
    installation = json.loads((root / 'config' / 'clickhouse-offline.json').read_text())
    binary = Path(installation['binary']).resolve()
    binary.relative_to((root / 'software').resolve())
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', PORT))
        except OSError:
            raise RuntimeError('local port 18123 is occupied; no existing service modified') from None
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    log_path = root / 'logs' / ('10-clickhouse-%s-%d.log' % (stamp, os.getpid()))
    def child_setup():
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    environment = dict(os.environ, TMPDIR=str(root / 'tmp'), CLICKHOUSE_WATCHDOG_ENABLE='0')
    with log_path.open('x') as log:
        process = subprocess.Popen([str(binary), 'server', '--config-file', str(config)],
                                   cwd=str(root / 'database' / 'clickhouse'), env=environment,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True, preexec_fn=child_setup, close_fds=True)
    state = {'pid': process.pid, 'start_ticks': identity(process.pid), 'binary': str(binary), 'log': str(log_path)}
    partial = state_path.with_suffix('.tmp')
    partial.write_text(json.dumps(state, indent=2))
    partial.replace(state_path)
    try:
        deadline = time.monotonic() + 90
        while True:
            if process.poll() is not None:
                raise RuntimeError('ClickHouse exited. Server log: ' + str(log_path))
            try:
                version = query(credentials, 'SELECT version()', timeout=2)
                if owned(state):
                    break
            except (urllib.error.URLError, OSError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError('startup timed out. Server log: ' + str(log_path))
            time.sleep(0.5)
        print('ClickHouse %s running at http://127.0.0.1:%d' % (version, PORT), flush=True)
        result = smoke_test(credentials)
        print('Synthetic test: 2 rows; 193 successes / 200 attempts = %.2f%%' % float(result['success_rate']))
        print('SUCCESS: database insert/query verified; synthetic table removed.')
        print('ClickHouse remains running. Ollama was not modified.')
        print('Server log: ' + str(log_path))
    except BaseException:
        if owned(state):
            try:
                terminate(state, process)
            except RuntimeError:
                print('Startup failed; graceful shutdown is pending. State retained; inspect logs.', file=sys.stderr)
                raise
        if process.poll() is not None or not owned(state):
            state_path.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'status', 'test', 'stop'))
    args = parser.parse_args()
    if not sys.platform.startswith('linux'):
        parser.error('run on the Linux AI server')
    os.umask(0o077)
    root = Path.home() / 'ran-ai'
    try:
        if os.geteuid() == 0:
            raise ValueError('run without sudo')
        if not (root / 'config' / 'clickhouse-offline.json').is_file():
            raise ValueError('complete the offline ClickHouse installation first')
        root.resolve().relative_to(Path('/home'))
        run_dir = root / 'run'
        run_dir.mkdir(exist_ok=True)
        state_path = run_dir / 'clickhouse-managed.json'
        import fcntl
        with (run_dir / 'clickhouse-manager.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.action == 'start':
                config, credentials = configure(root)
                start(root, state_path, config, credentials)
                return 0
            state = read_state(state_path)
            if not state or not owned(state):
                print('Managed ClickHouse is stopped (or state is stale).')
                return 0 if args.action == 'stop' else 1
            if args.action == 'stop':
                terminate(state)
                state_path.unlink(missing_ok=True)
                print('Managed ClickHouse stopped gracefully. Data retained.')
                return 0
            credentials = json.loads((root / 'config/clickhouse-server/credentials.json').read_text())
            if args.action == 'test':
                smoke_test(credentials)
                print('SUCCESS: insert/query verified; synthetic table removed.')
            else:
                print('RUNNING: ClickHouse ' + query(credentials, 'SELECT version()'))
                print('HTTP: http://127.0.0.1:18123; query threads: 8; tracked server memory cap: 64 GiB.')
                print('Log: ' + state['log'])
    except (Exception, KeyboardInterrupt) as error:
        print('FAILED: %s: %s' % (type(error).__name__, error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
