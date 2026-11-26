# Offline ClickHouse installation

Local Ollama, GPT-OSS 20B, persistent model loading and interactive Backspace
editing have been confirmed working. Next, install the analytical database binary.
The collector-to-AI-server ZIP handoff will be integrated later. The model will
query controlled data tools; KPI arithmetic remains deterministic in the database.

## Office laptop

Save `08_download_clickhouse_bundle.ps1` and `09_install_clickhouse_offline.py`
beside each other. In PowerShell in that folder run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\08_download_clickhouse_bundle.ps1 -AllowOfflineRevocationCheck
```

The explicit revocation option uses the same working curl setting as the AI
download. Certificate chain, hostname and SHA256 checks remain enabled.
The default output is `$HOME\Downloads\ran-ai-clickhouse-bundle`. Allow 2 GiB
free on the laptop. The package selected from official release metadata on
November 26, 2026 was about 241 MiB; future releases can differ. A release is
pinned in `release.json` on first run. Partial downloads resume on rerun.

The package is the official Linux AMD64 `clickhouse-common-static` tarball, not a
Windows build or debug-symbol package. The downloader verifies GitHub's published
SHA256 digest, records transfer hashes and copies the offline installer into the
bundle. No ClickHouse software is installed on Windows. No Python is required there.

## AI server

Transfer the entire `ran-ai-clickhouse-bundle` folder to:

```text
$HOME/ran-ai/software/ran-ai-clickhouse-bundle
```

Then run without sudo:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/software/ran-ai-clickhouse-bundle/09_install_clickhouse_offline.py"
```

The installer verifies transfer checksums, reads only the expected regular binary
from the package, and places it in a versioned project software directory. It
never runs package installation scripts, RPM tools or system service commands.
It checks `clickhouse client --version`, records the binary path in local
`config/clickhouse-offline.json`, and saves a timestamped log under project `logs/`.
Allow at least 5 GiB free under `/home` after transfer. No system Python changes,
database server startup or changes to the running Ollama process occur.

Share the version output and SUCCESS/FAILED result. After review, the next phase
will create a project-local database configuration, set memory/thread limits,
bind to localhost and run a synthetic table insert/query test. Production data
schema will require a representative CSV structure, retained locally or sanitized.

## Verification scope and sources

The release asset name and digest were checked against the official GitHub API.
Tests cover copying only the intended executable and rejecting symlink binaries.
Actual binary compatibility remains a target-server check.

- [Official ClickHouse release packages](https://github.com/ClickHouse/ClickHouse/releases)
- [Official package repository](https://packages.clickhouse.com/)
