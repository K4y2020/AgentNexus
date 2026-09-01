const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const {
  createCrashReporter,
  DIAGNOSTICS_DIR,
  MAX_DIAGNOSTIC_FILES,
  redact,
  sanitizeUrl,
  sanitizeHost,
  normalizeManifest,
} = require("../src/crash_reporter");

function makeTempDir(prefix) {
  return fs.mkdtempSync(path.join(os.tmpdir(), `${prefix}-`));
}

function makeApp() {
  return {
    getName: () => "AgentNexus",
    getVersion: () => "0.12.0",
    getPath: () => ".",
    on: () => undefined,
  };
}

function webContentsStub() {
  const handlers = {};
  return {
    on(event, handler) {
      handlers[event] = handler;
    },
    emit(event, ...args) {
      if (handlers[event]) handlers[event]({}, ...args);
    },
  };
}

describe("crash_reporter", () => {
  it("redacts secret-looking keys and known secret value shapes", () => {
    assert.deepEqual(
      redact({
        apiKey: "sk-abcdefghijklmnopqrstuvwxyz1234567890",
        Authorization: "Bearer abc.def.ghi",
        normal: "keep me",
      }),
      {
        apiKey: "[redacted]",
        Authorization: "[redacted]",
        normal: "keep me",
      },
    );
    assert.equal(redact("token sk-abcdefghijklmnopqrstuvwxyz1234567890 end"), "token s***redacted*** end");
  });

  it("sanitizes URLs and strips query, hash, and credentials", () => {
    assert.equal(
      sanitizeUrl("https://user:pass@example.com/c/abc?secret=1#frag"),
      "https://example.com/c/abc",
    );
    assert.equal(sanitizeHost("https://example.com/x?q=1"), "example.com");
    assert.equal(sanitizeUrl("not a url"), "not a url");
  });

  it("only keeps allow-listed manifest fields", () => {
    assert.deepEqual(
      normalizeManifest({ version: "0.12.0", apiVersion: "1", token: "sk-abc", name: "x" }),
      { version: "0.12.0", apiVersion: "1", name: "x" },
    );
    assert.equal(normalizeManifest({ token: "sk-abc" }), null);
  });

  it("writes a redacted bundle on render-process-gone", () => {
    const root = makeTempDir("agentnexus-crash");
    const reporter = createCrashReporter({
      app: makeApp(),
      userDataDir: root,
      now: () => new Date("2026-09-01T00:00:00.000Z"),
    });
    const wc = webContentsStub();
    reporter.attach(
      { webContents: wc },
      {
        origin: "https://localhost:6767",
        serverUrl: "https://user:pass@localhost:6767/secret?token=abc",
        serverManifest: { version: "0.12.0", token: "sk-secret" },
      },
    );
    wc.emit("render-process-gone", { reason: "crashed", exitCode: 1 });

    const latest = reporter.getLatestReport();
    assert.ok(latest);
    assert.equal(latest.data.kind, "render-process-gone");
    assert.equal(latest.data.version, "0.12.0");
    assert.equal(latest.data.originHost, "localhost:6767");
    assert.equal(latest.data.serverUrl, "https://localhost:6767/secret");
    assert.deepEqual(latest.data.serverManifest, { version: "0.12.0" });
    assert.ok(fs.existsSync(path.join(root, DIAGNOSTICS_DIR)));
  });

  it("prunes older bundles beyond the cap", () => {
    const root = makeTempDir("agentnexus-crash-prune");
    let tick = 0;
    const reporter = createCrashReporter({
      app: makeApp(),
      userDataDir: root,
      now: () => new Date(Date.UTC(2026, 0, 1, 0, 0, 0, tick++)),
    });
    for (let i = 0; i < 25; i += 1) {
      reporter.writeReport({ kind: "render-process-gone", details: { i } });
    }
    const files = fs.readdirSync(path.join(root, DIAGNOSTICS_DIR)).filter((f) => /^crash-.*\.json$/.test(f));
    assert.ok(files.length <= MAX_DIAGNOSTIC_FILES);
  });

  it("registers child-process-gone and process-gone app events", () => {
    const events = {};
    const app = {
      getName: () => "AgentNexus",
      getVersion: () => "0.12.0",
      getPath: () => ".",
      on: (name, fn) => {
        events[name] = fn;
      },
    };
    const root = makeTempDir("agentnexus-crash-app");
    const reporter = createCrashReporter({
      app,
      userDataDir: root,
      now: () => new Date("2026-09-01T00:00:00.000Z"),
    });
    reporter.registerAppEvents();
    assert.equal(typeof events["child-process-gone"], "function");
    events["child-process-gone"]({}, { reason: "crashed" });
    const latest = reporter.getLatestReport();
    assert.equal(latest.data.kind, "child-process-gone");
  });
});
