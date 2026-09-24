// Upgrade lifecycle marker for the AgentNexus desktop shell.
//
// A pre-upgrade snapshot alone cannot roll back a failed Windows update. This
// module records which backup belongs to which old->new desktop version, then
// reconciles it at launch:
//
//   - The pending version boots  -> the upgrade landed. The marker is cleared;
//     the backup stays on disk under `<userData>/update-backups` for manual
//     restore.
//   - The previous version boots -> the update never took effect. The snapshot
//     is restored so data matches the running binary, then the marker clears.
//   - Any other version boots    -> keep the marker untouched and never
//     auto-restore; surface the state for manual recovery.
//
// Restore happens before the local server starts, so chat/config/auth files are
// not racing a live server. A missing/unreadable backup aborts the restore and
// keeps the marker instead of destroying data.

"use strict";

const fs = require("fs");
const path = require("path");

const { restoreFromBackup } = require("./update_backup");

/** Marker file name under Electron's per-user data dir. */
const UPGRADE_STATE_FILE = "upgrade-state.json";

function writeJsonAtomic(filePath, value) {
  const tmp = `${filePath}.tmp`;
  fs.writeFileSync(tmp, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  fs.renameSync(tmp, filePath);
}

/**
 * Marshal/validate an upgrade-state record. Anything malformed is null, so
 * unreadable or partial state can never trigger an automatic restore.
 *
 * @param {unknown} parsed Decoded JSON value.
 * @returns {{previousVersion: string, pendingVersion: string, backupDir: string, startedAt: string} | null}
 */
function normalizeState(parsed) {
  if (!parsed || typeof parsed !== "object") return null;
  const record = parsed;
  const previousVersion = record.previousVersion;
  const pendingVersion = record.pendingVersion;
  const backupDir = record.backupDir;
  const startedAt = record.startedAt;
  if (
    typeof previousVersion !== "string" ||
    previousVersion === "" ||
    typeof pendingVersion !== "string" ||
    pendingVersion === "" ||
    typeof backupDir !== "string" ||
    backupDir === "" ||
    typeof startedAt !== "string"
  ) {
    return null;
  }
  return { previousVersion, pendingVersion, backupDir, startedAt };
}

/**
 * Read the current upgrade marker, or null when none exists.
 *
 * @param {{userDataDir: string}} opts
 * @returns {ReturnType<typeof normalizeState>}
 */
function readUpgradeState({ userDataDir }) {
  if (typeof userDataDir !== "string" || userDataDir === "") return null;
  try {
    return normalizeState(
      JSON.parse(fs.readFileSync(path.join(userDataDir, UPGRADE_STATE_FILE), "utf8")),
    );
  } catch {
    return null;
  }
}

/**
 * Write the marker after an approved upgrade has a backup. Rejects on invalid
 * input so the installer cannot be armed without a recoverable record.
 *
 * @param {object} opts
 * @param {string} opts.userDataDir
 * @param {string} opts.previousVersion Running desktop version before upgrade.
 * @param {string} opts.pendingVersion Desktop version about to be installed.
 * @param {string} opts.backupDir Absolute path to the pre-upgrade snapshot.
 * @returns {{previousVersion: string, pendingVersion: string, backupDir: string, startedAt: string}}
 */
function recordUpgradeStart({
  userDataDir,
  previousVersion,
  pendingVersion,
  backupDir,
  now = new Date(),
}) {
  const record = normalizeState({
    previousVersion,
    pendingVersion,
    backupDir,
    startedAt: now.toISOString(),
  });
  if (
    !record ||
    record.previousVersion === record.pendingVersion ||
    typeof userDataDir !== "string" ||
    userDataDir === ""
  ) {
    throw new Error(
      "upgrade-state is incomplete; refusing to arm an update without previousVersion, pendingVersion, and backupDir",
    );
  }
  writeJsonAtomic(path.join(userDataDir, UPGRADE_STATE_FILE), record);
  return record;
}

/**
 * Remove the upgrade marker (after successful boot or completed restore).
 *
 * @param {{userDataDir: string}} opts
 * @returns {boolean} True when a marker was removed.
 */
function clearUpgradeState({ userDataDir }) {
  const file = path.join(userDataDir, UPGRADE_STATE_FILE);
  try {
    fs.unlinkSync(file);
    return true;
  } catch (err) {
    if (err && err.code === "ENOENT") return false;
    throw err;
  }
}

/**
 * Decide what a desktop launch means for a pending upgrade.
 *
 * @param {object} opts
 * @param {string} opts.userDataDir
 * @param {string} opts.currentVersion Desktop version that is now running.
 * @param {typeof restoreFromBackup} [opts.restore] Inject for tests.
 * @returns {{
 *   action: "none" | "booted-new-version" | "restored-previous-version" | "restore-failed" | "version-mismatch",
 *   state?: object|null,
 *   restored?: object,
 *   error?: string,
 * }}
 */
function reconcileUpgradeState({ userDataDir, currentVersion, restore = restoreFromBackup }) {
  const state = readUpgradeState({ userDataDir });
  if (!state) return { action: "none", state: null };
  if (currentVersion === state.pendingVersion) {
    clearUpgradeState({ userDataDir });
    return { action: "booted-new-version", state };
  }
  if (currentVersion === state.previousVersion) {
    try {
      const restored = restore({ backupDir: state.backupDir });
      clearUpgradeState({ userDataDir });
      return { action: "restored-previous-version", restored, state };
    } catch (err) {
      return {
        action: "restore-failed",
        error: String(err?.message ?? err),
        state,
      };
    }
  }
  return { action: "version-mismatch", state };
}

module.exports = {
  UPGRADE_STATE_FILE,
  clearUpgradeState,
  readUpgradeState,
  reconcileUpgradeState,
  recordUpgradeStart,
};
