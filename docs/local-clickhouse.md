# Start the local ClickHouse database

The ClickHouse 26.9.40.9 executable passed the target server compatibility check.
This step starts an authenticated project-local database and verifies storage
and deterministic KPI arithmetic with synthetic data. It leaves Ollama running.

## Start and verify

Transfer `scripts/10_manage_clickhouse.py` through the office laptop and run from
its directory on the AI server, without sudo:

```bash
"$HOME/ran-ai/venv/bin/python" 10_manage_clickhouse.py start
"$HOME/ran-ai/venv/bin/python" 10_manage_clickhouse.py status
```

The first start creates a local configuration and a random password, starts the
server, creates database `ran_ai`, inserts two synthetic rows into a unique
temporary test table and verifies 193 successes / 200 attempts = 96.50%. It then
drops only that test table. Database `ran_ai` remains available. No real KPI data
is required. ClickHouse remains running when the script exits. Subsequent starts
reuse the configuration/password and do not start a duplicate managed instance.
If an unknown service already occupies port 18123, startup stops without touching it.

The test can be repeated independently:

```bash
"$HOME/ran-ai/venv/bin/python" 10_manage_clickhouse.py test
```

Share the SUCCESS and status output. On failure, share the final error and the
last lines of the server log path printed by the script. Do not share credentials.

## Configuration and limits

- HTTP endpoint: `http://127.0.0.1:18123`; no native TCP listener configured.
- Local account: `ran_ai`; a random password is stored only in project
  `config/clickhouse-server/credentials.json`, mode 0600.
- XML configuration stores password hashes and permits loopback connections only.
  The default account also receives a random, unretained password.
- Data and temporary files: `$HOME/ran-ai/database/clickhouse`.
- Query profile: 8 threads, 8 GiB tracked memory and 60 seconds per query.
- Server: 64 GiB tracked memory, 4 concurrent queries; background merge pool 16
  threads and scheduling pool 16. Background threads are separate from query
  thread settings. These are ClickHouse tracker limits, not a cgroup RSS cap.
- ClickHouse inherits the launching shell's current allowed CPU set. No CPU
  isolation, HugePages, kernel settings or Ollama configuration is changed.
- State: `$HOME/ran-ai/run/clickhouse-managed.json`.
- Timestamped operational logs: `$HOME/ran-ai/logs/10-clickhouse-*.log`.

Only the standalone executable is used; no RPM install, system service, network
download, firewall change or root privileges are required. This is a detached
background process, not a supervised boot service. Host logout policies can still
terminate user processes. Rerun `start` after reboot or if the process exits.

## Stop

```bash
"$HOME/ran-ai/venv/bin/python" 10_manage_clickhouse.py stop
```

The manager verifies PID lifetime, executable and process group before sending
a graceful shutdown signal. It does not automatically force-kill a database that
is slow to stop. Data is retained. Avoid manually deleting database directories.

## Next stage and validation scope

After the server test succeeds, profile one representative ZIP/CSV locally and
design the counter/time/cell schema before loading actual KPI values. The model
does not yet have permission or tools to query this database.

Local tests cover XML validity, loopback binding, hashed credentials and resource
limits; password reuse; synthetic-query validation/cleanup; and refusal to stop an
unknown process. Actual server startup and storage behavior require the target
server run above.

References: [ClickHouse server settings](https://clickhouse.com/docs/operations/server-configuration-parameters/settings)
and [query memory settings](https://clickhouse.com/docs/operations/settings/query-complexity#max_memory_usage).
