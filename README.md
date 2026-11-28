# RAN AI Platform

## Multi-server TAR collection through an intermediate server

`scripts/18_transfer_tar_batches.py` collects on the RHEL intermediate server,
waits for new archives from every configured producer, and pushes verified batches
to the AI server for the existing ClickHouse loader. Collection starts with only
the newest eligible TAR per producer and remembers its position so older history
is never backfilled. Source-specific hash paths
prevent collisions when every server uses the same TAR filename.
Each producer can now have its own `user` and `password` in the intermediate
server's private `config.json` (mode 600); key authentication remains optional.
See [configuration, one-time verification and 15-minute cron setup](docs/multi-server-tar-collection.md).

## Full-data statistics and filters

Update `$HOME/ran-ai/app/16_counter_tools.py`, verify the server SQL, and calculate
statistics without invoking AI:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/16_counter_tools.py" test-stats
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/16_counter_tools.py" stats --counter "DataFwdRxThru" --ne-type AUPF
```

Add exact family/version selectors if choices are listed. `ask` can now explain
full-data statistics and ranked observations. NE ID, location and UTC interval
filters are supported. See [commands, examples and numeric semantics](docs/counter-statistics.md).

## Ask by counter name

Update `scripts/16_counter_tools.py` and `scripts/counter_specs.py` in
`$HOME/ran-ai/app/`, then ask without a prior search or `--pick`:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/16_counter_tools.py" ask --counter "AirMac" --ne-type ADPF "What does this counter measure?"
```

Use the exact counter name. The command retrieves matching local documentation
and NE-filtered sample data, or lists ambiguous matches for you to narrow.
See [direct counter questions and available filters](docs/counter-tools.md).

## Excel counter specifications

Transfer `scripts/counter_specs.py` and the updated `scripts/16_counter_tools.py`
to `$HOME/ran-ai/app/`. Keep private `.xlsx` documents in
`$HOME/ran-ai/knowledge/counter-spec/`, then run:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/counter_specs.py" build
```

Counter explanations can then include exact documentation matches. See
[setup, definition lookup and examples](docs/counter-specifications.md).

Self-hosted 5G KPI analytics with local AI inference. The current deployment
priority is connecting controlled counter queries to local AI explanations.

## Current client update: streamed general questions

The recovery profile loaded in 11.92 seconds, returned a minimal answer in 3.83
seconds, and completed a counter explanation in 14.14 seconds. Keep the working
8-thread / 32-batch / disabled-flash profile. General `ask` was still buffering
responses; the updated manager now streams progress with a shorter default budget.
Replace `scripts/07_manage_ollama.py` under `$HOME/ran-ai/app/`, then run:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/07_manage_ollama.py" ask "What does this counter measure?"
```

Expect `ask-streaming-v3`. No restart is needed for this client update. Generic
questions do not retrieve data or verified vendor definitions. See the
[persistent instance instructions](docs/persistent-ollama.md).

## Current recovery test: smaller CPU startup profile

The last diagnostic showed only an idle API server after canceled loading, with
no model listed. The cause of the warm-up stall is not yet confirmed. Transfer
the updated `scripts/07_manage_ollama.py` and `scripts/16_counter_tools.py` to
`$HOME/ran-ai/app/`, interrupt the waiting question, then run:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/07_manage_ollama.py" stop
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/07_manage_ollama.py" start --threads 8 --batch-size 32 --disable-flash
```

Only after SUCCESS, test a minimal question. If warm-up remains pending, collect
diagnostic 17 from a second SSH session while the runner is active. See the
[recovery test instructions](docs/ollama-recovery.md) for commands and reversal.

## Current diagnostic: inspect Ollama after explanation timeout

The previously working manager's minimal question also stalls. Transfer
`scripts/17_diagnose_ollama.py` to `$HOME/ran-ai/app/`. While the stalled question
is still pending, run in a **second SSH session**:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/17_diagnose_ollama.py"
```

It samples the managed runtime's CPU, memory and process/thread state for three
seconds and reports fixed loading-event labels. No model requests, restarts,
signals or settings changes occur. Share this short diagnostic output.

### Previous log capture command

The database query succeeds, but the explanation received no model output before
its timeout. Run on the AI server in SSH:

```bash
log=$(ls -t "$HOME"/ran-ai/logs/07-ollama-*.log | head -n 1)
tail -n 40 "$log"
```

Capture relevant runner/loading/error lines before restarting or retrying.
This is read-only. Review the log for private information before sharing.

## Current commands: counter search and AI explanation

The sample viewer now works. Transfer `scripts/16_counter_tools.py` to
`$HOME/ran-ai/app/`, then run in SSH:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/16_counter_tools.py" inventory
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/16_counter_tools.py" search "throughput"
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/16_counter_tools.py" samples --pick 1
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/16_counter_tools.py" explain --pick 1
```

Choose a numbered search result before requesting samples/explanation. Fixed,
parameterized read-only queries retrieve bounded observations; GPT-OSS explains
those observations without SQL access. Keep real output local. See
[counter-tool instructions and limits](docs/counter-tools.md).
Search lists all matching items by default. Append `--limit 10` only when you want
a shorter list; transfer the updated `scripts/16_counter_tools.py` to the server's
`$HOME/ran-ai/app/16_counter_tools.py` to use this behavior.

## Completed step: view sample records locally

The first archive loaded successfully: 838 CSVs, 273,853 rows, 3,520,845 counter
fields, including all 187 blank locations. A rerun confirmed archive deduplication.
Transfer `scripts/15_view_sample_data.py` to `$HOME/ran-ai/app/`, then run in SSH:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/15_view_sample_data.py"
```

It queries ClickHouse locally and prints three source records with counter names
and values. No laptop HTTP access or database changes are needed. Output contains
real data; keep it on the lab systems. See [sample viewing instructions](docs/view-sample-data.md).

## Completed step: ingest the complete TAR

The source owner confirmed missing/blank index and LOCATION are valid data.
Transfer `scripts/14_ingest_vendor_tar.py` to `$HOME/ran-ai/app/`, then run:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/14_ingest_vendor_tar.py"
```

ClickHouse must be running. The loader includes all CSVs/rows, stores original
strings and metadata, verifies counts, and publishes only complete loads. Rerun
after success to verify byte-identical archive deduplication. See the
[complete ingestion instructions](docs/tar-ingestion.md). Share only its count summary.

## Completed review: identify blank-index/location CSVs

The user confirmed all source data must be preserved and these fields may be
blank or absent. To inspect the original diagnostic list if needed, transfer
`scripts/13_list_blank_location_files.py` to `$HOME/ran-ai/app/`, then run:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/13_list_blank_location_files.py"
```

It lists the first five affected filenames from the existing private report,
without row values. See [file identification instructions](docs/blank-location-review.md).

## Completed diagnostic: validate the vendor TAR format

The sample contains 838 CSVs with 297 candidate schemas. The first four records
define family, version, index and attribute headers. Transfer
`scripts/12_validate_vendor_tar.py` to `$HOME/ran-ai/app/12_validate_vendor_tar.py`,
then run on the AI server:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/12_validate_vendor_tar.py"
```

This reads every row and prints a short summary; no database load or extraction
occurs. Share the summary and retain the private report locally. See the
[next-step instructions](docs/vendor-validation.md).

## Completed step: profile the TAR sample

ClickHouse startup and synthetic insert/query verification succeeded. The user
uploaded a TAR collection, correcting the earlier ZIP assumption. Transfer
`scripts/11_profile_archive.py` to `$HOME/ran-ai/app/11_profile_archive.py` on the AI
server, then run from any directory:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/11_profile_archive.py"
```

The newest archive in `$HOME/ran-ai/data/landing` is sampled without extraction.
Share the terminal summary, which omits filenames, headers and row values.
Keep the separate private header-mapping report on the lab server. See
[archive profiling instructions](docs/archive-profiling.md).

## Completed step: start and verify ClickHouse

ClickHouse 26.9.40.9 installation succeeded. Transfer `scripts/10_manage_clickhouse.py`
to the AI server, then run from its directory, without sudo:

```bash
"$HOME/ran-ai/venv/bin/python" 10_manage_clickhouse.py start
"$HOME/ran-ai/venv/bin/python" 10_manage_clickhouse.py status
```

This starts an authenticated localhost database, verifies a synthetic table/query,
and leaves ClickHouse running. Existing Ollama is unchanged. See the
[local database instructions](docs/local-clickhouse.md) for limits, logs and stop commands.

## Completed step: install ClickHouse offline

The ClickHouse bundle has been uploaded to the AI server. Run this command
on the **Linux AI server**, without sudo:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/software/ran-ai-clickhouse-bundle/09_install_clickhouse_offline.py"
```

Share the final version and SUCCESS or FAILED output. This verifies and installs
the executable without starting the database or interrupting Ollama.

### Download and transfer reference

The user confirmed that persistent Ollama and interactive Backspace editing work.
Install the analytical database next using the [offline ClickHouse instructions](docs/offline-clickhouse-install.md).
Download `scripts/08_download_clickhouse_bundle.ps1` and
`scripts/09_install_clickhouse_offline.py` together on the office laptop, then run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\08_download_clickhouse_bundle.ps1 -AllowOfflineRevocationCheck
```

Transfer the complete generated bundle and run the installation command in the
guide. This phase installs the executable without starting a database server.

## Completed step: persistent local Ollama

The first GPT-OSS 20B inference test succeeded using 32 threads at 14.32 tokens/s.
Transfer `scripts/07_manage_ollama.py` to the AI server, then run from its directory:

```bash
"$HOME/ran-ai/venv/bin/python" 07_manage_ollama.py start
"$HOME/ran-ai/venv/bin/python" 07_manage_ollama.py status
"$HOME/ran-ai/venv/bin/python" 07_manage_ollama.py ask
```

Ollama stays running locally and the model stays loaded between questions. No
sudo or internet is required. Run the same script with `stop` to release it.
This is a background process, not a boot service; run `start` again after reboot.
See [persistent instance instructions](docs/persistent-ollama.md).

## Completed step: first inference test

Ollama 0.40.18 and GPT-OSS 20B installation succeeded on November 26, 2026.
The user confirmed that the server is dedicated to AI and all CPUs may be used.
Earlier workload-sharing restrictions no longer constrain CPU selection for this
test. Existing kernel isolation, HugePages and system configuration remain unchanged.

Transfer `scripts/06_test_ai_inference.py` to the AI server and, from its directory,
run without sudo:

```bash
"$HOME/ran-ai/venv/bin/python" 06_test_ai_inference.py
```

The script uses the available physical-core count for inference threads (expected
56 on this server), makes one local CPU inference request, prints the response
and speed, and stops its temporary Ollama server. Share the final output.
See the [inference test instructions](docs/offline-ai-install.md#4-first-inference-test-on-the-dedicated-ai-server).

## Completed step: AI server installation

After the entire `ran-ai-offline-bundle` folder has finished transferring to
`$HOME/ran-ai/software/ran-ai-offline-bundle`, run on the **Linux AI server**,
without sudo:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/software/ran-ai-offline-bundle/05_install_ai_offline.py"
```

This verifies the transferred files and installs both Ollama and GPT-OSS 20B.
The model is already in the bundle; no further download is needed. Installation
does not start inference or change existing lab services. Share the final version
and SUCCESS or FAILED output before proceeding to a resource-limited inference test.

See [offline AI installation instructions](docs/offline-ai-install.md) for the
Windows download command, required folder layout, disk space and installation details.

Keep model files, credentials, proprietary KPI files and confidential documents
on the lab systems and outside GitHub.
