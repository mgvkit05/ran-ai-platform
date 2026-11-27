# Reversible CPU runner recovery test

The latest diagnostic showed an idle API server, zero listed models, no runtime
worker and about 690 GiB of available host RAM. That snapshot was after canceled
loading, not evidence of what the runner was doing during warm-up. There is no
confirmed cause for the slowdown yet. Host memory availability does not rule out
per-process limits or a backend/kernel issue.

Test a smaller startup configuration without reinstalling runtime/model software.
This deliberately changes three settings together to test recovery, not to isolate
one proven culprit. Existing CPU affinity, memory ceiling, database and source
files remain unchanged.

## Transfer two updated scripts

Place the latest copies in the AI server's app directory:

```text
/home/sysadmin/ran-ai/app/07_manage_ollama.py
/home/sysadmin/ran-ai/app/16_counter_tools.py
```

The manager's old copy can have been in the software bundle directory. Use the
following full app paths to ensure the new version runs; both use the same existing
managed-instance state and installed model store.

## Stop, then start the recovery profile

Interrupt any waiting question client with Ctrl+C, then run in SSH:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/07_manage_ollama.py" stop
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/07_manage_ollama.py" start --threads 8 --batch-size 32 --disable-flash
```

Only the verified managed Ollama instance is stopped. ClickHouse is untouched.
The new start reports its profile, log path, progress every 10 seconds and final
warm-up duration. The warm-up HTTP idle timeout is 180 seconds, not an inference
speed target. If startup fails, the newly started process group is cleaned up.
`--disable-flash` sets `OLLAMA_FLASH_ATTENTION=false` only for the child server;
it does not modify global or shell settings.

If warm-up is still pending after about 30 seconds, leave that command running
and capture the following from a **second SSH session**, before timeout/cancellation:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/17_diagnose_ollama.py"
```

Share that output and the final startup result. This can show whether an active
runner is consuming CPU, faulting pages, or remaining blocked. Do not repeatedly
restart or queue additional generation requests while diagnosing.

## Only after startup reports SUCCESS

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/07_manage_ollama.py" ask "Reply with exactly: OK."
```

If the minimal question responds, retry the counter explanation:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/16_counter_tools.py" explain --pick 1
```

The manager records batch size and thread count; its questions and the updated
counter tool both reuse them with 2,048-token context. This avoids unintentionally
restoring the default 512-token batch after warming up at 32. Flash attention is
controlled at server startup. Status prints the recorded profile.

## Revert if needed

Stop the managed instance, then run `start` without recovery flags. Defaults are
up to 32 threads and batch size 512; flash attention uses the runtime/environment
default. No persistent operating-system configuration is changed by these flags.
Do not revert or retune while a request is still running.

Local tests cover request/state configuration consistency; successful CPU backend
loading must be established by the target-server test. The existing 128 GiB
per-process virtual-address limit remains unchanged in this experiment.

References: [Ollama environment options](https://github.com/ollama/ollama/blob/main/envconfig/config.go)
and [request batch option](https://github.com/ollama/ollama/blob/main/api/types.go).
