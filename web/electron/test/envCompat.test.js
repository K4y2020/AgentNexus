const { it } = require("node:test");
const assert = require("node:assert/strict");
const { readAgentNexusEnv } = require("../src/envCompat");

for (const key of ["URL", "AUTH_TOKEN", "DATA_DIR", "CONFIG_HOME", "NOTARIZE_DMG"]) {
  it(`${key}: reads the AGENTNEXUS_ value, including explicit empty values`, () => {
    const env = {};
    assert.equal(readAgentNexusEnv(key, env), undefined);
    env[`AGENTNEXUS_${key}`] = "new";
    assert.equal(readAgentNexusEnv(key, env), "new");
    env[`AGENTNEXUS_${key}`] = "";
    assert.equal(readAgentNexusEnv(key, env), "");
  });
}
