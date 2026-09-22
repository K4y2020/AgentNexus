"use strict";

/** New names win, including empty values. Legacy prefixes are removed in 2.0. */
function readAgentNexusEnv(key, env = process.env) {
  for (const prefix of ["AGENTNEXUS_", "OMNIGENT_", "OMNIGENTS_", "OMNIAGENTS_"]) {
    const value = env[prefix + key];
    if (value !== undefined) return value;
  }
  return undefined;
}

module.exports = { readAgentNexusEnv };
