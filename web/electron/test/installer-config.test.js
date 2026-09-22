const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const pkg = require("../package.json");

describe("Windows installer configuration", () => {
  it("ships an assisted per-user NSIS installer plus a portable zip", () => {
    assert.ok(pkg.build.win.target.some((t) => t === "nsis"));
    assert.ok(pkg.build.win.target.some((t) => t === "zip"));
    assert.equal(pkg.build.nsis.oneClick, false);
    assert.equal(pkg.build.nsis.perMachine, false);
    assert.equal(pkg.build.nsis.allowToChangeInstallationDirectory, true);
    assert.equal(pkg.build.nsis.deleteAppDataOnUninstall, false);
    assert.match(pkg.build.nsis.artifactName, /\$\{productName\}-\$\{version\}-.*\.\$\{ext\}/);
    assert.match(pkg.build.win.artifactName, /\$\{productName\}-\$\{version\}-.*\.\$\{ext\}/);
  });

  it("registers both branded and legacy deep-link schemes", () => {
    const schemes = pkg.build.protocols[0].schemes;
    assert.ok(schemes.includes("agentnexus"));
    assert.ok(schemes.includes("omnigent"));
    assert.equal(new Set(schemes).size, schemes.length);
  });

  it("keeps data on silent/updated uninstall and only purges on explicit choice", () => {
    const script = fs.readFileSync(path.join(__dirname, "..", "build", "installer.nsh"), "utf8");
    assert.match(script, /customUnInstall/);
    assert.match(script, /MessageBox/);
    assert.match(script, /RMDir \/r "\$APPDATA\\AgentNexus"/);
    assert.match(script, /RMDir \/r "\$PROFILE\\.agentnexus"/);
    assert.match(script, /\$\{ifNot\} \$\{Silent\}/);
  });
});
