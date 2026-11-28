# Multi-server TAR collection through an intermediate server

Version `tar-transfer-v8` pushes available producers without an all-host barrier
and deletes intermediate TARs after verified AI delivery. The AI server separately
deletes its received TARs after confirmed ClickHouse ingestion. The collector never
queries ClickHouse. Existing configuration and schedules require no changes.
Update the intermediate script first, then the AI copy, for retry compatibility
when a batch has already been ingested and cleaned on the AI server.

Deployment:

```text
TAR producers (/log/pm/oss/raw/nr)
             | read-only SSH pulls, every 15 minutes
             v
Intermediate server (RHEL 8.8): source-specific cache and pending queue
             | SSH push of whatever new completed data is available
             v
AI server (sysadmin): checksum-verified batches in landing/server-batches
             | existing archive loader and commit ledger
             v
ClickHouse counter_records / counter_files + archive_sources provenance
```

The AI server never connects to a producer. Source hosts, usernames, AI hostname,
and SSH key access must be configured on the intermediate server before running.
No addresses, credentials, private keys or real data belong in GitHub.

## Same filenames do not overwrite other servers' data

On the intermediate server an archive is stored at:

```text
$HOME/ran-ai-transfer/spool/cache/SOURCE_ID/ARCHIVE_SHA256.tar
```

Each original host gets a unique, permanent source ID. Two hosts producing
`same-name.tar` use different directories. A host later replacing that filename
with new contents gets a different SHA256 filename. Copies are retained until
verified delivery, then intermediate TARs are deleted. AI copies are retained
until successful ingestion, then also deleted. Origin metadata remains available.
The manifest preserves original host, username, original path/filename, size,
mtime and SHA256. Compressed TARs are also supported; the hash filename ends in
`.tar` and the existing loader detects compression from contents.

Exact byte-identical TARs are ingested once globally by the existing commit
ledger, while every source origin is retained in provenance. Repacked or
overlapping archives with different bytes remain separate raw sources. There is
no speculative measurement-level deduplication.

## Prerequisites

- Intermediate server: Python 3.6+ and OpenSSH client. Run under a normal account.
- Producers: Python 3.6+ available through SSH, and read access to the TAR folder.
- AI server: existing Python venv, running ClickHouse and current loader 14.
- The intermediate account must have SSH access to every producer and to the AI
  server as `sysadmin`, using each endpoint's username/password or keys.
- Verify SSH host fingerprints using your normal trusted procedure and populate
  that account's known_hosts before automation. Strict checking remains enabled;
  this script does not bypass verification. Configured passwords are not used to
  answer host-key confirmation prompts.
- Keep producer retention long enough for collection/retries. Finish files using
  a temporary extension followed by rename to `.tar` if possible.

If a producer permits only SFTP and no Python/SSH command execution, this version
needs a transport adaptation before use. Actual host access has not been tested
from this development workstation.

## 1. Install on the intermediate RHEL server

Save `scripts/18_transfer_tar_batches.py` as:

```text
$HOME/ran-ai-transfer/18_transfer_tar_batches.py
```

Create a private template (refuses to overwrite an existing config):

Alternatively, download [tar-transfer.example.json](../config/tar-transfer.example.json),
save it as `$HOME/ran-ai-transfer/config.json` on the intermediate server, and
replace the placeholder values there. The example includes two producers. Keep
real passwords in the intermediate server's private copy, not the GitHub sample.

```bash
python3 "$HOME/ran-ai-transfer/18_transfer_tar_batches.py" init-config
```

Edit `$HOME/ran-ai-transfer/config.json`. An example with two producers:

```json
{
  "timeout_seconds": 1800,
  "ai": {
    "host": "AI_HOST",
    "user": "sysadmin",
    "password": "REPLACE_WITH_AI_PASSWORD",
    "directory": "/home/sysadmin/ran-ai/data/landing/server-batches",
    "python": "/home/sysadmin/ran-ai/venv/bin/python"
  },
  "sources": [
    {
      "id": "nr-server-01",
      "host": "PRODUCER_HOST_1",
      "user": "PRODUCER_USER",
      "password": "REPLACE_WITH_PRODUCER_PASSWORD",
      "directory": "/log/pm/oss/raw/nr",
      "patterns": ["*.tar"],
      "min_age_seconds": 120,
      "max_archive_bytes": 10737418240
    },
    {
      "id": "nr-server-02",
      "host": "PRODUCER_HOST_2",
      "user": "PRODUCER_USER",
      "password": "REPLACE_WITH_PRODUCER_PASSWORD",
      "directory": "/log/pm/oss/raw/nr",
      "patterns": ["*.tar"],
      "min_age_seconds": 120,
      "max_archive_bytes": 10737418240
    }
  ]
}
```

Replace every placeholder. Add every producer; source IDs must be unique and
contain only letters, digits, `_` or `-`, starting with a letter/digit.
Each producer has its own `user` and `password`, even when the values are the same
on all hosts. `ai` has independent authentication and can also use a password.
`user` is the SSH username; there is no global credential inheritance. If your
config exists, edit it directly; `init-config` refuses to overwrite it.

`port` is optional (default 22); `python` is an executable path/name (default
`python3`). For key authentication, omit `password` and optionally set
`identity_file`, or use the normal SSH config/agent. Do not configure both
`password` and `identity_file` on one endpoint. The identity field is a key path,
never key contents. These options apply to producers and `ai`.

### Password authentication

After editing the config on the intermediate server:

```bash
chmod 600 "$HOME/ran-ai-transfer/config.json"
```

The file must be owned by the collector account. The script refuses password
configs accessible to group/others. Passwords are plaintext in this private file;
keep it on the intermediate server, out of GitHub and shared output. Apply JSON
escaping to a double quote or backslash in a password. Spaces, shell metacharacters
and non-ASCII text are supported; empty passwords, NULs and line breaks are not.

The collector uses OpenSSH askpass; no sshpass/Paramiko installation is required.
Only the current endpoint's password is placed in the SSH child's environment.
It is not placed in command arguments, remote requests, manifests, provenance or
helper-file contents. The owner-only launcher is removed after each SSH call.
The parent environment is not modified. This does not encrypt the local config
or prevent local root/equivalent-account access to process credentials.

A detached SSH session lets askpass work both interactively and from cron,
without an X server. The helper answers password prompts only, not host-key,
key-passphrase, MFA/OTP or other prompts. Password changes/expired credentials
are not supported. Remote SSH must permit password authentication. Strict host-key
checking stays enabled. Wrong credentials fail the collection and retain the queue.

Reference: [OpenSSH askpass environment](https://man.openbsd.org/ssh.1#SSH_ASKPASS).

Patterns match filenames case-sensitively in the named directory, not recursively.
Add `*.tar.gz`, `*.tgz`, `*.tar.bz2` or `*.tar.xz` as needed. Young files wait for
the next scan. Default maturity is 120 seconds; files must also have unchanged
size/mtime/inode and a consistent checksum during collection. A size-limit breach
stops that source for review, rather than silently dropping an archive.

The first run collects **only the newest eligible matching TAR per producer**.
Older pre-existing TARs are ignored. Newest is determined by filesystem
modification time, with filename as a deterministic tie-breaker, not by parsing
the filename or CSV timestamps. Files still younger than `min_age_seconds` wait.

After a verified download and saved origin record, a per-source cursor is stored
under the intermediate spool's `cursors/`. Subsequent runs select only the newest
eligible TAR newer than that cursor; unchanged/older files are not hashed or
downloaded. Deleting the newest producer file does not cause a fallback to an
older one. Interrupted downloads do not advance the cursor and can be retried.
Do not delete cursors to resolve connection problems.

Each poll selects at most one TAR per producer. If several eligible files arrive
between polls, only the newest is selected; this is newest-only monitoring, not
historical backfill or an every-period catch-up queue. Source timestamps should
advance normally. Already collected pending files and prepared batches are retained
on upgrade; this change does not erase or silently discard the existing queue.
Existing JSON configuration requires no new settings.

## 2. Prepare the AI server

Save the same `18_transfer_tar_batches.py` in `$HOME/ran-ai/app/`, beside the
existing `14_ingest_vendor_tar.py`. Prepare the inbox as `sysadmin`:

```bash
mkdir -p "$HOME/ran-ai/data/landing/server-batches"
```

No packages, firewall changes, ClickHouse restart or Ollama changes are required.
The configured AI inbox must match the directory above. An alternative is allowed
with `ingest --inbox /absolute/path`, provided it stays under `ran-ai/data/landing`.

## 3. Verify and run once

On the intermediate server:

```bash
python3 "$HOME/ran-ai-transfer/18_transfer_tar_batches.py" plan
python3 "$HOME/ran-ai-transfer/18_transfer_tar_batches.py" run
```

`plan` lists counts after checking sources; it downloads and pushes nothing.
Version `tar-transfer-v8` prints a version banner and safe diagnostic codes on
failure. `CONFIG_JSON` includes the exact line and column in the local config;
`CONFIG_PERMISSIONS`, `SSH_HOST_KEY`, `SSH_AUTH`, `SSH_CONNECTION`, and
`REMOTE_PYTHON` distinguish common setup failures without exposing credentials.
Inspect malformed JSON locally; do not share password-bearing config contents.
It lists filenames/metadata without reading or hashing archive contents.
SHA256 checks are performed by `run` before collecting/pushing content.
SSH operations print elapsed-time status every 15 seconds while waiting, so a
slow connection/directory listing is visible. A heartbeat indicates waiting,
not proof that authentication or transfer has completed. The configured timeout
still applies (default 1800 seconds per SSH operation).
`run` downloads the newest selected content to private `.part` files, verifies size/SHA256, then
publishes local cache files. Existing cached content is verified and reused.

A batch contains whatever not-yet-delivered archives are available. Empty, late,
standby or unavailable producers do not block successful producers. Collection
errors are reported and retried on later scans, while available files are pushed.
`WAITING` means no new archives are currently available to push.

There is no all-producer or collection-timestamp alignment barrier. A retry batch
may include queued files from earlier periods. Its manifest lists only included
sources, and the receiver verifies every included file before publication.
Producer originals are not deleted.

After verified upload of every batch archive, the AI receiver verifies all files
again and publishes an atomic `.ready` marker. Interrupted transfers have no
marker and are ignored by ingestion. Intermediate state is marked delivered only
after the AI receiver acknowledges the complete batch. Failed pushes retry the
same batch ID, rather than making a new partly received batch each time.

On the AI server, ingest complete batches:

```bash
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/18_transfer_tar_batches.py" ingest
```

The command verifies the complete batch before ingesting any TAR, then calls the
existing loader for each archive. Blank/absent index and blank location remain
valid. Existing raw-value preservation and CSV/archive limits remain in force.
After all archives succeed it writes `.ingested`, verifies each archive hash in
the ClickHouse commit ledger on the AI server, and deletes the manifest-listed
TARs. It writes `.cleaned` after cleanup completes. Small batch manifests/markers
remain. No directory-wide wildcard deletion is used.

Previously ingested batches are also cleaned on the next scheduled AI run.
Deletion failures are retried without loading committed rows again. Failed or
incomplete ingestion retains its archives. AI-side commit verification and
deletion run on the AI server; the intermediate collector does not query it.

The ClickHouse publication unit is one archive, not a transaction over the entire
batch. If an archive fails after earlier ones commit, those completed archives
stay visible; retries skip exact-TAR duplicates and resume the batch. Partial
attempts within an archive remain hidden by the existing commit ledger.

## 4. Enable automatic collection and ingestion once

Replace script 18 on **both** servers first:

- Intermediate: `$HOME/ran-ai-transfer/18_transfer_tar_batches.py`
- AI: `/home/sysadmin/ran-ai/app/18_transfer_tar_batches.py`, beside loader 14.

Keep your existing config, credentials, cache and cursors. Both hosts require an
active `crond` and the `crontab` client. The installer checks these prerequisites;
it does not install packages or change system services. Keep the existing
ClickHouse instance running on the AI server.

Run this one-time setup on the intermediate server, as the account owning the
configuration and known_hosts:

```bash
python3 -u "$HOME/ran-ai-transfer/18_transfer_tar_batches.py" install-automation
```

Setup preflights source access without downloading files, then uses configured
AI SSH credentials to install AI ingestion. It enables local collection after
AI setup succeeds:

- Collector: every 15 minutes, at `:00`, `:15`, `:30` and `:45`, all day.
- AI ingester: every minute, all day. It processes only complete checksum-verified
  batches with `.ready`; newly delivered batches normally start at the next minute
  boundary, subject to existing work and processing time.

After setup, no manual `run` or `ingest` is required per batch, and no open SSH
terminal is needed. Cron launches short-lived jobs. The first collection occurs
at the next 15-minute boundary. `min_age_seconds` still applies: files too young
for a scan wait until a later scan. Available producers' files are pushed without
waiting for other producers. Source preflight errors are reported but do not
prevent setup; the schedules continue retrying those hosts.

Same filenames are stored under `BATCH_ID/SOURCE_ID/SHA256.tar` on the AI server,
with original filenames in manifests. Incomplete uploads are never ingested.
Failed batches are retained and retried by later jobs; exact committed TARs are
skipped safely by the existing loader.

Check both schedules from the intermediate server:

```bash
python3 -u "$HOME/ran-ai-transfer/18_transfer_tar_batches.py" automation-status
```

Status verifies installed jobs and active cron, not successful transfers or loads.
For actual results, inspect:

- Intermediate: `$HOME/ran-ai-transfer/spool/logs/scheduled-collector.log`
- AI: `$HOME/ran-ai/logs/scheduled-ingest.log`

Re-running setup updates its managed jobs without duplicates and preserves
unrelated cron jobs. Previous manual jobs invoking this exact script and role are
replaced. Private crontab backups are saved under `run/` before modifications.
If setup fails partway through, resolve the diagnostic and rerun; AI scheduling
may already be installed and waiting for data.

The AI script path is derived from its configured inbox/project directory; set
`ai.ingest_script` to a full path only for a custom layout. The endpoint's
configured `python` is used to run it.

For separate local setup/status/removal, run the corresponding command on its host:

```bash
python3 "$HOME/ran-ai-transfer/18_transfer_tar_batches.py" install-schedule --role collector
python3 "$HOME/ran-ai-transfer/18_transfer_tar_batches.py" schedule-status --role collector
python3 "$HOME/ran-ai-transfer/18_transfer_tar_batches.py" remove-schedule --role collector
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/18_transfer_tar_batches.py" install-schedule --role ingest
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/18_transfer_tar_batches.py" schedule-status --role ingest
"$HOME/ran-ai/venv/bin/python" "$HOME/ran-ai/app/18_transfer_tar_batches.py" remove-schedule --role ingest
```

Removal retains all archives/state/data and does not stop an already running job.
Jobs survive logout and persist across reboots; OS cron and ClickHouse must still
be running. Password auth works unattended; key endpoints instead need their
configured keys/agent available to cron.

Nonblocking locks prevent overlapping collector runs and coordinate AI ingestion
with loader 14's `vendor-ingest.lock`. If a run lasts more than 15 minutes, the
next invocation skips while the lock is held. The schedule is a polling interval,
not a guaranteed end-to-end delivery deadline.

## Provenance, retained files and operations

### Watch collection, delivery and ingestion

On the **intermediate server**, follow the scheduled collection/push log:

```bash
tail -F "$HOME/ran-ai-transfer/spool/logs/scheduled-collector.log"
```

Interpret the messages as follows:

- `Source ...: 1 completed archive(s) verified.`: the selected TAR is available
  in the intermediate cache (downloaded or verified from retained cache).
- `Source ...: 0 completed archive(s) verified.`: no newer eligible TAR this scan.
- `WAITING`: no new archives are available to push; other producers do not block delivery.
- `Pushing batch archive ...`: upload started; this alone is not delivery confirmation.
- `SUCCESS: complete batch pushed`: the AI receiver acknowledged and verified all
  batch files and published the complete-batch marker.
- `DELETED intermediate TAR`: its intermediate cached copy was removed after
  verified delivery. This confirms transfer cleanup, not ClickHouse ingestion.

Show recent TAR arrival and successful push times on the intermediate server:

```bash
ls -lt "$HOME"/ran-ai-transfer/spool/cache/*/*.tar | head
ls -lt "$HOME"/ran-ai-transfer/spool/sent/*.json | head
```

The first lists cached TARs by local file modification time. The second lists
manifests written after confirmed delivery, so their modification times indicate
successful push completion. A sent manifest confirms delivery, not database
ingestion. The folders/files appear after their first successful activity.
An empty cache is normal after successful pushes because intermediate TARs are
now deleted. Use sent manifests and `deletions.jsonl` for transfer/cleanup history.

On the **AI server**, follow automatic ClickHouse ingestion:

```bash
tail -F "$HOME/ran-ai/logs/scheduled-ingest.log"
```

`Ingesting batch archive ...` indicates processing. `Batch ingestion summary`
with a positive `batches` count and `"failures": 0` indicates completed ingestion
during that invocation. `already_loaded` counts exact-TAR duplicates safely
skipped; `rows` counts newly loaded source rows. A zero-batch summary can simply
mean no completed batch is pending. Review failure diagnostics/private logs if
the failure count is positive.

`tail -F` waits/retries if the log has not been created yet. Press **Ctrl+C** to
stop watching; the scheduled jobs continue. With a custom `--spool`, substitute
that directory for the intermediate spool paths above.

`ran_ai.archive_sources_v1` records each source origin. The view
`ran_ai.archive_sources` exposes origins whose archive hash is committed. Join on
`archive_sha256` to relate origins to counter files/rows; keep these host/path
details private. One archive may have multiple origins, so a direct join can
multiply rows. Do not sum measurements after a many-origin join.

Producer originals remain retained. Intermediate cached TARs are deleted after
checksum-verified AI delivery. AI inbox TARs are deleted after confirmed committed
ingestion. The collector does not query ClickHouse or wait for ingestion; the AI
ingester verifies its local database before removing its received archives.

Follow AI-side deletion records:

```bash
tail -F "$HOME/ran-ai/logs/ingest-deletions.jsonl"
```

The audit includes source, original filename, AI archive path, hash, byte size,
batch ID, UTC timestamp and deletion result. `DELETED AI TAR after ClickHouse
commit` also appears in the scheduled ingestion console log. The batch summary
includes `deleted_archives` and `cleanup_failures`; an old batch may show zero
new ingestions but a positive cleanup count.

Only registered `SOURCE_ID/SHA256.tar` files are removed. The pipeline uses TARs,
not ZIP ingestion; unrelated `.zip` files are not deleted. `.ready` records prior
verified receipt, `.ingested` records successful ingestion, and `.cleaned` records
completed AI cleanup. These markers and manifests remain after archive deletion.
Duplicate delivery retries for an already ingested batch are acknowledged safely
without reloading its data or restoring cleaned TARs.

Follow deletion audit records on the intermediate server:

```bash
tail -F "$HOME/ran-ai-transfer/spool/logs/deletions.jsonl"
```

Each record includes UTC time, source ID, original filename/path, cached filename,
SHA256, byte size, batch ID and result. `delete_intent` is fsynced before removal;
`deleted` confirms removal. `delete_failed` is retried on later runs, and
`already_absent` handles a previous cleanup/crash or external removal. Only
registered cache TARs are eligible; pending shared references, changed contents
and symlinks prevent deletion. Archive data never appears in the audit log.

Small origin records, cursors, sent manifests and deletion logs are retained.
On upgrade, previously delivered cached TARs are also eligible for cleanup from
their saved sent manifests/delivery ledger. Interrupted or unconfirmed transfers
retain their TARs for retry. Do not delete those state files to free space.

Private detailed logs live under the intermediate spool's `logs/` and AI
`ran-ai/logs/`. Console output contains source IDs, counts and status, not archive
contents or credentials. Share only those short summaries. Queue/state corruption,
source-size limits and parse failures require local log review; don't delete state
to clear an error because that can repeat delivery or lose origin tracking.
