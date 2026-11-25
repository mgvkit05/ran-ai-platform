# AI server: initial ZIP intake

Phase 2 environment preparation must already have succeeded. Transfer
`scripts/03_process_landing.py` to the AI server and run it from its download
directory, without sudo:

```bash
"$HOME/ran-ai/venv/bin/python" 03_process_landing.py
```

An empty landing folder is valid: all counts will be zero. A timestamped summary
is saved under `$HOME/ran-ai/logs`. No downloads or services are required.

For the next check, place one representative ZIP in `$HOME/ran-ai/data/landing`
and rerun the same command. Originals are retained. Do not enable a schedule yet.
Share only the printed counts or error type, not proprietary CSV content.

## Collector handoff contract

The collector uploads to this landing directory as `NAME.zip.part`, closes the
transfer, then renames it to `NAME.zip` in the same directory. Final names must
be unique and immutable. Incomplete files and symlinks are ignored. The intake
script itself makes no SFTP connections and does not configure the collector.

## What this phase does

- Reads ZIP members in place; no extraction to disk or counter-value loading yet.
- Validates archive CRCs, paths, headers and row widths.
- Counts rows, including quoted CSV records; skips empty records.
- Stores SHA-256 archive identity, CSV names, headers, row counts and acceptance
  time in `database/sqlite/intake.sqlite3`. This local database can contain
  confidential names and headers and must stay off GitHub.
- Skips byte-identical accepted ZIPs even when renamed. Repackaged ZIPs with
  different bytes require later record-level deduplication during database load.
- Retries rejected ZIPs on the next run; never marks them accepted or deletes them.
- Uses a Linux advisory lock to reject overlapping intake runs.

Defaults: comma delimiter, UTF-8 with optional BOM, at most 10,000 archive members,
2 GiB total uncompressed data and 512 MiB per member. Other formats, encodings,
empty/duplicate column names or larger files need review with a sanitized sample.
An alternative encoding can be supplied with `--encoding`. Limits deliberately
bound the first validation run. No inference or bulk database workload is started.

## Validation

Run `python -m unittest discover -s tests -v` from the repository root.
Tests cover archive path rejection, quoted/BOM CSVs, malformed CSVs, size limits,
duplicate ZIPs, incomplete uploads, retry after failure and concurrent-run rejection.
Local Windows tests simulate `fcntl`; actual Linux locking remains to be verified
on the target server. The script targets Python 3.9; local tests used Python 3.13.
