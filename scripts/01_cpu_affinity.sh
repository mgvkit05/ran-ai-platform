#!/usr/bin/env bash
# Read-only follow-up. No Python, sudo, installations or workload changes.
# Run: bash 01_cpu_affinity.sh
set -u
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
