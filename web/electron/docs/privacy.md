# AgentNexus privacy note

## Local storage

The desktop shell stores:

- Electron settings, recent servers, window state, update settings, upgrade
  markers, and pre-upgrade snapshots under `%APPDATA%\AgentNexus`
  (macOS: `~/Library/Application Support/AgentNexus`).
- Local server data under `~/.agentnexus` by default: `chat.db` (SQLite WAL),
  `config.yaml`, `auth_tokens.json`, `local_server.pid`, `local_server.sig`,
  `daemons/`, artifacts, crash reports, and logs.

Conversation content is stored in the local database when you run the local
server. It passes through the configured server and the model/harness providers
you connect to, in the same way as any agent conversation.

## What leaves the machine

- Connections to the server you explicitly configure. A remote host can see
  the conversations and tool activity you send to it.
- Provider API calls made by the harnesses you run. Those calls are subject to
  the provider's privacy terms.
- Crash reports, only when you choose to file one. AgentNexus does not upload
  crash reports or telemetry automatically.

## Diagnostics and redaction

CLI diagnostics are always-on files under `<data-dir>/logs/cli/` with rotating,
private (0o600) files. At INFO level they log lifecycle and error context, not
prompt or message content. A redaction filter strips obvious secrets from
formatted output and tracebacks: authorization headers, bearer tokens,
env-style `*_TOKEN` / `*_API_KEY` / password assignments, `sk-*`, and
`dapi*`-style keys.

The crash handler writes `crash-*.md` reports under `<data-dir>/crashes/`.
Filing a bug opens the repository issue template with version/OS/traceback
prefilled; the clipboard carries the full report. Nothing is sent without your
action.

## Pre-upgrade backups

Upgrade snapshots include database, config, auth references, and daemon
registry files. They contain the same sensitive material as `~/.agentnexus`, so
protect the `%APPDATA%\AgentNexus\update-backups` directory like any local
credential store. Snapshots older than the five most recent are pruned.
