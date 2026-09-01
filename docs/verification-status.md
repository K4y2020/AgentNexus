# AgentNexus plan verification status

Status snapshot taken 2026-09-02 at `cdf88b84` (main). This file tracks the
plan's gates with authoritative evidence only; a row is marked done when the
evidence below proves it, not when a page or code path merely exists.

## Quality checks (fresh, this machine)

| Check | Result |
|---|---|
| Ruff (whole repo) | All checks passed |
| Coordination/behavior/model/workflow tests | 90/90 passed |
| Control-plane mock reliability | 100/100 cumulative, zero `effect_unknown` |
| Frontend vitest | 6280 passed, 3 expected fail, 1 skipped |
| Web production build (`vite build`) | Succeeded (42s) |
| Electron desktop tests (`node --test`) | 366/366 passed |
| Windows desktop build | NSIS setup.exe + portable zip built (unsigned) |
| Launch smoke | `AgentNexus.exe` started and exited cleanly, no orphan |

## Gate status

### P0 fork/reliability baseline
- Windows paths, host fallback, API key helper, watchdog, process-tree and
  shell fixes all have regression coverage.
- Doctor/diagnose CLI exists; upstream synced; `ruff`/`tsc` internal gates
  green.
- Remaining: repeated clean-machine cold boot/restart/wake matrix and the
  24h soak still need operator time on candidate builds.

### P1 transparent cockpit
- Behavior pack resolve/injection/requested-vs-resolved + delivery receipt
  endpoints and tests exist; Inspector distinguishes unconfirmed injection.
- Remaining: real-world model/tool observability soak on a candidate build.

### P2 durable agent bus
- SQLAlchemy coordination store, outbox, dispatcher, receipts, idempotency,
  ACL/tree validation, and a real `claude-sdk` control-plane E2E that
  converged to `accepted` are verified.
- `live message` p95 and Server/UI reconnect p95 have no independent
  sample yet; the reliability loop is the proxy evidence.

### P3 workspace/git coordination
- Persistent lease (SQLAlchemy), fencing token, managed-path validation,
  worktree safety, merge preview/execute with head/dirty guards exist and
  are tested.
- Two-implementer parallel isolation is now covered by a real-git test
  (`tests/host/test_git_worktree.py`) and a server integration test that
  creates two parallel worktree sessions off one repo
  (`tests/server/integration/test_session_worktree_create.py`).
- Remaining: a real two-implementer demo flow and real-merge acceptance on a
  candidate build.

### P4 recoverable workflow
- Fixed Plan -> Implement -> Review -> Test auto-advance, artifacts,
  pause/resume/cancel/retry/reassign, deadlines, DAG template, and behavior
  overrides exist; control-plane-native real E2E converged.
- Template upgrade isolation is covered: a newer DAG template instance
  leaves an already-running run's template, metadata and task set unchanged
  (`tests/test_coordination.py`).
- Remaining: resume-after-crash acceptance runs.

### P5 signed Windows internal beta
- Electron shell, update overlay, backup/upgrade guard, uninstall flow and
  the unsigned installer/portable zip build locally.
- Remaining: real signing certificate (`WIN_CSC_LINK` /
  `WIN_CSC_KEY_PASSWORD`), clean-machine 15-minute install + first run, and
  24h soak on the signed candidate.

### P6 public beta hardening
- Mock 100-run baseline collected (0 failures); nightly soak job added.
- Migration, privacy, and support-boundary docs shipped:
  `docs/migration-guide.md`, `docs/privacy.md`, `docs/support-boundaries.md`.
- Remaining: real-provider 100-run samples, macOS/Linux smoke artifacts,
  and the real-user Gate D sample (3-5 users, at least 5 repos / 20 runs).

## External blockers

- Code signing requires a certificate + secrets not present locally.
- Real-provider 100-run samples consume provider quota/tokens and must be
  explicitly launched by the operator. An attempted real `claude-sdk` smoke
  was blocked by the local aggregator's 429 rate limit
  (`All credentials for model gemini-3.7-flash-high are cooling down`), not
  by the control plane.
- 24h soak and Gate D require wall-clock time and real machines/users.
