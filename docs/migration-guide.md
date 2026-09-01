# Migration guide

This guide covers moving an existing Omnigent installation to the
AgentNexus control-plane layout. It is written for operators who already
run `omnigent server`/`host` and want to keep sessions, policies, and
config while adopting coordination tables, workspace leases, and the
desktop shell.

## 1. Know where state lives

| State | Default location | How to override |
|---|---|---|
| Global config | `~/.omnigent/config.yaml` | `OMNIGENT_CONFIG_HOME` |
| Project config | `<repo>/.omnigent/config.yaml` | per-project file |
| Runtime data dir | `~/.omnigent` | `OMNIGENT_DATA_DIR` |
| Server DB | `<data-dir>/chat.db` | `--database-uri` |
| Conversation tables | `--database-uri` by default | `--conversation-database-uri` |
| Artifacts | `<data-dir>/artifacts` | `--artifact-location` |
| CLI/server/host logs | `<data-dir>/logs` | via data dir |

AgentNexus coordination tables (runs, tasks, outbox, messages, leases,
merge operations) share the same SQLAlchemy database and Alembic lineage as
conversations/policies; there is no separate side-channel database.

## 2. Back up before migrating

A safe migration needs four things copied together because session IDs
reference rows across them:

```powershell
$data = "$env:USERPROFILE\.omnigent"
Copy-Item "$data\config.yaml"  "$env:USERPROFILE\omnigent-backup\config.yaml"
Copy-Item "$data\chat.db"      "$env:USERPROFILE\omnigent-backup\chat.db"
Copy-Item "$datartifacts"    "$env:USERPROFILE\omnigent-backuprtifacts" -Recurse
Copy-Item "$data\logs"         "$env:USERPROFILE\omnigent-backup\logs" -Recurse
```

Stop the server and host first so the DB is not mid-write. Keep secrets
(provider keys or CLI OAuth configs in `~/.claude`, `~/.codex`) out of any
shared backup archive.

## 3. Update the binary/branding

AgentNexus keeps the `omnigent` package namespace for compatibility, but
product names, package IDs, desktop schemes, and docs use AgentNexus. The
desktop app uses:

- application id `ai.agentnexus.desktop`
- deep-link schemes `agentnexus://` and legacy `omnigent://`
- publish URL `https://github.com/K4y2020/AgentNexus/releases/latest/download/`

No CLI command names changed during branding, so existing agent YAML files
and `omnigent run` invocations continue to work.

## 4. Run migrations

Start the server once after upgrading so Alembic applies pending
migrations to the configured database:

```powershell
omnigent server --database-uri <uri> --conversation-database-uri <uri>
```

If you previously used a `--database-uri` pointing at a non-default
location, pass the same URI on upgrade. `omnigent doctor` runs the same
one-off maintenance checks.

## 5. Switch to isolated test data first

Before migrating production state, reproduce the upgrade against a copy:

```powershell
$env:OMNIGENT_DATA_DIR = "$env:USERPROFILE\omnigent-migrate-test"
omnigent server --database-uri "sqlite:///$env:USERPROFILE\omnigent-migrate-test\chat.db" ^
  --conversation-database-uri "sqlite:///$env:USERPROFILE\omnigent-migrate-test\conv.db"
```

Validate sessions, policies, and at least one coordination run before
switching the real data dir back.

## 6. Validate

Check `GET /health`, `GET /v1/coordination/runs`, and the web UI at the
server root. Run one Plan -> Implement -> Review -> Test workflow through
the reliability integration suite (`scripts/run_control_plane_reliability.ps1`)
against the migrated DB before calling the migration complete.
