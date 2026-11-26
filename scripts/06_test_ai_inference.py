#!/usr/bin/env python3
"""One offline CPU inference test on the dedicated AI server; stops its server afterward.
Run: "$HOME/ran-ai/venv/bin/python" 06_test_ai_inference.py
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


def physical_threads(cpus, topology=Path('/sys/devices/system/cpu')):
    cores = set()
    for cpu in cpus:
        location = topology / ('cpu%d' % cpu) / 'topology'
        try:
            cores.add(((location / 'physical_package_id').read_text().strip(),
                       (location / 'core_id').read_text().strip()))
        except OSError:
            return len(cpus)
    return len(cores)


def request(opener, endpoint, payload=None, timeout=10):
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    query = urllib.request.Request('http://127.0.0.1:11435' + endpoint, data=data,
                                   headers={'Content-Type': 'application/json'})
    try:
        with opener.open(query, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read(4096).decode('utf-8', errors='replace')
        raise RuntimeError('Ollama HTTP %d: %s' % (error.code, detail)) from None


def payload(threads):
    return {'model': 'gpt-oss:20b', 'prompt': 'Reply with exactly this sentence: AI server is ready.',
            'stream': False, 'think': 'low', 'keep_alive': 0,
            'options': {'num_thread': threads, 'num_ctx': 2048, 'num_predict': 256,
                        'num_gpu': 0, 'temperature': 0}}


def stop_owned_group(process):
    # Only target the new session created by this script, never an existing service.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass
    # The leader can exit before its model runner; ensure its group is gone too.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def run(root, threads, emit, server_log):
    import resource
    if os.geteuid() == 0:
        raise ValueError('run without sudo')
    config = json.loads((root / 'config' / 'ollama-offline.json').read_text())
    binary = Path(config['binary']).resolve()
    binary.relative_to((root / 'software').resolve())
    models = Path(config['models']).resolve()
    models.relative_to((root / 'models').resolve())
    if not binary.is_file() or not (models / 'manifests/registry.ollama.ai/library/gpt-oss/20b').is_file():
        raise ValueError('installed runtime or model manifest missing')
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', 11435))
        except OSError:
            raise ValueError('port 11435 is already occupied; no existing service was modified') from None
    cpus = os.sched_getaffinity(0)
    available_cores = physical_threads(cpus)
    count = available_cores if threads is None else threads
    if not 1 <= count <= len(cpus):
        raise ValueError('thread count exceeds available logical CPUs')
    emit('Available logical CPUs: %d; available physical cores: %d; inference threads: %d' %
         (len(cpus), available_cores, count))
    emit('One CPU request; 2048-token context; at most 256 generated tokens.')
    emit('Each Ollama process has a 128 GiB virtual-address-space ceiling (not a RAM reservation).')
    environment = dict(os.environ, OLLAMA_HOST='127.0.0.1:11435', OLLAMA_MODELS=str(models),
                       OLLAMA_NO_CLOUD='1', OLLAMA_NUM_PARALLEL='1', OLLAMA_MAX_LOADED_MODELS='1',
                       OLLAMA_CONTEXT_LENGTH='2048', OLLAMA_KEEP_ALIVE='0', OLLAMA_MAX_QUEUE='1',
                       OLLAMA_LOAD_TIMEOUT='5m', OLLAMA_NOPRUNE='1',
                       TMPDIR=str(root / 'tmp'), CUDA_VISIBLE_DEVICES='-1', HIP_VISIBLE_DEVICES='-1')
    runtime_home = root / 'software' / 'ollama-home'
    runtime_home.mkdir(exist_ok=True)
    environment['HOME'] = str(runtime_home)
    limit = 128 * 1024 ** 3

    def child_limits():
        current_soft, current_hard = resource.getrlimit(resource.RLIMIT_AS)
        bound = limit if current_hard == resource.RLIM_INFINITY else min(limit, current_hard)
        if current_soft != resource.RLIM_INFINITY:
            bound = min(bound, current_soft)
        resource.setrlimit(resource.RLIMIT_AS, (bound, bound))
        os.sched_setaffinity(0, cpus)

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    process = subprocess.Popen([str(binary), 'serve'], env=environment,
                               stdout=server_log, stderr=subprocess.STDOUT,
                               start_new_session=True, preexec_fn=child_limits)
    try:
        deadline = time.monotonic() + 60
        while True:
            if process.poll() is not None:
                raise RuntimeError('Ollama exited during startup; share the server log tail')
            try:
                version = request(opener, '/api/version', timeout=2)
                break
            except (urllib.error.URLError, TimeoutError, OSError):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Ollama startup timed out') from None
                time.sleep(1)
        emit('Ollama version: ' + str(version.get('version')))
        tags = request(opener, '/api/tags')
        if not any(item.get('name') == 'gpt-oss:20b' or item.get('model') == 'gpt-oss:20b'
                   for item in tags.get('models', [])):
            raise RuntimeError('GPT-OSS 20B is not listed in the local model store')
        emit('Model found locally. Loading and generating; allow up to 10 minutes.')
        started = time.monotonic()
        result = request(opener, '/api/generate', payload(count), timeout=600)
        emit('Elapsed including model load: %.2f seconds' % (time.monotonic() - started))
        response = result.get('response', '').strip()
        emit('Response: ' + (response or '[no final response within token budget]'))
        tokens = result.get('eval_count', 0)
        duration = result.get('eval_duration', 0)
        emit('Generated tokens (including reasoning): %d' % tokens)
        if duration:
            emit('Generation speed: %.2f tokens/second' % (tokens * 1e9 / duration))
        emit('Model load: %.2f seconds' % (result.get('load_duration', 0) / 1e9))
        emit('Stop reason: ' + str(result.get('done_reason', 'unknown')))
        if not result.get('done') or not response:
            raise RuntimeError('generation did not produce a completed final response; review the summary')
        emit('SUCCESS: local GPT-OSS 20B inference completed.')
    finally:
        stop_owned_group(process)
        emit('Temporary Ollama server stopped.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--threads', type=int, help='default: available physical-core count')
    args = parser.parse_args()
    os.umask(0o077)
    root = Path.home() / 'ran-ai'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-%d' % os.getpid()
    summary = root / 'logs' / ('06-inference-' + stamp + '.log')
    server = root / 'logs' / ('06-ollama-' + stamp + '.log')
    try:
        with summary.open('x', encoding='utf-8') as log, server.open('x', encoding='utf-8') as server_log:
            def emit(message):
                print(message, flush=True)
                log.write(message + '\n')
                log.flush()
            try:
                run(root, args.threads, emit, server_log)
            except (Exception, KeyboardInterrupt) as error:
                emit('FAILED: %s: %s' % (type(error).__name__, error))
                emit('Server log: ' + str(server))
                return 1
            emit('Summary log: ' + str(summary))
    except OSError as error:
        print('Cannot open project logs: %s' % error, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
