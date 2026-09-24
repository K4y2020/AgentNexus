// Desktop crash diagnostics for the AgentNexus Electron shell.
//
// When a renderer process dies or the app sees an unexpected process event,
// this module writes a small, redacted diagnostic bundle under the Electron
// per-user data dir. The bundle records the desktop version, platform, origin
// host, the event details, and the current server identity so a crash can be
// reproduced without the user digging through logs by hand.
//
// **Privacy contract:** every field is passed through a redactor before it
// reaches disk. Query strings and secrets in URLs are stripped, keys whose
// names look like credentials are replaced wholesale, and common secret
// value shapes (sk-*, Bearer ..., dapi*) are masked. A server manifest is
// normalized to a small allow-list of fields, never the raw JSON.

"use strict";

const fs = require("fs");
const path = require("path");

/** Subdirectory under Electron's per-user data dir for crash bundles. */
const DIAGNOSTICS_DIR = "diagnostics";

/** Maximum crash bundles kept before pruning (oldest first). */
const MAX_DIAGNOSTIC_FILES = 20;

/** Object keys whose values are assumed secret and always dropped. */
const SECRET_KEY_RE =
  /(authorization|password|passwd|secret|token|api[_-]?key|bearer|cookie|credential)/i;

/** Common secret value shapes to mask anywhere they appear in strings. */
const SECRET_VALUE_RE =
  /\b(sk-[A-Za-z0-9_-]{8,}|Bearer\s+[A-Za-z0-9._~+/=-]{8,}|dapi[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b/g;

const MASKED = "***redacted***";

/** Fields allowed to survive from a server manifest in a crash bundle. */
const MANIFEST_ALLOWLIST = new Set(["apiVersion", "name", "product", "version", "serverVersion"]);

function maskSecretValue(value) {
  if (typeof value !== "string") return value;
  return value.replace(SECRET_VALUE_RE, (match) => {
    const first = match[0] ?? "";
    return `${first}${MASKED}`;
  });
}

/** Recursively redact a value, dropping secret-looking keys. */
function redact(value) {
  if (value === null || typeof value !== "object") {
    return maskSecretValue(value);
  }
  if (Array.isArray(value)) return value.map(redact);
  const out = {};
  for (const [key, child] of Object.entries(value)) {
    if (SECRET_KEY_RE.test(key)) {
      out[key] = "[redacted]";
    } else {
      out[key] = redact(child);
    }
  }
  return out;
}

/**
 * A safe, short URL descriptor for a crash bundle: scheme + host + pathname,
 * with any query string, hash, credentials, or long path dropped.
 *
 * @param {string} raw
 * @returns {string | null}
 */
function sanitizeUrl(raw) {
  if (typeof raw !== "string" || raw === "") return null;
  let url;
  try {
    url = new URL(raw);
  } catch {
    // Not a parseable URL (e.g. a bare host). Keep a short masked fragment.
    return maskSecretValue(raw).slice(0, 120) || null;
  }
  const descriptor = `${url.protocol}//${url.host}${url.pathname || "/"}`;
  const pathname = descriptor.slice(0, 160);
  return maskSecretValue(pathname) || null;
}

/** Extract just the origin host (no scheme/slash) for quick scanning. */
function sanitizeHost(raw) {
  const clean = sanitizeUrl(raw);
  if (!clean) return null;
  try {
    return new URL(clean).host || null;
  } catch {
    return null;
  }
}

/** Reduce a server manifest object to the allow-listed fields. */
function normalizeManifest(manifest) {
  if (!manifest || typeof manifest !== "object") return null;
  const out = {};
  for (const key of Object.keys(manifest)) {
    if (MANIFEST_ALLOWLIST.has(key)) {
      const value = manifest[key];
      if (typeof value === "string" || typeof value === "number") out[key] = value;
    }
  }
  return Object.keys(out).length > 0 ? out : null;
}

/** Sortable, filesystem-safe timestamp for a bundle filename. */
function timestampForFile(date) {
  const pad = (n, w = 2) => String(n).padStart(w, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}-${pad(date.getMinutes())}-${pad(date.getSeconds())}` +
    `-${pad(date.getMilliseconds(), 3)}Z`
  );
}

/**
 * Build the desktop crash reporter. Host dependencies are injected so the
 * module carries no Electron globals and can be unit-tested with fakes.
 *
 * @param {object} deps
 * @param {import("electron").App} deps.app
 * @param {string | (() => string)} deps.userDataDir Absolute Electron per-user
 *   data dir, or a thunk resolving it lazily (available before `app.whenReady`
 *   initializes the path on some platforms).
 * @param {() => string} [deps.getCurrentVersion] Desktop version reported.
 * @param {() => Date} [deps.now] Clock injection for tests.
 * @returns {{
 *   getDiagnosticsDir: () => string,
 *   writeReport: (input: object) => string | null,
 *   attach: (win: object, ctx: {origin?: string|null, serverUrl?: string|null, serverManifest?: object|null}) => void,
 *   registerAppEvents: () => void,
 *   getLatestReport: () => {file: string, data: object} | null,
 * }}
 */
function createCrashReporter({
  app,
  userDataDir,
  getCurrentVersion = () => app.getVersion(),
  now = () => new Date(),
}) {
  function resolveUserDataDir() {
    const value = typeof userDataDir === "function" ? userDataDir() : userDataDir;
    if (typeof value === "string" && value !== "") return value;
    try {
      return app.getPath("userData") || ".";
    } catch {
      return ".";
    }
  }
  const diagnosticsDir = () => path.join(resolveUserDataDir(), DIAGNOSTICS_DIR);
  const currentVersion = () => {
    try {
      return getCurrentVersion() ?? "unknown";
    } catch {
      return "unknown";
    }
  };

  function prune() {
    let entries;
    try {
      entries = fs
        .readdirSync(diagnosticsDir())
        .filter((name) => /^crash-.*\.json$/.test(name))
        .map((name) => ({ name, file: path.join(diagnosticsDir(), name) }))
        .sort((a, b) => a.name.localeCompare(b.name));
    } catch {
      return;
    }
    for (const entry of entries.slice(0, Math.max(0, entries.length - MAX_DIAGNOSTIC_FILES))) {
      try {
        fs.unlinkSync(entry.file);
      } catch {
        // Best-effort pruning; ignore races with the filesystem.
      }
    }
  }

  /**
   * Write a redacted crash bundle and return its absolute path (or null when
   * the diagnostic dir cannot be created).
   *
   * @param {object} input
   * @param {string} input.kind Event kind (render-process-gone, etc.).
   * @param {string|null} [input.host] Origin host that crashed.
   * @param {string|null} [input.url] Server URL descriptor.
   * @param {object|null} [input.details] Raw event details (redacted).
   * @param {string|null} [input.serverUrl] Server identity to record.
   * @param {object|null} [input.serverManifest] Server manifest to normalize.
   * @param {string|null} [input.cause] Optional free-text cause (redacted).
   * @returns {string | null}
   */
  function writeReport(input) {
    const stamp = now();
    const entry = {
      app: app.getName ? app.getName() : "AgentNexus",
      version: currentVersion(),
      platform: process.platform ?? "unknown",
      arch: process.arch ?? "unknown",
      timestamp: stamp.toISOString(),
      kind: String(input.kind ?? "unknown"),
      originHost: input.host ? sanitizeHost(input.host) : null,
      url: input.url ? sanitizeUrl(input.url) : null,
      serverUrl: input.serverUrl ? sanitizeUrl(input.serverUrl) : null,
      serverManifest: normalizeManifest(input.serverManifest),
      details: redact(input.details ?? null),
      cause: input.cause ? maskSecretValue(String(input.cause)).slice(0, 2000) : null,
    };
    try {
      fs.mkdirSync(diagnosticsDir(), { recursive: true });
      const file = path.join(diagnosticsDir(), `crash-${timestampForFile(stamp)}.json`);
      fs.writeFileSync(file, `${JSON.stringify(entry, null, 2)}\n`, "utf8");
      prune();
      return file;
    } catch (err) {
      console.error("[agentnexus] failed to write crash diagnostic:", String(err?.message ?? err));
      return null;
    }
  }

  /**
   * Attach crash/unresponsive listeners to a shell window's webContents.
   * The server context is captured at attach time so it survives renderer
   * teardown (the URL is gone by the time `render-process-gone` fires).
   *
   * @param {object} win A BrowserWindow-like with `.webContents`.
   * @param {{origin?: string|null, serverUrl?: string|null, serverManifest?: object|null | (() => object|null)}} [ctx]
   */
  function attach(win, ctx = {}) {
    const wc = win?.webContents;
    if (!wc || typeof wc.on !== "function") return;
    const origin = ctx.origin ?? null;
    const serverUrl = ctx.serverUrl ?? null;
    const manifestAt = () => {
      const value =
        typeof ctx.serverManifest === "function" ? ctx.serverManifest() : ctx.serverManifest;
      return value ?? null;
    };

    wc.on("render-process-gone", (_event, details) => {
      writeReport({
        kind: "render-process-gone",
        host: origin,
        url: serverUrl,
        serverUrl,
        serverManifest: manifestAt(),
        details,
      });
    });
    wc.on("unresponsive", () => {
      console.warn("[agentnexus] renderer became unresponsive at", origin ?? "unknown origin");
      // A soft note only — the process may recover, so don't roll a full bundle.
      try {
        const file = path.join(diagnosticsDir(), "latest-unresponsive.txt");
        fs.mkdirSync(diagnosticsDir(), { recursive: true });
        fs.writeFileSync(
          file,
          `${now().toISOString()} unresponsive ${origin ?? "unknown origin"}\n`,
          "utf8",
        );
      } catch {
        // Best-effort note.
      }
    });
    wc.on("responsive", () => undefined);
  }

  /** Subscribe to app-level child/process-gone surfaces where available. */
  function registerAppEvents() {
    if (!app || typeof app.on !== "function") return;
    app.on("child-process-gone", (_event, details) => {
      writeReport({ kind: "child-process-gone", details });
    });
    app.on("process-gone", (_event, details) => {
      writeReport({ kind: "process-gone", details });
    });
  }

  /** Read the most recently written bundle (by filename timestamp). */
  function getLatestReport() {
    let names;
    try {
      names = fs
        .readdirSync(diagnosticsDir())
        .filter((name) => /^crash-.*\.json$/.test(name))
        .sort()
        .reverse();
    } catch {
      return null;
    }
    if (names.length === 0) return null;
    const file = path.join(diagnosticsDir(), names[0]);
    try {
      return { file, data: JSON.parse(fs.readFileSync(file, "utf8")) };
    } catch {
      return null;
    }
  }

  return {
    getDiagnosticsDir: () => diagnosticsDir(),
    writeReport,
    attach,
    registerAppEvents,
    getLatestReport,
  };
}

module.exports = {
  createCrashReporter,
  DIAGNOSTICS_DIR,
  MAX_DIAGNOSTIC_FILES,
  redact,
  sanitizeUrl,
  sanitizeHost,
  normalizeManifest,
};
