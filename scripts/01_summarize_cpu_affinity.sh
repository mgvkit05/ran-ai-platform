#!/usr/bin/env bash
# Summarize an existing affinity report without repeating the server audit.
# Run beside cpu-affinity.txt: bash 01_summarize_cpu_affinity.sh
# Or specify the report: bash 01_summarize_cpu_affinity.sh /path/to/report.txt
set -eu
report=${1:-cpu-affinity.txt}
if [ ! -r "$report" ]; then
  printf 'Cannot read report: %s\nRun from the directory containing cpu-affinity.txt, or pass its path.\n' "$report" >&2
  exit 1
fi
printf '=== Thread totals by allowed CPU mask ===\n'
awk '/^process-/ {split($2,t,"="); count[$3]+=t[2]} END {for (cpu in count) print cpu, "threads=" count[cpu]}' "$report" | sort
printf '\n=== Read coverage ===\n'
grep '^Threads read:' "$report" || printf 'Coverage line missing; the input report may be incomplete.\n'
printf '\nAllowed masks describe where threads may run, not which CPUs are idle.\n'
