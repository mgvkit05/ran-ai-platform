#!/usr/bin/env python3
"""Manage a persistent local-only Ollama process: start, status, ask, stop.
Save as: /home/sysadmin/ran-ai/app/07_manage_ollama.py
Run: "$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/07_manage_ollama.py" start
Recovery experiment (after stop): append --threads 8 --batch-size 32 --disable-flash
No sudo, package downloads or system service changes. Restart manually after reboot.
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
import threading
import time
import urllib.error
import urllib.request

HOST = '127.0.0.1:11435'
MODEL = 'gpt-oss:20b'


def read_question():
    # Importing readline enables Python input() line editing. SSH clients may
    # send either ASCII Backspace (Ctrl-H) or Delete (DEL) for the same key.
    try:
        import readline
    except ImportError:
        raise RuntimeError('Python readline is unavailable. Use ask "your question" until terminal line editing is available.') from None
    if 'libedit' in (readline.__doc__ or ''):
        readline.parse_and_bind('bind "^H" ed-delete-prev-char')
        readline.parse_and_bind('bind "^?" ed-delete-prev-char')
    else:
        readline.parse_and_bind('"\\C-h": backward-delete-char')
        readline.parse_and_bind('"\\C-?": backward-delete-char')
    # Questions should not be added to readline history.
    if hasattr(readline, 'set_auto_history'):
        readline.set_auto_history(False)
    return input('Question: ')


def identity(pid, proc=Path('/proc')):
    """Identify the Linux process lifetime, not just a reusable PID."""
    try:
        fields = (proc / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        return fields[19]
    except (OSError, IndexError):
        return None


def owned(state):
    try:
        pid = int(state['pid'])
        return (pid > 1 and identity(pid) == state['start_ticks']
                and os.getpgid(pid) == pid
                and (Path('/proc') / str(pid) / 'exe').resolve() == Path(state['binary']).resolve())
    except (OSError, KeyError, ValueError, TypeError):
        return False


def api(endpoint, data=None, timeout=10):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    query = urllib.request.Request('http://' + HOST + endpoint,
                                   data=None if data is None else json.dumps(data).encode(),
                                   headers={'Content-Type': 'application/json'})
    try:
        with opener.open(query, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError('Ollama HTTP %d: %s' % (error.code, error.read(4096).decode(errors='replace'))) from None


def generation(threads, prompt='', batch_size=512):
    return {'model': MODEL, 'prompt': prompt, 'stream': False, 'think': 'low',
            'keep_alive': -1, 'options': {'num_thread': threads, 'num_gpu': 0,
            'num_ctx': 2048, 'num_batch': batch_size, 'num_predict': 1024, 'temperature': 0}}


def load_state(path):
    if not path.exists():
        return None
    return json.loads(path.read_text())


def stop(state):
    if not owned(state):
        raise RuntimeError('state does not match a live managed Ollama process; no process signalled')
    pid = state['pid']
    os.killpg(pid, signal.SIGTERM)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    # The group was created and verified by this manager; terminate leftover runners.
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def start(root, state_path, requested_threads, batch_size=512, disable_flash=False):
    import resource
    existing = load_state(state_path)
    if existing and owned(existing):
        print('Managed Ollama is already running. Use status or ask.')
        return
    config = json.loads((root / 'config' / 'ollama-offline.json').read_text())
    binary = Path(config['binary']).resolve()
    models = Path(config['models']).resolve()
    binary.relative_to((root / 'software').resolve())
    models.relative_to((root / 'models').resolve())
    if not binary.is_file() or not (models / 'manifests/registry.ollama.ai/library/gpt-oss/20b').is_file():
        raise ValueError('installed runtime or model is missing')
    cpus = os.sched_getaffinity(0)
    threads = min(32, len(cpus)) if requested_threads is None else requested_threads
    if not 1 <= threads <= len(cpus):
        raise ValueError('thread count must be between 1 and the allowed logical CPU count')
    if not 1 <= batch_size <= 512:
        raise ValueError('batch size must be between 1 and 512')
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', 11435))
        except OSError:
            raise RuntimeError('port 11435 is occupied; no existing service was modified') from None
    home = root / 'software' / 'ollama-home'
    home.mkdir(exist_ok=True)
    environment = dict(os.environ, HOME=str(home), TMPDIR=str(root / 'tmp'),
                       OLLAMA_HOST=HOST, OLLAMA_MODELS=str(models), OLLAMA_NO_CLOUD='1',
                       OLLAMA_KEEP_ALIVE='-1', OLLAMA_NUM_PARALLEL='1', OLLAMA_MAX_LOADED_MODELS='1',
                       OLLAMA_MAX_QUEUE='2', OLLAMA_CONTEXT_LENGTH='2048', OLLAMA_LOAD_TIMEOUT='5m',
                       OLLAMA_NOPRUNE='1', OLLAMA_DEBUG='0', CUDA_VISIBLE_DEVICES='-1', HIP_VISIBLE_DEVICES='-1')
    if disable_flash:
        environment['OLLAMA_FLASH_ATTENTION'] = 'false'

    def limits():
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        bound = 128 * 1024 ** 3
        for previous in (soft, hard):
            if previous != resource.RLIM_INFINITY:
                bound = min(bound, previous)
        resource.setrlimit(resource.RLIMIT_AS, (bound, bound))
        os.sched_setaffinity(0, cpus)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    log_path = root / 'logs' / ('07-ollama-%s-%d.log' % (stamp, os.getpid()))
    with log_path.open('x') as log:
        process = subprocess.Popen([str(binary), 'serve'], env=environment, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                   preexec_fn=limits, close_fds=True)
    state = {'pid': process.pid, 'start_ticks': identity(process.pid), 'binary': str(binary),
             'threads': threads, 'batch_size': batch_size, 'disable_flash': disable_flash,
             'cpus': sorted(cpus), 'log': str(log_path), 'host': HOST}
    try:
        # Confirm executable identity once exec has completed before writing control state.
        deadline = time.monotonic() + 60
        while True:
            if process.poll() is not None:
                raise RuntimeError('Ollama exited. Check ' + str(log_path))
            try:
                version = api('/api/version', timeout=2)
                if owned(state):
                    break
            except (urllib.error.URLError, OSError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError('Ollama startup timed out. Check ' + str(log_path))
            time.sleep(0.5)
        partial = state_path.with_suffix('.tmp')
        partial.write_text(json.dumps(state, indent=2))
        partial.replace(state_path)
        print('Ollama %s started at http://%s; inference threads: %d.' % (version.get('version'), HOST, threads), flush=True)
        print('Startup profile: threads=%d, batch=%d, flash=%s' %
              (threads, batch_size, 'disabled' if disable_flash else 'runtime default'), flush=True)
        print('Log: ' + str(log_path), flush=True)
        print('Loading GPT-OSS 20B; warm-up idle timeout is 180 seconds.', flush=True)
        warmup_started = time.monotonic()
        warmup_done = threading.Event()
        def progress():
            while not warmup_done.wait(10):
                print('[%.0fs: awaiting model warm-up; run diagnostic 17 in a second SSH session if delayed]' %
                      (time.monotonic() - warmup_started), flush=True)
        threading.Thread(target=progress, daemon=True).start()
        try:
            result = api('/api/generate', generation(threads, batch_size=batch_size), timeout=180)
        finally:
            warmup_done.set()
        if not result.get('done'):
            raise RuntimeError('model warm-up did not complete')
        running = api('/api/ps')
        if not any(item.get('name') == MODEL or item.get('model') == MODEL for item in running.get('models', [])):
            raise RuntimeError('model was not resident after warm-up')
        print('SUCCESS: persistent local Ollama is ready; GPT-OSS 20B is loaded.')
        print('Warm-up elapsed: %.2f seconds' % (time.monotonic() - warmup_started))
        print('Log: ' + str(log_path))
        print('No boot service installed. Run start again after a reboot.')
    except BaseException:
        # Only clean up the child created by this invocation, including failed exec/startup.
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=15)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        if state_path.exists() and load_state(state_path) == state:
            state_path.unlink()
        raise


def status(state):
    if not state or not owned(state):
        print('Managed Ollama is stopped (or state is stale).')
        return 1
    version = api('/api/version')
    running = api('/api/ps')
    print('RUNNING: Ollama %s at http://%s; threads: %d' % (version.get('version'), HOST, state['threads']))
    print('Batch: %d; flash attention override: %s' %
          (state.get('batch_size', 512), 'disabled' if state.get('disable_flash') else 'runtime default'))
    for model in running.get('models', []):
        print('Loaded: %s; reported memory: %.2f GiB' % (model.get('name'), model.get('size', 0) / 1024 ** 3))
    print('Log: ' + state['log'])
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'status', 'ask', 'stop'))
    parser.add_argument('prompt', nargs='?', help='ask only; omitted to enter interactively')
    parser.add_argument('--threads', type=int, help='start only; default 32 or fewer if restricted')
    parser.add_argument('--batch-size', type=int, default=512, help='start only; CPU batch size, 1-512')
    parser.add_argument('--disable-flash', action='store_true', help='start only; disable flash attention for a recovery test')
    args = parser.parse_args()
    if not sys.platform.startswith('linux'):
        parser.error('run on the Linux AI server')
    os.umask(0o077)
    root = Path.home() / 'ran-ai'
    try:
        if os.geteuid() == 0:
            raise ValueError('run without sudo')
        if not (root / 'config' / 'ollama-offline.json').is_file():
            raise ValueError('offline installation configuration is missing')
        run_dir = root / 'run'
        run_dir.mkdir(exist_ok=True)
        state_path = run_dir / 'ollama-managed.json'
        if args.action in ('start', 'stop'):
            import fcntl
            with (run_dir / 'ollama-manager.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if args.action == 'start':
                    start(root, state_path, args.threads, args.batch_size, args.disable_flash)
                else:
                    state = load_state(state_path)
                    if state and owned(state):
                        stop(state)
                        state_path.unlink()
                        print('Managed Ollama stopped; model memory released.')
                    else:
                        print('No live managed Ollama instance found; nothing stopped.')
        else:
            state = load_state(state_path)
            if args.action == 'status':
                return status(state)
            if not state or not owned(state):
                raise RuntimeError('managed Ollama is not running; run start first')
            prompt = args.prompt if args.prompt is not None else read_question()
            if not prompt.strip():
                raise ValueError('question is empty')
            began = time.monotonic()
            result = api('/api/generate', generation(state['threads'], prompt, state.get('batch_size', 512)), timeout=180)
            print(result.get('response', '').strip() or '[No final response within the token budget]')
            if result.get('eval_duration'):
                print('Generation: %.2f tokens/s; elapsed: %.2f seconds' %
                      (result.get('eval_count', 0) * 1e9 / result['eval_duration'], time.monotonic() - began))
            if result.get('done_reason') == 'length':
                print('Generation reached the 1024-token limit; the answer may be incomplete.')
            if not result.get('done') or not result.get('response', '').strip():
                raise RuntimeError('no completed final response; server remains running for retry')
    except (Exception, KeyboardInterrupt) as error:
        print('FAILED: %s: %s' % (type(error).__name__, error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
