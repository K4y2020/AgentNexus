# AgentNexus migration guide

## Automatic schema migrations

AgentNexus runs on an Alembic-managed SQLAlchemy schema. On server startup the
runtime automatically migrates the configured database to the schema head that
the running build knows (`omnigent/db/utils.py`). A fresh database has no
`alembic_version` table and is migrated to head without manual action; an older
database is migrated forward; a database whose revision is newer than the
running build is refused rather than downgraded blindly.

Users do not need to run migrations by hand for normal upgrades. The migration
scripts live under `omnigent/db/migrations/versions/` and are committed with
their upgrade/downgrade functions. Downgrade support exists per-revision for
authoring/testing, but downgrading a production database out-of-band is not a
supported upgrade path; the supported recovery path is a pre-upgrade backup.

## What happens during a desktop upgrade

1. You approve the update after a native consent dialog.
2. AgentNexus stops the owned local server and connected host agents.
3. It snapshots Electron settings plus the local server database, WAL/SHM,
   `config.yaml`, auth references, pid/signature files, and `daemons/`.
4. It writes an upgrade marker mapping the previous version, pending version,
   and backup directory to `<userData>/upgrade-state.json`.
5. The installer runs and the app relaunches.
6. If the pending version boots, the marker clears and the snapshot is kept
   under `<userData>/update-backups/` for manual restore.
7. If the previous version is still running (the update never took effect),
   launch restores the snapshot before the server starts and clears the marker.
8. If restore fails, the marker stays in place so the failure is visible and
   the snapshot is not treated as applied.

## Manual restore after a failed upgrade

Follow [windows-installer.md](windows-installer.md#restore-after-a-failed-upgrade):
stop AgentNexus and the local server, copy `settings.json` back to the Electron
user-data dir, and copy every runtime subfolder from the snapshot back over the
matching local server files. The same operation is available programmatically
through `restoreFromBackup` in `web/electron/src/update_backup.js`.

## Environment overrides

The runtime data, config, and state directories can be redirected with
`OMNIGENT_DATA_DIR` and `OMNIGENT_CONFIG_HOME`; the shared state directory is
always `~/.omnigent`. The desktop updater snapshots exactly the effective
directories and restores exactly the paths recorded in `backup.json`, so
override setups are backed up consistently.

## Database-only recovery

The `chat.db` SQLite file contains the conversations, tunnels/users, and
coordination state. It should be included in normal machine backups. If the
database is lost, AgentNexus recreates it from the current schema but the
prior conversations are gone; restore from a pre-upgrade or external snapshot
rather than treating a fresh DB as a migration.
