# Control-plane reliability runs

AgentNexus keeps a reproducible reliability gate for the durable multi-agent
bus: each sample starts a fresh session tree and drives one full
Plan -> Implement -> Review -> Test workflow through the real local server,
runner, wrapped SDK harness, outbox, dispatcher, and terminal-idle receipt.
The model provider is the deterministic mock LLM, so a red sample points at
control-plane, runner, or harness plumbing rather than paid-provider flakiness.

## Run the baseline

```bash
RUNS=5 scripts/run_control_plane_reliability.sh
```

The default is 100 samples. Pytest JUnit XML is written to
`.reliability-results/reliability.xml` (override with
`RELIABILITY_ARTIFACTS`). The workflow
`.github/workflows/control-plane-reliability.yml` runs the 100-sample baseline
weekly and on manual dispatch.

Native Windows/PowerShell users can run the same baseline with:

```powershell
$env:RUNS = 5
.\scripts\run_control_plane_reliability.ps1
```

The PowerShell entry point accepts `-Runs N` for an explicit override and
`RELIABILITY_ARTIFACTS` for the artifact directory. It writes the same
`.reliability-results/reliability.xml` layout as the bash script. The
`.github/workflows/control-plane-reliability.yml` workflow can also run the
Windows leg on manual dispatch (`include_windows: true`); the weekly scheduled
run stays Ubuntu to keep the regression baseline inexpensive.

## What each sample verifies

- One workflow run reaches `succeeded` with Planner, Implementer, Reviewer,
  and Tester all `succeeded`.
- At least four coordination messages are consumed via terminal-idle receipts.
- `effect_unknown_count` is `0`; no sample may end with an injected-but-
  unconfirmed message that the reconciler had to flag.
- No provider credentials are required; explicit `ANTHROPIC_API_KEY`,
  `OPENAI_API_KEY`, `CODEX`, and `CLAUDE_CODE` are cleared in CI.

## Relationship to the public-beta gate

This is the code-level reproducibility harness for the 100-run reliability
measurement. The plan's public-beta exit criterion is still operational: at
least 100 independent runs on real machines/providers with an infrastructure
failure rate below 2%, plus the 24-hour soak and real-user gates described in
`docs/coding-agent-control-plane-plan.md`.

## Real CLI cross-harness handoff E2E

The mock baseline above proves the control-plane plumbing. As a complementary
real-credential check, AgentNexus also validates that the actual `claude` and
`codex` CLIs can collaborate on one shared checkout without the user copying
text between them: Claude plans, Codex implements, Claude reviews, Codex fixes,
and the loop converges to an approved verdict.

This is a **direct-CLI handoff** (each agent reads the repository state the
previous one left), not the server/runner/Dispatcher path. It is the
real-credential complement; the control-plane-native real run remains a
separate follow-up.

### Procedure

Run from a fresh scratch git repo (a throwaway directory is safest):

```bash
# 1. Plan - Claude
cd $SCRATCH && claude -p --permission-mode acceptEdits \
  "Write PLAN.md with an exact spec, acceptance criteria, and test list."

# 2. Implement - Codex (uses the CLI's configured provider/credentials)
codex exec --skip-git-repo-check -s workspace-write \
  "Implement per PLAN.md; write the code and tests, then run them."

# 3. Review - Claude
claude -p --permission-mode acceptEdits \
  "Read PLAN.md and the implementation; check every acceptance criterion; write REVIEW.md with APP/REJ."

# 4. Fix loop - feed REJECTED findings back to Codex, then re-review with Claude.

# 5. Independent verification
python -m pytest -q
```

No secrets are baked into this repo or the runbook; credentials come from the
CLIs' own login/config (`~/.claude`, `~/.codex`). Providers and models are read
from the invoker's CLI config, so the exact backend is environment-specific.

### Known caveats (Windows)

- The Codex CLI `workspace-write` sandbox can fail to launch on Windows when
  many skills/plugins push the sandbox-setup command line past the 32k
  `CreateProcessW` limit (OS error 206). In a throwaway scratch directory this
  is worked around with `--dangerously-bypass-approvals-and-sandbox`; keep it
  out of any shared or trusted checkout.
- A configured local/aggregator gateway may transiently return HTTP 429 after
  heavy runs; retry after the rate-limit window. This is infrastructure
  throttling, not an AgentNexus defect.
