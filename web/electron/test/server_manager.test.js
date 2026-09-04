// Tests for the auth gate in src/server_manager.js (`ensureServerAuth`), run
// with `node --test`. The spawning functions need a real binary + live server
// and are covered by the manual verification flow; here we test the pure
// decision logic: loopback skip → /v1/me probe → (idempotent) login → error.
//
// `server_manager` captures the `omnigent_cli` module object once at require
// time, so mocking methods on that same shared object (via `mock.method`) is
// seen by the code under test.

const { describe, it, mock, afterEach } = require("node:test");
const assert = require("node:assert/strict");

const cli = require("../src/omnigent_cli");
const { ensureServerAuth, isHostConnectedMarker } = require("../src/server_manager");

const SERVER = "https://app.example.com";
const CLI_PATH = "/bin/omnigent";

describe("ensureServerAuth", () => {
  afterEach(() => {
    mock.restoreAll();
  });

  it("skips auth entirely for a loopback server (no probe, no login)", async () => {
    mock.method(cli, "isLoopbackServer", () => true);
    const probe = mock.method(cli, "probeServerAuth", async () => ({
      authed: false,
      reachable: true,
    }));
    const login = mock.method(cli, "loginServer", async () => ({ ok: false, output: "" }));

    const res = await ensureServerAuth(CLI_PATH, "http://localhost:6767");

    assert.deepEqual(res, { ok: true });
    assert.equal(probe.mock.callCount(), 0);
    assert.equal(login.mock.callCount(), 0);
  });

  it("skips login when the probe reports already authed", async () => {
    mock.method(cli, "isLoopbackServer", () => false);
    mock.method(cli, "probeServerAuth", async () => ({ authed: true, reachable: true }));
    const login = mock.method(cli, "loginServer", async () => ({ ok: false, output: "" }));

    const res = await ensureServerAuth(CLI_PATH, SERVER);

    assert.deepEqual(res, { ok: true });
    assert.equal(login.mock.callCount(), 0);
  });

  it("skips login (defers to the connect attempt) when the server is unreachable", async () => {
    mock.method(cli, "isLoopbackServer", () => false);
    mock.method(cli, "probeServerAuth", async () => ({ authed: false, reachable: false }));
    const login = mock.method(cli, "loginServer", async () => ({ ok: false, output: "" }));

    const res = await ensureServerAuth(CLI_PATH, SERVER);

    assert.deepEqual(res, { ok: true });
    assert.equal(login.mock.callCount(), 0);
  });

  it("runs login when not authed, and returns ok on success", async () => {
    mock.method(cli, "isLoopbackServer", () => false);
    mock.method(cli, "probeServerAuth", async () => ({ authed: false, reachable: true }));
    const login = mock.method(cli, "loginServer", async () => ({ ok: true, output: "Logged in." }));

    const res = await ensureServerAuth(CLI_PATH, SERVER);

    assert.deepEqual(res, { ok: true });
    assert.equal(login.mock.callCount(), 1);
    assert.deepEqual(login.mock.calls[0].arguments, [CLI_PATH, SERVER]);
  });

  it("returns an authError with a generic message and does NOT surface raw login output", async () => {
    mock.method(cli, "isLoopbackServer", () => false);
    mock.method(cli, "probeServerAuth", async () => ({ authed: false, reachable: true }));
    // `omnigent login` stdout on the OIDC path can carry the login-ticket URL
    // (auth material); it must never reach the renderer via the error string.
    mock.method(cli, "loginServer", async () => ({
      ok: false,
      output: "Opening browser for login: https://app.example.com/auth/login?ticket=SECRET123",
    }));

    const res = await ensureServerAuth(CLI_PATH, SERVER);

    assert.equal(res.ok, false);
    assert.equal(res.authError, true);
    assert.doesNotMatch(res.error, /ticket=|SECRET123/);
    assert.match(res.error, /omnigent login https:\/\/app\.example\.com/);
  });

  it("uses the same generic message when login fails with no output", async () => {
    mock.method(cli, "isLoopbackServer", () => false);
    mock.method(cli, "probeServerAuth", async () => ({ authed: false, reachable: true }));
    mock.method(cli, "loginServer", async () => ({ ok: false, output: "" }));

    const res = await ensureServerAuth(CLI_PATH, SERVER);

    assert.equal(res.ok, false);
    assert.equal(res.authError, true);
    assert.match(res.error, /omnigent login https:\/\/app\.example\.com/);
  });
});

describe("isHostConnectedMarker", () => {
  it("matches real CLI connected output line", () => {
    const line = "Connected as 'DESKTOP-NQTAH8K' (b437d4563fce4d8b9331f9419af26810), 0 live runner(s). Listening for sessions — Ctrl-C to disconnect.";
    assert.equal(isHostConnectedMarker(line), true);
  });

  it("matches legacy checkmark connected marker", () => {
    assert.equal(isHostConnectedMarker("✓ Connected"), true);
  });

  it("matches listening status", () => {
    assert.equal(isHostConnectedMarker("Listening for sessions"), true);
  });

  it("rejects unrelated or booting log messages", () => {
    assert.equal(isHostConnectedMarker("Connecting to http://127.0.0.1:6767"), false);
    assert.equal(isHostConnectedMarker("Session logs: ~/.omnigent/logs/runner/"), false);
    assert.equal(isHostConnectedMarker(null), false);
    assert.equal(isHostConnectedMarker(""), false);
  });
});
