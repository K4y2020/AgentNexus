const { it } = require("node:test");
const assert = require("node:assert/strict");
const { readAgentNexusEnv } = require("../src/envCompat");

for (const key of ["URL", "AUTH_TOKEN", "DATA_DIR", "CONFIG_HOME", "NOTARIZE_DMG"]) {
  it(`${key}: new values win, including explicit empty values`, () => {
    const env = { [`OMNIGENT_${key}`]: "legacy" };
    assert.equal(readAgentNexusEnv(key, env), "legacy");
    env[`AGENTNEXUS_${key}`] = "new";
    assert.equal(readAgentNexusEnv(key, env), "new");
    env[`AGENTNEXUS_${key}`] = "";
    assert.equal(readAgentNexusEnv(key, env), "");
  });
}

it("recognizes all historical prefixes in deterministic order", () => {
  const env = { OMNIAGENTS_URL: "oldest", OMNIGENTS_URL: "older", OMNIGENT_URL: "old" };
  assert.equal(readAgentNexusEnv("URL", env), "old");
  delete env.OMNIGENT_URL;
  assert.equal(readAgentNexusEnv("URL", env), "older");
  delete env.OMNIGENTS_URL;
  assert.equal(readAgentNexusEnv("URL", env), "oldest");
  assert.equal(readAgentNexusEnv("URL", {}), undefined);
});
