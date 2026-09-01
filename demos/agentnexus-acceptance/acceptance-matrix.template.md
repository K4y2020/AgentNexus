# AgentNexus Acceptance Matrix

One row per collaborative run against the fixed sample
(`demos/agentnexus-acceptance/`). Copy this template into the run session,
fill the evidence fields, and link the session/artifact where your product
allows it. A row is only "accepted" when every required column is present and
green.

| Run / Session | Task IDs | Reviewer | Diff reviewed | Evidence command | Tests pass | No secrets in diff | Workspace scope respected | Result |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `2026-*.run_1` / session link | `T01` | reviewer session id | `PASS` / link to diff | `python demos/agentnexus-acceptance/verify_baseline.py` | `PASS` / link to output | `PASS` | `PASS` | `accepted` |

Rules:

- Diff reviewed must name the reviewer session, not just "yes".
- Tests pass must cite the evidence-command output or a session block that
  contains it.
- No secrets in diff is checked by review plus the repo security scan; a row
  cannot override a scanner finding.
- Workspace scope respected means the diff never touched files outside
  `sample_repo/` (the fixed workflow's own files excepted).
- Result is `accepted`, `needs_changes`, or `blocked`; accepted rows need all
  other columns `PASS`.
