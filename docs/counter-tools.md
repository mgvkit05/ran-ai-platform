# Controlled counter queries and local AI explanations

The database viewer has been confirmed working. This step connects read-only
counter lookup and bounded source observations to the existing local GPT-OSS 20B
instance. It does not grant the model SQL execution or database credentials.

## Transfer the script

Save `scripts/16_counter_tools.py` on the AI server as:

```text
/home/sysadmin/ran-ai/app/16_counter_tools.py
```

No packages or model downloads are needed. ClickHouse must be running for all
actions; persistent Ollama must also be running for `explain`. Use the following
commands in the existing SSH terminal, without sudo.

## 1. Check the inventory

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/16_counter_tools.py" inventory
```

Shows committed CSV/row counts, exact header-layout count and UTC coverage.

## 2. Search counters

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/16_counter_tools.py" search "throughput"
```

Search is a case-insensitive substring of counter label OR measurement family.
Change the keyword to any known counter-name fragment. Up to ten numbered results
are displayed by default (`--limit 1` through `--limit 50` is supported). If a
result limit is reached, narrow the keyword. Exact family, version, header layout
and counter position define each result, avoiding the assumption that similarly
named counters from different schemas are interchangeable.

Search results are saved privately to `$HOME/ran-ai/run/counter-search.json`.
Selection numbers refer only to the most recent search under this account.
Do not share that file or run competing searches while choosing a result.

## 3. Inspect a selected counter

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/16_counter_tools.py" samples --pick 1
```

Replace `1` with the desired numbered result. This prints five bounded source
observations (UTC, location, element ID and the original counter value). It also
computes exact Decimal comparisons for the sample's minimum, maximum and zero
count, plus empty/nonnumeric counts. Those are sample facts, not whole-series
statistics or KPI aggregation. Original numeric representations are retained;
empty values are not converted to zero.

Optional filters and bounds:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/16_counter_tools.py" samples --pick 1 --rows 10
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/16_counter_tools.py" samples --pick 1 --location ""
```

Rows are bounded to 1-20. The second example selects valid blank locations only;
a populated location can also be passed for exact matching. Without a location
filter, samples may contain multiple entities; no cross-entity KPI is inferred.

## 4. Explain the database evidence locally

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/16_counter_tools.py" explain --pick 1
```

This retrieves the same kind of bounded observations and prints deterministic
facts first. Then local Ollama receives the selected counter/family/version, unit
label, sample facts and observations identified as `[R1]`, `[R2]`, etc. Actual
element names, IDs and location paths are replaced by consistent entity aliases
in the model prompt. Source timestamps and counter values stay local to the
server. Credentials and SQL are not passed to the model.

The model is instructed to explain only those facts, cite observation labels,
distinguish a bounded sample from a full series, and avoid inventing counter
definitions, KPI formulas, trends or root causes. Its response is advisory: verify
it against the displayed deterministic facts. Prompt instructions are not a
guarantee that generated text is correct. No counter dictionary is connected yet.
No observations means no AI call. Oversized evidence requires a smaller `--rows`
selection instead of silent truncation.

The explanation request uses the manager's recorded thread count, CPU inference,
4,096-token context, low reasoning effort, a 768-generated-token ceiling and a
10-minute timeout. It keeps the model resident. Changing from the earlier 2,048
context may cause a one-time model reload; returning to the manager's `ask` with
its smaller context may also reload. Existing memory ceilings remain inherited
from the running Ollama instance. No service configuration is rewritten.

## Data access and verification

All database statements are fixed SELECT templates with typed query parameters,
sent through loopback HTTP GET. No arbitrary SQL input or model-generated SQL is
accepted. Only committed views are read. Ollama uses only loopback port 11435;
database access uses only loopback port 18123. HTTP proxies are bypassed.

No source data, model weights, database rows or credentials are uploaded to GitHub.
Local output contains real data; a generic success confirmation is enough. The
only file written is the private search selection catalog, not question or answer
history. Existing ingestion and services are unchanged.

Local tests cover parameter binding, Decimal precision, selection bounds and
family/version scoping, identifier aliases and local-only model requests.
Actual query execution and AI response quality require the target-server run.
This is the first controlled data-to-model bridge, not unrestricted natural-language
database access, causal analysis or automated KPI formula creation.
