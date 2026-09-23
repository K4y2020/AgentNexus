"use strict";

/** Read an `AGENTNEXUS_<key>` variable; an explicitly empty value is returned as-is. */
function readAgentNexusEnv(key, env = process.env) {
  return env[`AGENTNEXUS_${key}`];
}

module.exports = { readAgentNexusEnv };
