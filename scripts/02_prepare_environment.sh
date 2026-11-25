#!/usr/bin/env bash
# Offline Phase 2: create only user-owned project directories and a Python venv.
# No downloads, package upgrades, service changes or inference.
# Run: bash 02_prepare_environment.sh
set -euo pipefail
umask 077
fail() { printf 'ERROR: %s\n' "$1" >&2; exit 1; }
[ "$(id -u)" != 0 ] || fail 'Run as your regular lab account, without sudo.'
command -v python3 >/dev/null || fail 'python3 is required.'
command -v readlink >/dev/null || fail 'readlink is required.'
home_path=$(readlink -f -- "$HOME")
case "$home_path" in /home/*) ;; *) fail 'Your home must resolve beneath /home.' ;; esac
[ -d "$home_path" ] && [ -w "$home_path" ] || fail 'Home is not writable.'
[ "$(stat -c %d -- "$home_path")" != "$(stat -c %d /)" ] || fail 'Home is on the root filesystem; stopping.'
project="$home_path/ran-ai"
[ ! -L "$project" ] || fail 'Project directory is a symlink; stopping.'
[ ! -e "$project" ] || [ -d "$project" ] || fail 'Project path exists and is not a directory.'
python3 -c 'import sys, venv, ensurepip, sqlite3; assert sys.version_info[:2] == (3, 9), "Expected audited Python 3.9"' || fail 'Python prerequisites failed.'
available_kb=$(df -Pk -- "$home_path" | awk 'END {print $4}')
[ "$available_kb" -ge 1048576 ] || fail 'At least 1 GiB free is required for environment preparation.'
mkdir -p -- "$project"
for directory in app bin models software config data data/raw data/landing data/staging data/quarantine database database/clickhouse database/sqlite knowledge logs reports backups tests tmp cache cache/pip; do
  target="$project/$directory"
  [ ! -L "$target" ] || fail "Project subdirectory is a symlink: $directory"
  [ ! -e "$target" ] || [ -d "$target" ] || fail "Project subdirectory is not a directory: $directory"
  mkdir -p -- "$target"
  [ -w "$target" ] || fail "Project subdirectory is not writable: $directory"
done
log="$project/logs/02-prepare-$(date -u +%Y%m%dT%H%M%SZ)-$$.log"
exec > >(tee "$log") 2>&1
trap 'printf "FAILED at line %s. Keep the log for diagnosis; do not proceed to installation.\n" "$LINENO"' ERR
export TMPDIR="$project/tmp"
export PIP_CACHE_DIR="$project/cache/pip"
export PIP_NO_INDEX=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
printf '=== Offline environment preparation ===\n'
[ ! -L "$project/venv" ] || fail 'venv is a symlink; stopping.'
if [ -e "$project/venv" ]; then
  [ -f "$project/venv/pyvenv.cfg" ] && [ -x "$project/venv/bin/python" ] || fail 'Existing venv is incomplete. Nothing was deleted; share the error.'
  printf 'Reusing existing virtual environment.\n'
else
  python3 -m venv "$project/venv"
  printf 'Created virtual environment.\n'
fi
"$project/venv/bin/python" -I -c 'import os, sys, sqlite3, importlib.metadata; expected = sys.argv[1]; assert sys.prefix != sys.base_prefix, "Not a virtual environment"; assert os.path.realpath(sys.prefix) == os.path.realpath(expected), "Unexpected environment location"; assert sys.version_info[:2] == (3, 9), "Expected Python 3.9"; print("Python:", sys.version.split()[0]); print("pip:", importlib.metadata.version("pip")); print("SQLite:", sqlite3.sqlite_version); connection = sqlite3.connect(":memory:"); connection.execute("CREATE VIRTUAL TABLE counter_search USING fts5(text)"); connection.close(); print("SQLite FTS5: available"); print("Virtual environment: verified")' "$project/venv"
printf '\n=== SUCCESS ===\n'
printf 'Project directories and isolated Python environment are ready.\n'
printf 'No software downloaded, system packages upgraded or AI workloads started.\n'
printf 'Log: %s\n' "$log"
printf 'Stop here and share this result before the offline dependency phase.\n'
