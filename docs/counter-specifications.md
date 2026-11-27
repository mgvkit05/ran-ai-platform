# Local Excel counter specifications

Keep the four original `.xlsx` workbooks in
`$HOME/ran-ai/knowledge/counter-spec/`. Each must contain a `counter` worksheet
(case insensitive). The importer locates the heading row containing `Type Name`,
`Type Description`, and `NE Type`, below any grouped headings.

All named columns are preserved as text, including definitions, triggers, units,
periods, ranges and calculation formulas. Excel formulas are retained as formula
text and never executed. Ordinary blanks remain blank; only explicit merged
cells inherit their anchor value. Index-only and family-only rows do not become
counter definitions. Original workbooks are never edited or uploaded.

## Install and index

Transfer `scripts/counter_specs.py` and the updated `scripts/16_counter_tools.py`
from GitHub to `$HOME/ran-ai/app/` on the server. No additional Python packages,
model training or service restart is required. Run from any directory without sudo:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/counter_specs.py" build
```

All `.xlsx` workbooks except Excel `~$` lock files are read. A private
`knowledge/counter-spec/index.json` is published atomically after all succeed.
A failed build retains the previous index and original documents.
NE type comes from the row's `NE Type` cell. If blank, the filename must identify
one NE type, such as `ACPF-counter-spec.xlsx`. Supported types are ADPF, uADPF,
ACPF and AUPF. Ambiguous or unsupported populated NE cells stop import for review.

Share only the success summary, including workbook and definition counts.
Keep the index, documents and lookup output private on the server.

On failure, version `counter-specs-v3` prints the running script path and hash,
workbook number and compact function/line locations. Share that short diagnostic
output. No worksheet values or local variables are included in the location list.
If the version banner is absent, the server is running an older copy.

## Read a definition without the model

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/counter_specs.py" lookup "ConnEstabAtt" --ne-type ACPF
```

Use an actual `Type Name`. This prints all exact matches for the NE type,
including all columns and workbook, worksheet, row and SHA256 provenance.
It does not query ClickHouse or call Ollama.

## Explain documentation and database observations

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/16_counter_tools.py" search "ConnEstabAtt"
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/16_counter_tools.py" explain --pick 1 --rows 5
```

Choose the appropriate search number. Running `samples` first is optional.
Counter name (with a trailing CSV unit suffix removed if present), family and NE
type must identify exactly one definition. Family must match `Family Name` or
`Family Display Name`, ignoring case and whitespace. NE IDs can have prefixes
or suffixes such as `ACPF_6` or `6_ACPF`. uADPF and ADPF remain distinct.
Samples with multiple or unknown NE types receive no documentation definition.

The answer cites `[D1]` for the definition and `[R1]` etc. for observations.
The source location is printed before generation. Document release compatibility
is not automatically verified: a matching name does not prove a document applies
to the CSV software version.

Missing, ambiguous or outdated definitions are reported; the model receives
sample facts only. Oversized definitions are excluded from model context with
an explicit message. `lookup` still displays them in full; the index never
silently truncates fields. Rebuild after adding, removing or updating workbooks.
The matched source file's hash is checked before use.

The prebuilt local index requires no embedding model or vector database.
Excel parsing happens at build time. Generic `07_manage_ollama.py ask` does not
use this index.
