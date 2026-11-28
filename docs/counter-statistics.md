# Full-data counter statistics

Replace `$HOME/ran-ai/app/16_counter_tools.py` with the updated script. Keep
`counter_specs.py` beside it for AI documentation lookup. No reingestion, index
rebuild, packages or restart is required.

## Verify the server SQL first

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/16_counter_tools.py" test-stats
```

This executes the analytics SQL against literal fixtures in your local ClickHouse,
checking counts, finite numeric conversion, scientific notation, empty values,
ranking and empty results. It creates no tables, writes no data and calls no AI.
Share only its short success/failure output.

## Statistics without the LLM

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/16_counter_tools.py" stats --counter "DataFwdRxThru" --ne-type AUPF
```

Use an exact counter name. If there are several families, software versions or
header layouts, the command prints their choices. Add the correct `--family`,
`--data-version`, and/or `--schema-id` from that output. It never guesses.
Use the correct CSV family name, which must also match the Excel family for a
definition to be retrieved.

The `[S1]` summary scans **all matching committed rows** for that selection:

- Matching, numeric, empty, other and zero observation counts.
- Minimum, maximum and unweighted arithmetic mean of finite numeric values.
- First and last available UTC timestamp and count with unavailable UTC.
- Explicit filter scope and numeric precision.

The default `--analysis stats` prints one maximum and one minimum supporting row,
with raw value, UTC, NE ID and location. To display the highest or lowest N rows:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/16_counter_tools.py" stats --counter "DataFwdRxThru" --ne-type AUPF --analysis highest --rows 10
```

Use `--analysis lowest` for the reverse. `--rows` accepts 1-20 and limits only
supporting rows; it never limits the aggregate scan. Equal numeric values are
ordered by UTC, archive, member and row; these are bounded ranked observations,
not an enumeration of every tied observation.

## Filters

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/16_counter_tools.py" stats --counter "DataFwdRxThru" --ne-type AUPF --ne-id "AUPF_104900110" --from "2026-11-26T20:00:00Z" --to "2026-11-26T21:00:00Z"
```

`--ne-id` and `--location` are exact string filters. `--location ""` selects valid
blank locations. `--from` is inclusive and `--to` exclusive. ISO timestamps may
include offsets; timestamps without an offset use UTC, not the SSH shell's local
timezone. A row without UTC is included when there is no time filter, and cannot
match a time-filtered interval. The same filters apply to statistics and ranked
supporting rows. Filters do not remove family/version/schema ambiguities: use
the explicit selectors printed by the command.

## Ask the LLM to explain full-data results

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/16_counter_tools.py" ask --counter "DataFwdRxThru" --ne-type AUPF "Show the highest value and its location."
```

Add the necessary exact family selector. Ask defaults to `--analysis auto`:
highest/maximum/max/top selects ranked highest; lowest/minimum/min/bottom selects
ranked lowest; average/mean/statistics/count/observations selects statistics.
Other wording uses the existing bounded sample workflow. Override the mode with
`--analysis stats`, `highest`, `lowest`, or `sample` when needed. This is keyword
routing into fixed SQL templates, not model-generated SQL.

All filters above also work with `ask`, including sample mode. Time, NE and
location filters must be passed as flags; the script does not parse those from
question text. The model cites `[S1]` for full filtered statistics, `[R1]` etc.
for supporting observations, and `[D1]` for the matching Excel definition.

## Interpretation and scope

Source values remain unchanged strings. Numeric analytics convert to Float64:
minima, maxima, rankings and means are approximate, particularly for very large
integers or high-precision decimals. Original raw values are printed in supporting
rows. Empty, unconvertible, NaN and infinite values are counted separately and
excluded from numeric statistics; this classification does not label source
records erroneous. With no finite numeric observations, extrema and mean are
null and no ranked rows are reported. With no matching observations, no AI answer
is generated.

The mean is over observations across selected entities and timestamps, not a
time-weighted or traffic-weighted KPI. No sums, counter deltas, rate formulas,
time-bucket comparisons or causal diagnosis are inferred. Multiple query
statements use the committed views; avoid concurrent ingestion if you need the
summary and ranked rows to reflect exactly the same committed set.

The local server's existing query resource/time limits remain in force. Source
data and indexes are never modified by these commands. `search`, `samples`,
`explain` and `inventory` remain supported.

SQL reference: [ClickHouse aggregate combinators](https://clickhouse.com/docs/reference/functions/aggregate-functions/combinators)
and [type conversions](https://clickhouse.com/docs/reference/functions/regular-functions/type-conversion-functions).
