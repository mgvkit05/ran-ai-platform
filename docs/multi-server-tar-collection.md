# Multi-server TAR collection through an intermediate server

Deployment:

```text
TAR producers (/log/pm/oss/raw/nr)
             | read-only SSH pulls, every 15 minutes
             v
Intermediate server (RHEL 8.8): source-specific cache and pending queue
             | SSH push after every configured source has new completed data
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
with new contents gets a different SHA256 filename. Both versions are retained.
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

The first run collects **all eligible matching history** in each directory.
Use a suitable source folder/pattern when you want a narrower initial backfill.

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
Version `tar-transfer-v3` prints a version banner and safe diagnostic codes on
failure. `CONFIG_JSON` includes the exact line and column in the local config;
`CONFIG_PERMISSIONS`, `SSH_HOST_KEY`, `SSH_AUTH`, `SSH_CONNECTION`, and
`REMOTE_PYTHON` distinguish common setup failures without exposing credentials.
Inspect malformed JSON locally; do not share password-bearing config contents.
It reads/hashes eligible remote archives and may take time on a large backfill.
`run` downloads new content to private `.part` files, verifies size/SHA256, then
publishes local cache files. Existing cached content is verified and reused.

A new batch waits until there is at least one not-yet-delivered archive origin
from **every configured source**. If a source is empty, late or unavailable, files
from the others remain queued across invocations. A failed collection does not
publish a new partial batch. An already prepared complete batch can retry its
push even if a subsequent collection scan fails.

This is an arrival barrier, not matching by collection timestamp: queued archives
can span several periods. Exact 15-minute interval alignment would require a
separate naming/timestamp policy. Every eligible queued origin is included; no
source file is deleted or removed from its producer.

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
After all archives succeed it writes `.ingested`. Source TARs remain in the inbox.

The ClickHouse publication unit is one archive, not a transaction over the entire
batch. If an archive fails after earlier ones commit, those completed archives
stay visible; retries skip exact-TAR duplicates and resume the batch. Partial
attempts within an archive remain hidden by the existing commit ledger.

## 4. Schedule every 15 minutes

After the manual run succeeds, add this line using `crontab -e` under the
intermediate account. Replace `/home/INTERMEDIATE_USER` with the real absolute
home and use the verified Python path (`command -v python3`):

```cron
*/15 * * * * /usr/bin/python3 /home/INTERMEDIATE_USER/ran-ai-transfer/18_transfer_tar_batches.py run >> /home/INTERMEDIATE_USER/ran-ai-transfer/collection-console.log 2>&1
```

Use the same account that owns the private password config; password authentication
does not need an interactive prompt. For endpoints using keys instead, ensure cron
can access their keys/agent. A login-shell agent is not automatically available in cron.

On the AI server, add a separate normal-user cron entry to ingest received batches:

```cron
*/15 * * * * /home/sysadmin/ran-ai/venv/bin/python /home/sysadmin/ran-ai/app/18_transfer_tar_batches.py ingest >> /home/sysadmin/ran-ai/logs/batch-ingest-console.log 2>&1
```

Ingestion can run before a push finishes; it ignores batches without `.ready`
and picks them up on the next cycle. This can add up to 15 minutes of ingestion
latency after delivery. These entries are instructions, not automatically
installed schedules. Do not add duplicate entries.

Nonblocking locks prevent overlapping collector runs and coordinate AI ingestion
with loader 14's `vendor-ingest.lock`. If a run lasts more than 15 minutes, the
next invocation skips while the lock is held. The schedule is a polling interval,
not a guaranteed end-to-end delivery deadline.

## Provenance, retained files and operations

`ran_ai.archive_sources_v1` records each source origin. The view
`ran_ai.archive_sources` exposes origins whose archive hash is committed. Join on
`archive_sha256` to relate origins to counter files/rows; keep these host/path
details private. One archive may have multiple origins, so a direct join can
multiply rows. Do not sum measurements after a many-origin join.

All producer files, intermediate cache/pending records, sent batch manifests and
AI inbox archives are retained. No retention deletion policy is installed: plan
disk capacity and review storage growth before unattended operation. A later
cleanup policy should require confirmed delivery AND ingestion, plus agreed
retention. SHA256 integrity checks do not replace that retention decision.

Private detailed logs live under the intermediate spool's `logs/` and AI
`ran-ai/logs/`. Console output contains source IDs, counts and status, not archive
contents or credentials. Share only those short summaries. Queue/state corruption,
source-size limits and parse failures require local log review; don't delete state
to clear an error because that can repeat delivery or lose origin tracking.
