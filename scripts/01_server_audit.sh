#!/usr/bin/env bash
# Phase 1 only. Offline and unprivileged. Writes only its own report.
# Process names, arguments, users, IP addresses, hostnames and home paths are omitted.
# Review the report before sharing. It contains CPU topology and resource usage.
# Run: bash 01_server_audit.sh
set -u
umask 077
report="ran-ai-audit-$(date -u +%Y%m%dT%H%M%SZ)-$$.txt"
if [ ! -w . ]; then
  printf 'Run from a writable directory under /home.\n' >&2
  exit 1
fi
exec 3>&1
exec > "$report" 2>&1
section() { printf '\n=== %s ===\n' "$1"; }
check() {
  if command -v "$1" >/dev/null 2>&1; then
    "$@" || printf 'Check unavailable or failed: %s\n' "$1"
  else
    printf 'Command unavailable: %s\n' "$1"
  fi
}

section 'OS and kernel'
if [ -r /etc/os-release ]; then
  grep -E '^(NAME|VERSION_ID|PRETTY_NAME)=' /etc/os-release
fi
check uname -srm

section 'CPU topology'
check lscpu
check lscpu -e=CPU,CORE,SOCKET,NODE,ONLINE

section 'CPU isolation (no kernel arguments collected)'
for item in isolated nohz_full online; do
  printf '%s: ' "$item"
  if [ -r "/sys/devices/system/cpu/$item" ]; then
    cat "/sys/devices/system/cpu/$item"
  else
    printf 'unavailable\n'
  fi
done

section 'Memory and HugePages'
check free -h
grep -E '^(MemTotal|MemAvailable|HugePages_Total|HugePages_Free|HugePages_Rsvd|HugePages_Surp|Hugepagesize|Hugetlb):' /proc/meminfo
for node in /sys/devices/system/node/node[0-9]*; do
  [ -d "$node" ] || continue
  printf '\n%s\n' "${node##*/}"
  grep -E 'MemTotal|MemFree|HugePages' "$node/meminfo"
  for huge in "$node"/hugepages/hugepages-*; do
    [ -d "$huge" ] || continue
    printf '%s: ' "${huge##*/}"
    for metric in nr_hugepages free_hugepages; do
      printf '%s=' "$metric"
      cat "$huge/$metric" 2>/dev/null || printf 'unavailable\n'
    done
  done
done

section 'Storage capacity (paths and device identifiers omitted)'
for location in / /home "$HOME" .; do
  case "$location" in
    /) label='root filesystem' ;;
    /home) label='home filesystem' ;;
    "$HOME") label='user home filesystem' ;;
    *) label='report directory filesystem' ;;
  esac
  printf '\n%s\n' "$label"
  df -hT "$location" 2>/dev/null | awk 'NR == 1 {print "Type Size Used Available Use%"} NR > 1 {print $2,$3,$4,$5,$6}'
done
printf '\nHome writable: '
if [ -w "$HOME" ]; then printf 'yes\n'; else printf 'no\n'; fi
printf 'Existing project directory: '
if [ -d "$HOME/ran-ai" ]; then
  if [ -w "$HOME/ran-ai" ]; then printf 'writable\n'; else printf 'not writable\n'; fi
else
  printf 'not created\n'
fi
printf 'Running as root: '
if [ "$(id -u)" = 0 ]; then printf 'yes\n'; else printf 'no\n'; fi

section 'GPU and storage controllers'
if command -v lspci >/dev/null 2>&1; then
  lspci -nn | grep -Ei 'VGA|3D|Display|NVIDIA|RAID|SAS|NVMe' || true
else
  printf 'lspci unavailable\n'
fi

section 'Installed software (no installation or network access)'
check python3 --version
check python3 -c 'import importlib.metadata; print("pip:", importlib.metadata.version("pip"))'
check python3 -c 'import venv, ensurepip, sqlite3; print("venv and ensurepip available; SQLite:", sqlite3.sqlite_version)'
check docker --version
check docker compose version
check gcc --version
check cmake --version
check numactl --version
check rpm -q docker-ce containerd.io glibc python39 python3

section 'Current CPU and I/O sample (three one-second samples)'
check vmstat 1 3

printf '=== CPU affinity follow-up ===\n'
for item in online isolated nohz_full; do
  printf '%s: ' "$item"
  cat "/sys/devices/system/cpu/$item" 2>/dev/null || printf 'unavailable\n'
done
printf '\n=== Thread masks grouped by anonymous process ===\n'
process_count=0
skipped=0
thread_count=0
for process in /proc/[0-9]*; do
  [ -d "$process/task" ] || continue
  process_count=$((process_count + 1))
  declare -A masks=()
  for status in "$process"/task/[0-9]*/status; do
    cpus=''
    nodes=''
    if [ ! -r "$status" ]; then
      skipped=$((skipped + 1))
      continue
    fi
    while IFS=: read -r key value; do
      case "$key" in
        Cpus_allowed_list) cpus=${value//[[:space:]]/} ;;
        Mems_allowed_list) nodes=${value//[[:space:]]/} ;;
      esac
    done < "$status" 2>/dev/null
    if [ -z "$cpus" ] || [ -z "$nodes" ]; then
      skipped=$((skipped + 1))
      continue
    fi
    mask="$cpus|$nodes"
    masks["$mask"]=$(( ${masks["$mask"]:-0} + 1 ))
    thread_count=$((thread_count + 1))
  done
  for mask in "${!masks[@]}"; do
    printf 'process-%04d threads=%d cpus=%s nodes=%s\n' "$process_count" "${masks[$mask]}" "${mask%|*}" "${mask#*|}"
  done
  unset masks
done
printf '\nThreads read: %d; unreadable or vanished: %d\n' "$thread_count" "$skipped"
printf 'Aliases apply only to this run. Allowed CPUs are not necessarily idle.\n'
printf 'No CPU allocation has been selected; review alongside sibling topology and utilization.\n'

section 'Completion'
printf 'No packages installed, services changed, or workloads started.\n'
printf 'Affinity and utilization snapshots require review before selecting inference CPUs.\n'
printf 'Report complete. Review before sharing: %s\n' "$report" >&3
