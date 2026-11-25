#!/usr/bin/env bash
# AI server readiness checks for ran-ai-platform.
# Assumes a Linux server with Bash. No sudo, installations, or settings changes.
# Copy this file to the AI server, then run:
#   bash ai-server-commands.sh 2>&1 | tee ai-server-readiness.txt
# Review the report before sharing it; it includes system and hardware details.
# Deployment commands will require application code and runtime requirements;
# the repository currently contains only a README.

set -u

section() { printf '\n=== %s ===\n' "$1"; }
check() {
  if command -v "$1" >/dev/null 2>&1; then
    "$@" || printf 'Check failed: %s\n' "$1"
  else
    printf 'Not available: %s\n' "$1"
  fi
}

section 'Operating system and architecture'
if [ -r /etc/os-release ]; then cat /etc/os-release; fi
check uname -srm

section 'CPU'
check lscpu

section 'Memory'
check free -h

section 'Disk space in current directory'
check df -h .

section 'NVIDIA GPU and driver'
check nvidia-smi

section 'CUDA compiler (optional; not required by all GPU runtimes)'
check nvcc --version

section 'Python and pip'
check python3 --version
check python3 -m pip --version

section 'Installed PyTorch and GPU access'
if command -v python3 >/dev/null 2>&1; then
  python3 - <<'PY'
import importlib.util

if importlib.util.find_spec('torch') is None:
    print('PyTorch is not installed in the current Python environment.')
else:
    import torch
    print('PyTorch:', torch.__version__)
    print('PyTorch CUDA runtime:', torch.version.cuda)
    print('CUDA available:', torch.cuda.is_available())
    print('GPU count:', torch.cuda.device_count())
    for index in range(torch.cuda.device_count()):
        print('GPU', index, ':', torch.cuda.get_device_name(index))
PY
fi

section 'Git'
check git --version

section 'Container tooling'
check docker --version
check docker compose version
check nvidia-ctk --version

section 'Existing model runtimes'
check ollama --version
check python3 -m pip show vllm transformers accelerate

section 'Checks complete'
printf 'No software was installed and no server settings were changed.\n'
