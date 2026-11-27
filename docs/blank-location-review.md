# Identify CSV files with blank index and LOCATION

Ingestion is paused while the user explains these source records. The source
data is authoritative: blank metadata is not grounds to discard any files or rows.
No database loading script has been deployed for this step.

Transfer `scripts/13_list_blank_location_files.py` to
`$HOME/ran-ai/app/13_list_blank_location_files.py` on the AI server, then run:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/13_list_blank_location_files.py"
```

This reads the newest existing private Phase 12 validation report and displays
the first five affected CSV member filenames, with row counts. It does not reread
or extract the TAR, display row values, modify any files or write to the database.
Unlike the sanitized validation summary, this deliberately displays filenames
locally so the user can identify and explain the measurement families. Actual
filenames remain off GitHub. Share only family descriptions or nonconfidential
filename portions if appropriate; the entire private report is unnecessary.

Next five:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/13_list_blank_location_files.py" --offset 5 --limit 5
```

All 28, if desired:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/13_list_blank_location_files.py" --limit 28
```
