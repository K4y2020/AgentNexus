const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const {
  BACKUP_DIR_NAME,
  collectBackupSources,
  createPreUpgradeBackup,
  restoreFromBackup,
} = require("../src/update_backup");

function makeTempDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), `${prefix}-`));
}

describe("update_backup — source collection", () => {
  it("collects shell settings and the durable runtime files that exist", () => {
    const root = makeTempDir("agentnexus-backup-sources");
    const userData = path.join(root, "userData");
    const runtime = path.join(root, "runtime");
    fs.mkdirSync(userData, { recursive: true });
    fs.mkdirSync(runtime, { recursive: true });
    fs.writeFileSync(path.join(userData, "settings.json"), "{}");
    fs.writeFileSync(path.join(runtime, "chat.db"), "database");
    fs.mkdirSync(path.join(runtime, "daemons"));
    fs.writeFileSync(path.join(runtime, "daemons", "target.json"), "{}");

    const sources = collectBackupSources(userData, [{ name: "state", dir: runtime }]);

    assert.deepEqual(
      sources.map((s) => s.target).sort(),
      [
        path.join("shell", "settings.json"),
        path.join("state", "chat.db"),
        path.join("state", "daemons"),
      ].sort(),
    );
    assert.equal(sources.find((s) => s.file === "settings.json").kind, "shell");
    assert.ok(sources.some((s) => s.file === "chat.db" && s.kind === "runtime"));
    assert.ok(sources.some((s) => s.file === "daemons" && s.kind === "runtime"));
  });

  it("ignores missing files instead of inventing sources", () => {
    const root = makeTempDir("agentnexus-backup-missing");
    const userData = path.join(root, "userData");
    fs.mkdirSync(userData, { recursive: true });

    const sources = collectBackupSources(userData, [{ name: "state", dir: path.join(root, "none") }]);

    assert.deepEqual(sources, []);
  });

  it("deduplicates runtime dirs that resolve to the same filesystem path", () => {
    const root = makeTempDir("agentnexus-backup-dedupe");
    const userData = path.join(root, "userData");
    const runtime = path.join(root, "runtime");
    fs.mkdirSync(userData, { recursive: true });
    fs.mkdirSync(runtime, { recursive: true });
    fs.writeFileSync(path.join(runtime, "chat.db"), "database");

    const sources = collectBackupSources(userData, [
      { name: "data", dir: runtime },
      { name: "config", dir: runtime },
      { name: "state", dir: runtime },
    ]);

    assert.deepEqual(
      sources.map((s) => s.target).sort(),
      [path.join("data", "chat.db")].sort(),
    );
    assert.equal(sources.length, 1);
  });
});

describe("update_backup — pre-upgrade snapshot", () => {
  it("writes a manifest plus every selected file and restores it back", () => {
    const root = makeTempDir("agentnexus-backup-roundtrip");
    const userData = path.join(root, "userData");
    const runtime = path.join(root, "runtime");
    const config = path.join(root, "config");
    fs.mkdirSync(userData, { recursive: true });
    fs.mkdirSync(runtime, { recursive: true });
    fs.mkdirSync(config, { recursive: true });
    fs.writeFileSync(path.join(userData, "settings.json"), '{"server_url":"http://localhost:8000/"}');
    fs.writeFileSync(path.join(runtime, "chat.db"), "chat-data-v1");
    fs.writeFileSync(path.join(config, "config.yaml"), "host:\n  host_id: abc123\n");

    const result = createPreUpgradeBackup({
      userDataDir: userData,
      runtimeDirs: [
        { name: "state", dir: runtime },
        { name: "config", dir: config },
      ],
      version: "0.12.0-dev.0",
      now: new Date("2026-09-01T00:00:00.000Z"),
    });

    assert.equal(result.fileCount, 3);
    assert.equal(result.appVersion, "0.12.0-dev.0");
    assert.ok(fs.existsSync(path.join(result.root, "backup.json")));
    assert.equal(
      fs.readFileSync(path.join(result.root, "state", "chat.db"), "utf8"),
      "chat-data-v1",
    );

    fs.writeFileSync(path.join(runtime, "chat.db"), "corrupted-on-upgrade");
    fs.writeFileSync(path.join(userData, "settings.json"), "{}");

    const restored = restoreFromBackup({ backupDir: result.root });
    assert.equal(restored.restoredCount, 3);
    assert.equal(fs.readFileSync(path.join(runtime, "chat.db"), "utf8"), "chat-data-v1");
    assert.equal(
      fs.readFileSync(path.join(userData, "settings.json"), "utf8"),
      '{"server_url":"http://localhost:8000/"}',
    );
  });

  it("prunes to the configured retention count", () => {
    const root = makeTempDir("agentnexus-backup-prune");
    const userData = path.join(root, "userData");
    const runtime = path.join(root, "runtime");
    fs.mkdirSync(userData, { recursive: true });
    fs.mkdirSync(runtime, { recursive: true });
    fs.writeFileSync(path.join(runtime, "chat.db"), "x");
    const backupRoot = path.join(userData, BACKUP_DIR_NAME);
    fs.mkdirSync(backupRoot, { recursive: true });
    for (const name of ["000-old", "001-old", "002-old"]) {
      fs.mkdirSync(path.join(backupRoot, name));
      fs.writeFileSync(path.join(backupRoot, name, "backup.json"), "{}");
    }

    const result = createPreUpgradeBackup({
      userDataDir: userData,
      runtimeDirs: [{ name: "state", dir: runtime }],
      version: "0.12.0",
      now: new Date("2026-09-01T01:00:00.000Z"),
      keep: 3,
    });

    assert.equal(result.pruned, 1);
    assert.equal(fs.existsSync(path.join(backupRoot, "000-old")), false);
    assert.ok(fs.existsSync(result.root));
  });
});
