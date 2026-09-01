# AgentNexus Windows installer, upgrade, and uninstall

## Build outputs

From `web/electron/`:

```bash
pnpm run build:win          # NSIS installer + portable zip
pnpm run build:win:portable # portable zip only
```

Artifacts land in `web/electron/dist/`:

- `AgentNexus-<version>-<arch>-setup.exe` - assisted NSIS installer.
- `AgentNexus-<version>-<arch>-win.zip` - portable Windows build (unpack and
  run `AgentNexus.exe`; user data still lives in the normal per-user app data
  directory).

The NSIS installer is deliberately **assisted**, **per-user**, and lets the
user choose the installation directory. It registers both the branded
`agentnexus://` deep-link scheme and the legacy `omnigent://` scheme so
existing links keep working.

## Upgrade safety

Before an approved desktop update restarts the app, AgentNexus first stops
the local server and connected host agents, then snapshots:

- Electron shell settings (`settings.json` under the app's per-user data dir).
- The local server runtime: `chat.db`, SQLite WAL/SHM files, `config.yaml`,
  `auth_tokens.json`, `local_server.pid`, `local_server.sig`, and the
  `daemons/` registry.

Snapshots are written under `<userData>/update-backups/`
(`%APPDATA%\AgentNexus\update-backups\`) with a `backup.json` manifest. Old
snapshots are pruned to the five most recent. If the backup fails, the update
install is **not** armed, so an upgrade can never silently destroy history.
Stopping the server before the snapshot is deliberate: the DB is quiescent, so
the copied `chat.db*` files are consistent and no live WAL write can race the
backup. The restart dialog also tells the user that connected agents and the
local server are stopped as part of the update.

### Restore after a failed upgrade

1. Stop AgentNexus and the local server.
2. Open `%APPDATA%\AgentNexus\update-backups\<timestamp>-<version>\`.
3. Copy `settings.json` back to `%APPDATA%\AgentNexus\`.
4. Copy every runtime subfolder in the snapshot (by default `data\`; a snapshot
   may also contain `config\`/`state\` if environment overrides split the
   directories) back over the matching files under
   `%USERPROFILE%\.omnigent\` (or the override directories in effect when the
   snapshot was created).
5. Launch AgentNexus. Server/coordination state is rehydrated from sqlite, so
   no in-memory state is needed for recovery.

The same restore behavior is exposed programmatically by
`web/electron/src/update_backup.js` (`restoreFromBackup`), which is covered by
unit tests and rejects malformed manifests.

## Uninstall behavior

Default uninstall keeps all user data:

- `%APPDATA%\AgentNexus` (shell settings, recent servers, update backups).
- `%USERPROFILE%\.omnigent` (conversation DB, config, auth references,
  daemons, logs).

During an assisted uninstall, AgentNexus asks once whether to also remove
local data. Choosing **No** keeps everything. Choosing **Yes** removes both
directories explicitly. Silent or update-driven uninstall runs never prompt
and always keep data, so automation cannot destroy a user's sessions.
