#!/usr/bin/env python3
"""Read-only runtime diagnostics; run in a SECOND SSH session while ask is stalled.
Save as: /home/sysadmin/ran-ai/app/17_diagnose_ollama.py
Run from any directory, without sudo:
  "$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/17_diagnose_ollama.py"
No inference requests, restarts, signals, settings changes or database access.
"""
from collections import Counter
import json
import os
from pathlib import Path
import sys
import time
import urllib.request


def parse_stat(text):
    parts = text.rsplit(')', 1)[1].split()
    return {'state': parts[0], 'ppid': int(parts[1]), 'pgrp': int(parts[2]),
            'ticks': int(parts[11]) + int(parts[12]), 'start': parts[19],
            'major_faults': int(parts[9])}


def process_info(directory):
    result = parse_stat((directory / 'stat').read_text())
    status = {}
    for line in (directory / 'status').read_text().splitlines():
        key, _, value = line.partition(':')
        status[key] = value.strip()
    result.update(status=status, pid=int(directory.name))
    try:
        result['exe'] = (directory / 'exe').resolve(strict=True)
    except OSError:
        result['exe'] = None
    return result


def collect(server_pid, runtime):
    all_processes = {}
    for directory in Path('/proc').glob('[0-9]*'):
        try:
            if directory.stat().st_uid == os.getuid():
                item = process_info(directory)
                all_processes[item['pid']] = item
        except (OSError, ValueError, IndexError):
            continue
    selected = {server_pid}
    for _ in range(10):
        added = {pid for pid, item in all_processes.items() if item['ppid'] in selected or item['pgrp'] == server_pid}
        if added <= selected:
            break
        selected.update(added)
    for pid, item in all_processes.items():
        if item['exe'] is not None and runtime in item['exe'].parents:
            selected.add(pid)
    return {pid: all_processes[pid] for pid in selected if pid in all_processes}


def local_api(endpoint):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:11435' + endpoint, timeout=3) as response:
            return json.load(response)
    except Exception as error:
        return {'diagnostic_error': type(error).__name__}


def log_events(path):
    # Only print fixed event labels, not log bodies which can contain private paths.
    patterns = [('warming up the model', 'warm-up began'),
                ('runner started in', 'runner-ready message'),
                ('server started in', 'server-ready message'),
                ('client connection closed before', 'client disconnected during load'),
                ('timed out waiting for llama', 'runner startup wait timed out'),
                ('out of memory', 'out-of-memory message'),
                ('cannot allocate memory', 'allocation-failure message'),
                ('context canceled', 'request context canceled')]
    with path.open('rb') as source:
        source.seek(max(0, path.stat().st_size - 1024 * 1024))
        lines = source.read().decode('utf-8', errors='replace').splitlines()
    events = []
    for line in lines:
        for pattern, label in patterns:
            if pattern in line.lower():
                events.append(label)
    return events[-8:]


def main():
    if not sys.platform.startswith('linux'):
        print('Run this diagnostic on the Linux AI server.')
        return 1
    root = Path.home() / 'ran-ai'
    try:
        state = json.loads((root / 'run/ollama-managed.json').read_text())
        server_pid = int(state['pid'])
        runtime = Path(state['binary']).resolve().parent.parent
        runtime.relative_to((root / 'software').resolve())
        log = Path(state['log']).resolve()
        log.relative_to((root / 'logs').resolve())
        print('=== Ollama runtime diagnostic-v1 (read-only) ===', flush=True)
        version = local_api('/api/version')
        models = local_api('/api/ps')
        print('API version: %s; model-status API: %s; listed models: %d' %
              (version.get('version', version.get('diagnostic_error')), models.get('diagnostic_error', 'responding'), len(models.get('models', []))))
        print('Manager threads: %s; saved CPU count: %d' % (state['threads'], len(state['cpus'])))
        memory = {}
        for line in Path('/proc/meminfo').read_text().splitlines():
            key, _, value = line.partition(':')
            if key in ('MemAvailable', 'SwapTotal', 'SwapFree'):
                memory[key] = int(value.split()[0]) / 1024 ** 2
        print('Host GiB: ' + ', '.join('%s=%.2f' % pair for pair in memory.items()))
        first = collect(server_pid, runtime)
        began = time.monotonic()
        time.sleep(3)
        second = collect(server_pid, runtime)
        elapsed = time.monotonic() - began
        hz = os.sysconf('SC_CLK_TCK')
        print('Runtime processes: %d; CPU sample=%.1fs; 100%% CPU means one logical CPU.' % (len(second), elapsed))
        for pid, item in sorted(second.items())[:5]:
            previous = first.get(pid)
            cpu = 'new process'
            faults = '?'
            if previous and previous['start'] == item['start']:
                cpu = '%.1f%%' % ((item['ticks'] - previous['ticks']) / hz / elapsed * 100)
                faults = str(item['major_faults'] - previous['major_faults'])
            status = item['status']
            role = 'manager-server' if pid == server_pid else 'runtime-worker'
            print('%s PID=%d state=%s CPU=%s threads=%s RSS=%s swap=%s major_faults_delta=%s' %
                  (role, pid, item['state'], cpu, status.get('Threads', '?'), status.get('VmRSS', '?'), status.get('VmSwap', '?'), faults))
            print('  allowed CPUs: %s; virtual size: %s' % (status.get('Cpus_allowed_list', '?'), status.get('VmSize', '?')))
            task_states = Counter()
            for task in (Path('/proc') / str(pid) / 'task').glob('[0-9]*'):
                try:
                    task_states[parse_stat((task / 'stat').read_text())['state']] += 1
                except (OSError, ValueError, IndexError):
                    pass
            print('  task states: ' + ', '.join('%s=%d' % pair for pair in sorted(task_states.items())))
        if len(second) > 5:
            print('Additional runtime processes omitted: %d' % (len(second) - 5))
        events = log_events(log)
        print('Recent load-event sequence: ' + (' -> '.join(events) if events else 'no matching event markers'))
        print('No processes signalled or settings changed. Share this diagnostic summary.')
        return 0
    except (OSError, ValueError, KeyError, IndexError) as error:
        print('Diagnostic unavailable (%s). Confirm the managed Ollama state/log files exist.' % type(error).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
