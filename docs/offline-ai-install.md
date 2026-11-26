# Install AI first: Windows download, offline Linux installation

Start with CPU-based Ollama and GPT-OSS 20B. Leave GPT-OSS 120B, embeddings,
RAG and the dashboard until the first bounded inference test succeeds.
PyTorch, CUDA and GPU drivers are not prerequisites for this Ollama CPU path.

## 1. Office Windows laptop: build the bundle

Save both scripts in the SAME folder:

- `scripts/04_download_ai_bundle.ps1`
- `scripts/05_install_ai_offline.py`

Open PowerShell in that folder. The working command on the office laptop,
confirmed on November 25, 2026, is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\04_download_ai_bundle.ps1 -AllowOfflineRevocationCheck
```

This downloads files only: it does not install Windows Ollama or run the model.
No Python is required on Windows. Allow at least 40 GiB free for the download;
the model itself is approximately 14 GB and the Linux runtime adds several GB.
The default destination is `Downloads\ran-ai-offline-bundle` under your user home.
Use `-OutputDirectory D:\ran-ai-offline-bundle` to choose another drive.

Sources are the official Ollama GitHub release, Ollama model registry and PyPI.
The first run records a release and model manifest so interrupted runs use the
same artifacts. Downloads resume with Windows curl and are checked against
upstream SHA-256 digests. `bundle.json` records all transferred file hashes.
If a release layout or registry response changes, stop and share the error.
Do not bypass a checksum error. The bundle metadata is not a digital signature;
keep it with its trusted download source.

The office laptop previously reported `CRYPT_E_NO_REVOCATION_CHECK` (0x80092012).
Adding `-AllowOfflineRevocationCheck` resolved the reported download failure.
The confirmed invocation is:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\04_download_ai_bundle.ps1 -AllowOfflineRevocationCheck
```

This opts into curl's `--ssl-revoke-best-effort` for these downloads only. Missing
or offline revocation-list endpoints no longer block the transfer; certificate
chain, hostname and file checksum verification remain enabled. Known revoked
certificates are still rejected. It does not change Windows certificate settings.
Normal mode keeps strict revocation checks. If corporate policy requires live
revocation checks, ask IT to restore access instead. Existing verified downloads
are reused and `.part` downloads resume. See the [curl option reference](https://curl.se/docs/manpage.html#--ssl-revoke-best-effort).

## 2. Transfer the entire folder

Use your existing office-to-lab transfer method. Keep every file and subfolder.
The final layout on the AI server must be:

```text
$HOME/ran-ai/software/ran-ai-offline-bundle/
  bundle.json
  release.json
  ollama-linux-amd64.tar.zst
  05_install_ai_offline.py
  wheels/...
  models/blobs/...
  models/manifests/registry.ollama.ai/library/gpt-oss/20b
```

Do not put the bundle under `/tmp`, `/usr` or the root filesystem. Leave at least
60 GiB free in `/home` AFTER transfer for extraction and the separate model store.

## 3. AI server: install offline, without sudo

After the entire bundle has finished uploading, run this command on the AI
server as your normal account. It installs **both Ollama and GPT-OSS 20B** from
the transferred bundle; no additional model download is needed:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/software/ran-ai-offline-bundle/05_install_ai_offline.py"
```

The installer validates hashes, installs only the Linux `zstandard` wheel into
the existing project venv, unpacks a versioned Ollama runtime beneath project
`software/`, and copies the model store into `models/ollama/`. Paths are recorded
in local `config/ollama-offline.json`. Timestamped logs are under project `logs/`.
Original downloaded files are retained for recovery. Existing differing model
files are not overwritten. No system Python/package/container changes occur.

The binary `--version` check tests initial executable compatibility with Rocky
Linux 8.10 / glibc 2.28. A message saying no Ollama server is running can be normal
during this check. The CPU backend and actual model compatibility still require
the next inference test; the script does not claim to have verified them.

Installation succeeded with Ollama 0.40.18 on November 26, 2026. Proceed with the
following controlled inference test rather than an unrestricted background service.

## 4. First inference test on the dedicated AI server

The user confirmed on November 26, 2026 that the server is dedicated to AI and
all CPUs may be used regardless of earlier lab workloads. This supersedes the
earlier requirement to identify unused cores before inference. Do not alter
existing kernel isolation, HugePages, BIOS, or system services.

Transfer `scripts/06_test_ai_inference.py` through the office laptop and run from
its directory on the AI server:

```bash
"$HOME/ran-ai/venv/bin/python" 06_test_ai_inference.py
```

This uses the current allowed CPU set and defaults to one inference thread per
available physical core (expected 56). It starts a temporary local-only Ollama
server at `127.0.0.1:11435`, verifies that the model is listed locally and sends
one synthetic question. No external endpoint is contacted by the test client;
cloud features are disabled. A single request uses CPU inference, 2,048-token
context, low reasoning effort and at most 256 generated tokens. A per-process
128 GiB virtual-address-space limit is inherited by the model runner. This is
not a RAM reservation or an aggregate cgroup memory limit. Model loading uses
memory-mapped files and counts against the address-space ceiling.

The request times out after 10 minutes. The script stops only its own new server
process group on completion, error or Ctrl+C. An occupied port causes it to stop
before starting anything. Logs are saved under project `logs/`; share the summary
response, timings and speed. If startup fails, share the last lines of the printed
server-log filename. No persistent service is installed.

To compare thread counts later, rerun with `--threads 28`, for example. More
threads do not necessarily improve token generation; use the first measured
result before tuning or starting GPT-OSS 120B.

## References

- [Official Linux install guide](https://docs.ollama.com/linux)
- [GPT-OSS 20B model](https://ollama.com/library/gpt-oss:20b)
- [Ollama model storage layout source](https://github.com/ollama/ollama/blob/main/manifest/paths.go)
- [zstandard 0.23.0 Linux wheels](https://pypi.org/project/zstandard/0.23.0/#files)

## Validation scope

PowerShell syntax and download-metadata selection are checked separately from
large downloads. Unit tests cover checksum calculation and safe runtime archive
paths/extraction, using a simulated decompressor. The complete multi-GB download,
Linux wheel installation, runtime loading and inference are verified on the
office laptop/target server, not assumed from these unit tests.
