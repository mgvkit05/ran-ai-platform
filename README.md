# RAN AI Platform

Self-hosted 5G KPI analytics with local AI inference. The current deployment
priority is validating GPT-OSS 20B inference on the offline AI server.

## Next command: first inference test

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

## Next command: AI server installation

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
