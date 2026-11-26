# Persistent local Ollama instance

The first CPU inference test succeeded on November 26, 2026: Ollama 0.40.18,
GPT-OSS 20B, 32 inference threads, 14.32 generated tokens/second, 12.67 seconds
model loading and 15.13 seconds total for the short synthetic request. This is
a smoke-test measurement, not a long-prompt or concurrency benchmark. The shell
allowed 36 logical CPUs representing 32 physical cores at the time of the test.

## Start

Transfer `scripts/07_manage_ollama.py` to the AI server. From its directory run:

```bash
"$HOME/ran-ai/venv/bin/python" 07_manage_ollama.py start
```

This uses the installed runtime/model and no internet. The manager defaults to
32 inference threads, reduced if fewer logical CPUs are allowed, inherits the
current CPU affinity, and retains the tested 128 GiB per-process virtual address
space ceiling. This ceiling is not a RAM reservation or aggregate cgroup limit.
It runs one request at a time with 2,048-token context and keeps one loaded model.
Questions are capped at 1,024 generated tokens including reasoning.

The server binds only `http://127.0.0.1:11435`; cloud features are disabled. The
startup command waits for the model to load, confirms it is resident, then exits
while Ollama keeps running. Detached stdio/session and ignored SIGHUP allow the
process to remain after normal terminal closure. A host's logout cleanup policy
may still terminate it. There is no systemd unit, automatic restart after failure,
or automatic boot startup; rerun `start` if the process stops or the server reboots.
No sudo, firewall changes or modifications to existing services are required.

## Status and questions

```bash
"$HOME/ran-ai/venv/bin/python" 07_manage_ollama.py status
"$HOME/ran-ai/venv/bin/python" 07_manage_ollama.py ask
```

`ask` prompts for a question, prints the final answer and generation speed, and
keeps the model loaded. It does not save chat history or implement multi-turn
conversation memory. Interactive entry avoids putting questions in shell history.
An explicit question is also supported:

```bash
"$HOME/ran-ai/venv/bin/python" 07_manage_ollama.py ask "Explain what a 5G handover is."
```

The model does not yet have database tools or confidential-document retrieval.
Its generic answers are not evidence about the actual network. Startup and
request errors do not trigger model downloads. The manager does not log question
or answer text, but Ollama's own operational log remains local and should be
reviewed before sharing. Requests have a 10-minute client timeout; if a timed-out
request continues in the backend, `stop` terminates the managed instance.

## Stop or change threads

```bash
"$HOME/ran-ai/venv/bin/python" 07_manage_ollama.py stop
```

Only the process group created by this manager is stopped. The state records PID,
Linux process start time and executable path to guard against PID reuse. An
occupied port without matching manager state is not adopted or stopped.
An exclusive file lock serializes start/stop operations.

To change the thread count, stop, then run `start --threads 28`, for example.
Starting twice does not launch a second managed instance. State is stored under
`$HOME/ran-ai/run`; timestamped Ollama logs are under `$HOME/ran-ai/logs`.
If a state file is stale, start can replace it after verifying the port is free.

## Verification

Local tests cover PID-lifetime parsing, PID reuse rejection, refusal to stop an
unowned process and persistent-model request settings. Background process startup,
real Linux signals, model residency and terminal-disconnect behavior need target
server verification. Share the `start` and `status` output first.

Reference: [Ollama model keep-alive documentation](https://docs.ollama.com/faq).
