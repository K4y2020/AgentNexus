const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const {
  UPGRADE_STATE_FILE,
  readUpgradeState,
  recordUpgradeStart,
  reconcileUpgradeState,
} = require("../src/upgrade_guard");

function makeTempDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), `${prefix}-`));
}

function makeBackup(root) {
  const backupDir = path.join(root, "backup");
  fs.mkdirSync(backupDir, { recursive: true });
  fs.writeFileSync(path.join(backupDir, "backup.json"), JSON.stringify({ sources: [] }));
  return backupDir;
}

describe("upgrade_guard", () => {
  it("records and reads an upgrade marker", () => {
    const root = makeTempDir("agentnexus-upgrade-record");
    const backupDir = makeBackup(root);

    const record = recordUpgradeStart({
      userDataDir: root,
      previousVersion: "0.11.0",
      pendingVersion: "0.12.0",
      backupDir,
      now: new Date("2026-09-01T00:00:00.000Z"),
    });

    assert.equal(record.previousVersion, "0.11.0");
    assert.equal(record.pendingVersion, "0.12.0");
    assert.deepEqual(readUpgradeState({ userDataDir: root }), record);
    assert.ok(fs.existsSync(path.join(root, UPGRADE_STATE_FILE)));
  });

  it("refuses a marker whose pending version is the same as the previous version", () => {
    const root = makeTempDir("agentnexus-upgrade-same-version");
    const backupDir = makeBackup(root);

    assert.throws(
      () =>
        recordUpgradeStart({
          userDataDir: root,
          previousVersion: "0.12.0",
          pendingVersion: "0.12.0",
          backupDir,
        }),
      /incomplete/,
    );
  });

  it("clears the marker when the pending version boots", () => {
    const root = makeTempDir("agentnexus-upgrade-booted");
    const backupDir = makeBackup(root);
    recordUpgradeStart({
      userDataDir: root,
      previousVersion: "0.11.0",
      pendingVersion: "0.12.0",
      backupDir,
    });

    const result = reconcileUpgradeState({
      userDataDir: root,
      currentVersion: "0.12.0",
      restore: () => {
        throw new Error("must not restore when the new version boots");
      },
    });

    assert.equal(result.action, "booted-new-version");
    assert.equal(result.state.pendingVersion, "0.12.0");
    assert.equal(readUpgradeState({ userDataDir: root }), null);
  });

  it("restores the snapshot when the update never took effect", () => {
    const root = makeTempDir("agentnexus-upgrade-aborted");
    const backupDir = makeBackup(root);
    recordUpgradeStart({
      userDataDir: root,
      previousVersion: "0.11.0",
      pendingVersion: "0.12.0",
      backupDir,
    });
    let restored = null;

    const result = reconcileUpgradeState({
      userDataDir: root,
      currentVersion: "0.11.0",
      restore: ({ backupDir: from }) => {
        restored = from;
        return { restoredCount: 1, targets: ["data/chat.db"] };
      },
    });

    assert.equal(result.action, "restored-previous-version");
    assert.equal(restored, backupDir);
    assert.equal(result.restored.restoredCount, 1);
    assert.equal(readUpgradeState({ userDataDir: root }), null);
  });

  it("keeps the marker and reports a failed restore instead of dropping it", () => {
    const root = makeTempDir("agentnexus-upgrade-restore-fail");
    const backupDir = makeBackup(root);
    recordUpgradeStart({
      userDataDir: root,
      previousVersion: "0.11.0",
      pendingVersion: "0.12.0",
      backupDir,
    });

    const result = reconcileUpgradeState({
      userDataDir: root,
      currentVersion: "0.11.0",
      restore: () => {
        throw new Error("disk not found");
      },
    });

    assert.equal(result.action, "restore-failed");
    assert.match(result.error, /disk not found/);
    assert.ok(readUpgradeState({ userDataDir: root }));
  });

  it("never auto-restores when the running version matches neither endpoint", () => {
    const root = makeTempDir("agentnexus-upgrade-mismatch");
    const backupDir = makeBackup(root);
    recordUpgradeStart({
      userDataDir: root,
      previousVersion: "0.11.0",
      pendingVersion: "0.12.0",
      backupDir,
    });

    const result = reconcileUpgradeState({
      userDataDir: root,
      currentVersion: "0.13.0",
      restore: () => {
        throw new Error("must not auto-restore an unknown pairing");
      },
    });

    assert.equal(result.action, "version-mismatch");
    assert.ok(readUpgradeState({ userDataDir: root }));
  });
});
