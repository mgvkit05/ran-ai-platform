# Profile the uploaded TAR sample

The source collection is a TAR archive, correcting the earlier ZIP assumption.
The sample is already in `$HOME/ran-ai/data/landing`. Do not rename it to ZIP or
extract it manually. ClickHouse and Ollama can remain running.

Transfer `scripts/11_profile_archive.py` through the office laptop to
`$HOME/ran-ai/app/11_profile_archive.py` on the AI server. Run from any directory:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/11_profile_archive.py"
```

The newest completed supported archive in landing is selected automatically.
An explicit filename relative to landing can be passed as the first argument.
Supported formats are TAR, gzip/bzip2/xz-compressed TAR, and ZIP. Regular CSV and
gzip-compressed CSV members are sampled directly without writing extracted files.
Links and unsafe paths are skipped. Nested archives are not expanded in this phase.

## Output to share

Share the terminal summary. It shows archive format, member counts, CSV counts,
candidate schema groups, column counts, candidate header positions, delimiter and
encoding, sampled row-width mismatches and candidate role positions. It does not
print archive/member filenames, actual column headers, row values, cell identifiers
or timestamps from the data. Numeric-column counts describe only the first file
in each candidate schema group; value types may differ across other files.

The terminal prints at most 8 candidate schema groups. The complete sanitized
summary is saved as `reports/11-archive-summary-*.txt`. A separate local private
JSON report, `reports/11-archive-private-*.json`, retains member filenames and
headers for later schema mapping. It contains no sampled row values. Keep that
private report on the lab server; do not upload it or commit it to GitHub.
Both files are created with restrictive permissions.

Samples are bounded to 1 MiB and 200 data records per CSV, with a 64 MiB overall
sample budget and 10,000-member limit. Candidate headers are heuristically chosen
within the first 20 records, allowing common vendor preambles. They must be
verified before ingestion. UTF-8 with BOM and UTF-16 are recognized, with Latin-1
fallback explicitly reported. A truncated or unparsable sample does not establish
that the original CSV is corrupt. This is structural sampling, not full archive
CRC validation, total row counting, extraction or database ingestion.

The older `03_process_landing.py` is ZIP-only and is not the intake path for TAR
collections. A TAR-aware ingestion pipeline will be implemented after this report
identifies the source layout. Collector publication should use `NAME.tar.part`
during transfer and rename to `NAME.tar` in landing only after completion.

## Verification

Local synthetic tests cover TAR/ZIP reads, gzip CSVs, vendor preambles, delimiter
detection, link/path rejection, report redaction and bounded terminal output.
No proprietary data is required for those tests.
