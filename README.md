# RAN AI Platform

Self-hosted 5G KPI analytics with local AI inference. The current deployment
priority is installing Ollama and GPT-OSS 20B on the offline AI server.

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
