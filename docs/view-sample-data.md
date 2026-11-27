# View loaded data in your SSH terminal

No HTTP connectivity from the laptop to the AI server is needed. The script runs
on the AI server and contacts its local ClickHouse HTTP listener at
`127.0.0.1:18123`, the same endpoint used by ingestion. No native TCP port, browser,
firewall changes or new dependencies are required.

Transfer `scripts/15_view_sample_data.py` to:

```text
/home/sysadmin/ran-ai/app/15_view_sample_data.py
```

Run in the existing SSH terminal from any directory, without sudo:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/15_view_sample_data.py"
```

It prints the committed CSV/row counts, UTC coverage and three sample source rows,
with three named counter values each. Common identifier fields, source filename,
family/version, original timestamps/offsets, raw collection periods, location and
index metadata are visible. Empty strings are shown explicitly as `""`; an absent
index is distinguished from a present index. Decimal strings retain their source
precision. There is no multi-row aggregation or counter interpretation.

For a larger sample:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/15_view_sample_data.py" --rows 5 --counters 5
```

To inspect a specific member ordinal in the loaded archive:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/15_view_sample_data.py" --member 100 --rows 2 --counters 5
```

The member ordinal is the original TAR entry position, not the column index or
cell identifier. Across multiple committed archives the same ordinal may occur
more than once; source filenames distinguish those displayed records. Default
selection is deterministic by archive hash, member ordinal and row ordinal.
Sample size is bounded to 20 rows and 20 counters per row.

Queries are fixed SELECT statements through HTTP GET with explicit read-only mode.
The script reuses local credentials, bypasses HTTP proxies, writes no files and
makes no database changes. Use the committed views, not raw incomplete-attempt
tables. Output includes real lab data: keep it local rather than posting screenshots
containing identifiers, filenames or values. A generic success confirmation is
enough. SQL-engine execution remains a target-server check; local tests verify
query bounds, local/read-only requests and empty-value display.
