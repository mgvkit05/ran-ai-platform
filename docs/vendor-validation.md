# Next step: full-row vendor TAR validation

Source profiling found 838 CSV members and 297 candidate schemas, without profile
errors or exhaustion of the sampling budget. Screenshots established four metadata
records: `#family`, `#version`, `#index`, then `#attribute` containing CSV headers.
This full-row validation replaces the profiler's provisional header/role guesses.
It does not load data yet.

## 1. Transfer one script

Save `scripts/12_validate_vendor_tar.py` from GitHub and transfer it to:

```text
/home/sysadmin/ran-ai/app/12_validate_vendor_tar.py
```

Leave the completed TAR sample in `$HOME/ran-ai/data/landing`; do not extract or
rename it. The script selects the newest supported TAR there. Ollama and
ClickHouse can stay running.

## 2. Run on the AI server

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/12_validate_vendor_tar.py"
```

An explicit filename relative to landing can be supplied after the script path.
Only Python's standard library is used; no downloads, sudo or dependency changes.

## 3. Share the short result

The summary is fewer than 15 lines irrespective of the schema count. Share the
whole terminal summary. It reports CSV count, family/version/header-combination
count, fully checked row count, structural issues, numeric/empty counter-field
counts and collection-period integers. No filenames, actual headers, source
timestamps, identifier values or counter values are printed.

Timestamped summary reports and private JSON reports go under project `reports/`;
a sanitized execution log goes under `logs/`. Keep the private JSON on the lab
server: it contains family/version/index strings, filenames and header definitions,
but no data row values. Generated files have private permissions.

## Checks and interpretation

- Requires family/version/index metadata and an explicit attribute header.
- Checks the leading columns `NE_ID`, `SYSTEM_ID`, `NE_NAME`, `INIT_TIME`,
  `TIME_OFFSET`, `GRAN_PERIOD`, `LOCATION`. Underscore variants such as `NENAME`
  are recognized; exact original headers are retained locally.
- Treats identifiers as strings. Validates calendar timestamp and signed UTC
  offset together, without relying on the server's local timezone.
- Checks that collection-period fields are positive integers. Their units are
  not inferred or converted yet.
- Checks every data row's width, the presence of common values, and finite decimal
  or scientific numeric representations after the seven common fields.
- Empty counter fields are counted separately and never treated as zero.
  Nonnumeric or nonfinite values require review rather than silent coercion.
- Header-only files can pass structural checks; no fabricated rows are generated.
- Reads CSV or `.csv.gz` members from TAR/TGZ/TAR.GZ/TAR.BZ2/TAR.XZ. No extraction,
  SQLite update, ClickHouse insert or source-file modification is performed.
- Limits: 10,000 TAR members, 128 MiB per uncompressed CSV, 2 GiB combined CSV
  bytes, 1 MiB per CSV field. Links/unsafe paths are skipped and trigger review.

`STRUCTURAL VALIDATION: PASS` is a format check, not proof of correct counter
semantics or data completeness. If the result says REVIEW REQUIRED, share the
issue counts; do not change data or force a database load.

## After review

Confirm the collection-period unit and timestamp convention, then implement
schema registry and TAR ingestion with archive/row identity and explicit failure
handling. Preserve family/version, object location, original counter names and
units. Deterministic database queries will calculate KPIs; the model will explain
query results through controlled tools. Counter semantics and aggregation rules
must come from the counter dictionary rather than numeric appearance alone.
