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
