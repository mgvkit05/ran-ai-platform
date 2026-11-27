# Controlled counter queries and local AI explanations

## Current diagnostic: explanation timed out before receiving output

The server log shows model warm-up followed by client disconnection and canceled
loading. A minimal question through the previously working manager also stalls,
so this is not isolated to the database explanation prompt. The cause of the
slow/stalled runtime is not yet established; a loaded-model listing alone does
not prove the runner is ready to generate.

Transfer `scripts/17_diagnose_ollama.py` to `$HOME/ran-ai/app/`, then run in a
second SSH session while the stalled question is pending:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/17_diagnose_ollama.py"
```

The read-only diagnostic reports runtime CPU/memory/affinity, thread states,
major page-fault deltas, local API responses and fixed labels for recent loading
events. It writes no files, submits no generation request and makes no changes.
It also includes same-user processes from the installed runtime directory, which
can reveal older workers outside the current manager's descendant/group tree.
Run it before a further restart, capture the output, and then the waiting client
can be interrupted. Do not infer a CPU or memory bottleneck solely from process
state letters; interpret utilization, faults and available memory together.

### Previous log capture

Run these two lines in the AI server's SSH terminal, from any directory:

```bash
log=$(ls -t "$HOME"/ran-ai/logs/07-ollama-*.log | head -n 1)
tail -n 40 "$log"
```

This reads the last 40 lines of the newest persistent Ollama log. Share the
relevant runner/loading/memory/error/cancellation lines after reviewing them for
private information. Do not restart or retry generation before capturing these
diagnostics. No service or database changes are made.

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
the tested 2,048-token context, low reasoning effort and a 192-generated-token
ceiling. Compact evidence and an at-most-60-word answer instruction reduce prompt
and generation work. `streaming-v2` prints the answer as it arrives and a heartbeat
every 10 seconds while waiting/loading/reasoning. Private reasoning text is not
printed. Completion reports load, prompt and generation durations and token counts.
The socket idle timeout is 60 seconds; total elapsed time is checked against 120
seconds whenever a stream event arrives. This is not a hard wall-clock deadline.
The model remains resident and inherited memory ceilings are unchanged.

If an earlier explanation is stalled, interrupt that client with Ctrl+C. An older
request can still occupy the single model slot: use the existing Ollama manager's
`stop`, then `start`, to clear it before retrying. Replace only this tool script;
there is no database reload. A RUNNING/loaded status confirms availability, not
that generation is making progress. The new timings diagnose the bottleneck.

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
