# RAN AI Platform

Self-hosted 5G KPI analytics with local AI inference. The current deployment
priority is running persistent local GPT-OSS 20B inference on the offline AI server.

## Next step: install ClickHouse offline

The user confirmed that persistent Ollama and interactive Backspace editing work.
Install the analytical database next using the [offline ClickHouse instructions](docs/offline-clickhouse-install.md).
Download `scripts/08_download_clickhouse_bundle.ps1` and
`scripts/09_install_clickhouse_offline.py` together on the office laptop, then run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\08_download_clickhouse_bundle.ps1 -AllowOfflineRevocationCheck
```

Transfer the complete generated bundle and run the installation command in the
guide. This phase installs the executable without starting a database server.

## Current step: persistent local Ollama

The first GPT-OSS 20B inference test succeeded using 32 threads at 14.32 tokens/s.
Transfer `scripts/07_manage_ollama.py` to the AI server, then run from its directory:

```bash
"$HOME/ran-ai/venv/bin/python" 07_manage_ollama.py start
"$HOME/ran-ai/venv/bin/python" 07_manage_ollama.py status
"$HOME/ran-ai/venv/bin/python" 07_manage_ollama.py ask
```

Ollama stays running locally and the model stays loaded between questions. No
sudo or internet is required. Run the same script with `stop` to release it.
This is a background process, not a boot service; run `start` again after reboot.
See [persistent instance instructions](docs/persistent-ollama.md).

## Completed step: first inference test

Ollama 0.40.18 and GPT-OSS 20B installation succeeded on November 26, 2026.
The user confirmed that the server is dedicated to AI and all CPUs may be used.
Earlier workload-sharing restrictions no longer constrain CPU selection for this
test. Existing kernel isolation, HugePages and system configuration remain unchanged.

Transfer `scripts/06_test_ai_inference.py` to the AI server and, from its directory,
run without sudo:

```bash
"$HOME/ran-ai/venv/bin/python" 06_test_ai_inference.py
```

The script uses the available physical-core count for inference threads (expected
56 on this server), makes one local CPU inference request, prints the response
and speed, and stops its temporary Ollama server. Share the final output.
See the [inference test instructions](docs/offline-ai-install.md#4-first-inference-test-on-the-dedicated-ai-server).

## Completed step: AI server installation

After the entire `ran-ai-offline-bundle` folder has finished transferring to
`$HOME/ran-ai/software/ran-ai-offline-bundle`, run on the **Linux AI server**,
without sudo:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/software/ran-ai-offline-bundle/05_install_ai_offline.py"
```

This verifies the transferred files and installs both Ollama and GPT-OSS 20B.
The model is already in the bundle; no further download is needed. Installation
does not start inference or change existing lab services. Share the final version
and SUCCESS or FAILED output before proceeding to a resource-limited inference test.

See [offline AI installation instructions](docs/offline-ai-install.md) for the
Windows download command, required folder layout, disk space and installation details.

Keep model files, credentials, proprietary KPI files and confidential documents
on the lab systems and outside GitHub.
