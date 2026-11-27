# First complete TAR-to-ClickHouse load

The source owner confirmed that missing/blank `#index` and blank `LOCATION` are
valid. All CSVs and rows must be processed. No global/cell scope is inferred from
an empty location. Phase 12 diagnostics now accept those cases while retaining
the distinction between absent, blank and populated index records.

## 1. Transfer the loader

Save `scripts/14_ingest_vendor_tar.py` from GitHub and transfer it to:

```text
/home/sysadmin/ran-ai/app/14_ingest_vendor_tar.py
```

No replacement of the installed AI/database software is needed. The loader is
self-contained and does not need the other scripts beside it. ClickHouse must
be running at its existing local HTTP endpoint. Ollama can remain running.

## 2. Load the sample on the AI server

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/14_ingest_vendor_tar.py"
```

The newest completed TAR in `$HOME/ran-ai/data/landing` is selected automatically.
To choose a specific TAR, append its filename relative to landing. Do not unzip,
rename, delete or overwrite the original TAR. There are no network downloads or
new Python dependencies. The only database writes are this ingestion's tables,
views and records in database `ran_ai`.

The known sample previously contained 838 CSVs, 273,853 data rows and 3,520,845
counter fields, including 187 rows with blank LOCATION across 28 blank-index
files. The loader derives counts from the selected source instead of hardcoding
these numbers. Share the short final SUCCESS/count summary.

## 3. Confirm rerun behavior

Rerun the same command after SUCCESS. It should report:

```text
ALREADY LOADED: this exact TAR is committed; no data rows inserted again.
```

The archive SHA-256 identifies byte-identical archives even when filenames change.
Repacked archives with different bytes are distinct source archives, even if they
contain overlapping measurements. Cross-archive logical deduplication needs an
explicit measurement identity policy; the initial raw layer preserves every row.

## Preserved representation

- `ran_ai.counter_records` exposes complete source records. `raw_values` contains
  every CSV field in its original column order, as strings, including empty
  strings, identifier leading zeros and numeric decimal precision. CSV quoting
  is decoded normally; original bytes remain in the retained TAR.
- Common fields are also available as `ne_id`, `system_id`, `ne_name`,
  `init_time_raw`, `time_offset_raw`, `gran_period_raw`, and `location`.
- `init_time_utc` is an additional nullable UTC derivation with microsecond
  precision. The unmodified source timestamp and offset are always retained.
  If the derivation is unavailable, the row still loads and the summary counts it.
- `ran_ai.counter_files` exposes member filenames, exact header arrays, family,
  version, `index_present`, `index_definition` and original parsed metadata records.
  Missing index is `index_present=0`; present-but-blank is `index_present=1` with
  an empty definition. No scope or location is invented.
- Original counter labels, including unit suffixes, are retained in `counter_names`.
  Trailing parenthesized unit labels are additionally copied to `counter_units`;
  this is not a determination of aggregation semantics.
- `schema_id` hashes the exact header list. Archive hash, member ordinal, source
  member name, CSV content hash and row ordinal provide lineage without discarding
  duplicate-looking rows. Filenames and data remain in the lab database.
- Collection-period values stay raw; no unit conversion or automatic KPI
  aggregation is performed. Numeric conversions and metric-specific semantics
  will be implemented in controlled analytical queries after loading.

## Publication, interruption and recovery

Each attempt gets a unique load ID. Batched rows and file metadata are written to
`ran_ai.ingest_rows_v1` and `ran_ai.ingest_files_v1`. Counts, unique source row/file
positions, counter-field counts and source archive stability are verified before
one commit marker is inserted into `ran_ai.ingest_commits_v1`. The two public
views expose only load IDs referenced by that commit ledger.

This is application-level publication, not a multi-table ClickHouse transaction.
The loader does not retry ambiguous INSERT requests. An interrupted attempt can
leave raw staging records, which remain hidden from the public views. Retry checks
the commit ledger first; otherwise it starts a fresh load ID. Old partial records
are retained for diagnosis, not automatically deleted. A lost commit response may
mean the load actually completed; rerunning checks the ledger safely. Do not query
the staging tables for production analytics or manually alter the commit ledger.

A project file lock prevents simultaneous runs from this project on this server.
This is not a distributed ingestion lock. Run only one importer against these
tables. SHA256 checks read the archive before and after loading. HTTP endpoint
authentication uses the existing local credentials file; passwords are not printed.

Staging tables use 256 possible archive-hash-prefix partitions, avoiding a new
partition for every 15-minute file. This is a lossless raw storage layer; a
time/counter-oriented analytical layer will follow measured data access patterns.

## Failures and limits

No source fields are coerced to zero, omitted for blank locations, or excluded for
missing index metadata. Unsupported encodings, malformed CSV structure, unsupported
archive entries or limits stop publication instead of skipping files. The entire
original TAR remains available. Limits are 10,000 archive members, 128 MiB per CSV,
2 GiB expanded CSV content and 1 MiB per field. Each batch is approximately bounded
to 2 MiB / 2,000 rows; one unusually large record may exceed the byte batch target.

The existing ClickHouse profile's memory/thread/time limits remain active. No
service settings are changed. Progress contains only counts; no row values,
source timestamps, filenames or identifiers are printed. Timestamped logs go under
project `logs/`. Detailed error messages in those private logs may contain source
content; review and redact them before sharing.

## Validation scope

Synthetic tests cover missing/blank metadata, empty locations, leading zeros,
high-precision numeric strings, UTC derivation, duplicate archive reruns,
ambiguous partial writes, invisible incomplete attempts and count mismatch refusal.
Database protocol tests simulate ClickHouse; target-server execution verifies the
actual SQL schema and ingestion. The next phase will add bounded analytical
queries and AI access through explicit tools, rather than letting the model
calculate KPIs or execute arbitrary SQL.
