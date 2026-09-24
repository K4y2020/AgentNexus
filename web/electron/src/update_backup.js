// Pre-upgrade backup for the AgentNexus desktop shell.
//
// electron-updater replaces the app binary in place; the durable state that
// must survive an upgrade lives OUTSIDE the install dir (Electron userData and
// the local server's runtime/config/state dirs). This module snapshots exactly
// those files before an approved upgrade, writes a machine-readable manifest,
// and can copy the snapshot back (the restore path is a deliberate, local,
// explicit step - never automatic).

"use strict";

const fs = require("fs");
const path = require("path");

/** Backups live under `<userData>/update-backups`. */
const BACKUP_DIR_NAME = "update-backups";
/** Oldest snapshots beyond this count are pruned after a successful backup. */
const DEFAULT_KEEP_BACKUPS = 5;
/** The subset of each runtime dir that constitutes durable data + config. */
const RUNTIME_FILE_NAMES = [
  "chat.db",
  "chat.db-shm",
  "chat.db-wal",
  "config.yaml",
  "auth_tokens.json",
  "local_server.pid",
  "local_server.sig",
];

function sanitizeVersion(version) {
  const clean = String(version ?? "unknown")
    .replace(/[^a-zA-Z0-9._-]/g, "_")
    .slice(0, 80);
  return clean || "unknown";
}

function timestampFor(now) {
  return now.toISOString().replace(/[:.]/g, "-");
}

/**
 * Walk userData + the given runtime dirs and return the files that belong in a
 * pre-upgrade snapshot. Missing files are skipped; nothing is guessed.
 *
 * @param {string} userDataDir Electron `app.getPath("userData")`.
 * @param {{name: string, dir: string}[]} runtimeDirs Named local server dirs.
 * @returns {{kind: "shell" | "runtime", name: string, file: string, target: string, src: string}[]}
 */
function collectBackupSources(userDataDir, runtimeDirs) {
  const sources = [];
  const seen = new Set();
  const addSource = (source) => {
    const key = path.resolve(source.src);
    if (seen.has(key)) return;
    seen.add(key);
    sources.push(source);
  };
  const shellSettings = path.join(userDataDir, "settings.json");
  if (fs.existsSync(shellSettings)) {
    addSource({
      kind: "shell",
      name: "shell",
      file: "settings.json",
      target: path.join("shell", "settings.json"),
      src: shellSettings,
    });
  }

  for (const entry of Array.isArray(runtimeDirs) ? runtimeDirs : []) {
    const name = String(entry?.name ?? "runtime");
    const dir = entry?.dir;
    if (!dir) continue;
    for (const file of RUNTIME_FILE_NAMES) {
      const src = path.join(dir, file);
      if (!fs.existsSync(src)) continue;
      addSource({
        kind: "runtime",
        name,
        file,
        target: path.join(name, file),
        src,
      });
    }
    const daemons = path.join(dir, "daemons");
    if (fs.existsSync(daemons)) {
      addSource({
        kind: "runtime",
        name,
        file: "daemons",
        target: path.join(name, "daemons"),
        src: daemons,
      });
    }
  }
  return sources;
}

function writeJsonAtomic(filePath, value) {
  const tmp = `${filePath}.tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  fs.renameSync(tmp, filePath);
}

function pruneBackups(backupRoot, keep) {
  let entries;
  try {
    entries = fs.readdirSync(backupRoot);
  } catch {
    return 0;
  }
  const dirs = entries
    .filter((name) => {
      try {
        return fs.statSync(path.join(backupRoot, name)).isDirectory();
      } catch {
        return false;
      }
    })
    .sort();
  const excess = dirs.length - Math.max(1, keep);
  for (const name of dirs.slice(0, excess)) {
    fs.rmSync(path.join(backupRoot, name), { recursive: true, force: true });
  }
  return Math.max(0, excess);
}

/**
 * Snapshot settings + durable local server state before an approved upgrade.
 *
 * @param {object} opts
 * @param {string} opts.userDataDir
 * @param {{name: string, dir: string}[]} [opts.runtimeDirs]
 * @param {string} [opts.version] App version being upgraded from.
 * @param {Date} [opts.now] Injectable clock for tests.
 * @param {number} [opts.keep] Snapshots to retain after pruning.
 * @returns {{root: string, createdAt: string, appVersion: string, fileCount: number, pruned: number}}
 */
function createPreUpgradeBackup({
  userDataDir,
  runtimeDirs = [],
  version = "unknown",
  now = new Date(),
  keep = DEFAULT_KEEP_BACKUPS,
}) {
  if (typeof userDataDir !== "string" || userDataDir === "") {
    throw new Error("userDataDir is required for a pre-upgrade backup");
  }
  const sources = collectBackupSources(userDataDir, runtimeDirs);
  const root = path.join(
    userDataDir,
    BACKUP_DIR_NAME,
    `${timestampFor(now)}-${sanitizeVersion(version)}`,
  );
  fs.mkdirSync(root, { recursive: true });

  for (const item of sources) {
    const destination = path.join(root, item.target);
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.cpSync(item.src, destination, { recursive: true, force: true, preserveTimestamps: true });
  }

  const metadata = {
    schema_version: 1,
    created_at: now.toISOString(),
    app_version: String(version ?? "unknown"),
    backup_dir: root,
    sources,
  };
  writeJsonAtomic(path.join(root, "backup.json"), metadata);
  const pruned = pruneBackups(path.join(userDataDir, BACKUP_DIR_NAME), keep);
  return {
    root,
    createdAt: metadata.created_at,
    appVersion: metadata.app_version,
    fileCount: sources.length,
    pruned,
  };
}

/**
 * Copy a snapshot back over the live files. Exact, explicit, and fail-closed:
 * unknown members of `backup.json` are skipped, and a missing manifest aborts.
 *
 * @param {object} opts
 * @param {string} opts.backupDir
 * @returns {{restoredCount: number, targets: string[]}}
 */
function restoreFromBackup({ backupDir }) {
  if (typeof backupDir !== "string" || backupDir === "") {
    throw new Error("backupDir is required");
  }
  const metadataPath = path.join(backupDir, "backup.json");
  let metadata;
  try {
    metadata = JSON.parse(fs.readFileSync(metadataPath, "utf8"));
  } catch (err) {
    throw new Error(`pre-upgrade backup manifest missing/unreadable: ${err.message}`, {
      cause: err,
    });
  }
  const sources = Array.isArray(metadata?.sources) ? metadata.sources : [];
  if (sources.length === 0) return { restoredCount: 0, targets: [] };

  const backupRoot = path.resolve(backupDir);
  const targets = [];
  for (const source of sources) {
    if (!source || typeof source.src !== "string" || typeof source.target !== "string") continue;
    const from = path.resolve(backupDir, source.target);
    if (from !== backupRoot && !from.startsWith(`${backupRoot}${path.sep}`)) continue;
    fs.mkdirSync(path.dirname(source.src), { recursive: true });
    fs.cpSync(from, source.src, { recursive: true, force: true, preserveTimestamps: true });
    targets.push(source.target);
  }
  return { restoredCount: targets.length, targets };
}

module.exports = {
  BACKUP_DIR_NAME,
  DEFAULT_KEEP_BACKUPS,
  RUNTIME_FILE_NAMES,
  collectBackupSources,
  createPreUpgradeBackup,
  restoreFromBackup,
};
