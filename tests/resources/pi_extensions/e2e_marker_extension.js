// E2E marker extension: writes a file under $HOME when Pi loads extensions.
// Used by tests/e2e/agentnexus/test_pi_managed_extensions_e2e.py.
const fs = require("fs");
const path = require("path");
const os = require("os");

module.exports = function (pi) {
  pi.on("session_start", () => {
    const marker = path.join(os.homedir(), "agentnexus-pi-ext-marker");
    fs.writeFileSync(marker, "loaded", "utf8");
  });
};
