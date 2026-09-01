# Privacy and data handling

## What stays on your machine

- Conversation history, items, labels, policies, and coordination state
  (runs, tasks, messages, outbox, delivery attempts, leases, audit events)
  live in the local server database and artifacts directory.
- CLI/server/host logs stay under the runtime data dir
  (`~/.omnigent/logs` by default, `OMNIGENT_DATA_DIR` to isolate).
- Provider credentials are not stored by the server. API keys come from
  environment variables, YAML provider config, or the CLI's own login
  state (`~/.claude`, `~/.codex`); secret-bearing YAML fields are expanded
  client-side and resolved before reaching the process environment.

## What leaves your machine

Only the model/provider calls an agent actually makes leave the machine:
the prompts, tool results, and files selected for a turn go to the
configured provider endpoint. No analytics-only upload of
conversations is configured by default, and no
model-privacy chain-of-thought or hidden reasoning stored or displayed.

## Redaction and diagnostics

- CLI diagnostics redact secret-shaped substrings (`sk-*`, `dapi*`, bearer
  patterns, etc.) before writing to the always-on diagnostics log.
- `omnigent diagnose` emits a sanitized environment snapshot described as
  safe to paste into an issue; it intentionally excludes secrets.
- Coordination/workspace logs avoid echoing bearer tokens from router
  advertisements; the reason plus the file name is enough to find the
  offending file on disk.

## Operator controls

- `OMNIGENT_DATA_DIR` isolates a full environment (DB, artifacts, logs)
  for staging, testing, or multi-tenant runs on one machine.
- `--database-uri` / `--conversation-database-uri` let you keep data in a
  named SQLite file or a server-side PostgreSQL/MySQL/MariaDB URI.
- Auth is disabled by default locally. When `OMNIGENT_AUTH_ENABLED` is on,
  the server enforces account/session access checks; coordination routes
  additionally require session-tree membership and read vs. manage levels.
- Before sharing logs or a `diagnose` snapshot, re-scan for tokens and
  code snippets; redaction is best-effort defense in depth, not a
  guarantee.

## Backup policy

The user is responsible for backups. The desktop update flow creates a
pre-upgrade backup around managed upgrades, but it is not a substitute
for regular copies of the data dir (see `docs/migration-guide.md`).
