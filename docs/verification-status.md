# AgentNexus plan verification status

Status snapshot taken 2026-09-02 at `cdf88b84` (main). This file tracks the
plan's gates with authoritative evidence only; a row is marked done when the
evidence below proves it, not when a page or code path merely exists.

Updated 2026-09-02 with independent UI event delivery, runner relaunch, and
host tunnel reconnect p95 samples.

## Quality checks (fresh, this machine)

| Check | Result |
|---|---|
| Ruff (whole repo) | All checks passed |
| Coordination/behavior/model/workflow tests | 90/90 passed |
| P3/P4 server acceptance | 2/2 passed (real worktrees merge, restart recovery) |
| Control-plane mock reliability | 100/100 cumulative, zero `effect_unknown` |
| Full-turn benchmark journey smoke | 9/9 runner journey types passed on Windows |
| A2A delivery benchmark (mock LLM, 30 samples) | 30/30 passed, p95 565.6ms / p99 568.4ms |
| Server/UI stream reconnect benchmark (mock LLM, 30 samples) | 30/30 passed, p95 780.5ms / p99 801.6ms |
| UI event delivery benchmark (mock LLM, 30 samples) | 30/30 passed, p95 32.8ms / p99 33.5ms |
| Runner relaunch benchmark (mock LLM, 30 samples) | 30/30 passed, p95 16754.1ms / p99 16754.1ms |
| Host tunnel reconnect benchmark (mock LLM, 30 samples) | 30/30 passed, p95 5958.5ms / p99 6036.2ms |
| Frontend vitest | 6280 passed, 3 expected fail, 1 skipped |
| Web production build (`vite build`) | Succeeded (42s) |
| Electron desktop tests (`node --test`) | 366/366 passed |
| Linux desktop smoke | AppImage + deb built in WSL Ubuntu (SHA256 recorded) |
| Windows desktop build | NSIS setup.exe + portable zip built (unsigned) |
| Launch smoke | `AgentNexus.exe` started and exited cleanly, no orphan |

## Gate status

### P0 fork/reliability baseline
- Windows paths, host fallback, API key helper, watchdog, process-tree and
  shell fixes all have regression coverage.
- Doctor/diagnose CLI exists; upstream synced; `ruff`/`tsc` internal gates
  green.
- UI event delivery, runner relaunch, and host tunnel reconnect now have
  independent 30-sample mock-LLM benchmarks on this machine, below their plan
  gates: `ui_event_running` p95 32.8ms (<500ms), `session_cold_restart` p95
  16754.1ms (<30s), and `host_tunnel_reconnect` p95 5958.5ms (<15s), all 0
  failures.
- Remaining: repeated clean-machine cold boot/restart/wake matrix and the
  24h soak still need operator time on candidate builds.

### P1 transparent cockpit
- Behavior pack resolve/injection/requested-vs-resolved + delivery receipt
  endpoints and tests exist; Inspector distinguishes unconfirmed injection.
- Remaining: real-world model/tool observability soak on a candidate build.

### P2 durable agent bus
- SQLAlchemy coordination store, outbox, dispatcher, receipts, idempotency,
  ACL/tree validation, and a real `claude-sdk` control-plane E2E that
  converged to `accepted` are verified. On 2026-09-02 a real local Codex
  custom-provider control-plane run also reached `succeeded` with
  `consumed: 4` and terminal-idle receipts from real Codex turns
  (`tests/integration/test_real_provider_control_plane.py`).
- `live message` p95 now has an independent 30-sample mock-LLM benchmark
  (`a2a_message_delivery` in `dev/benchmarks/omnigent/journeys.py`): p95
  565.6ms, p99 568.4ms, 0 failures on this machine, below the <1s gate.
  The measured span is POST message → durable outbox → Dispatcher → runner
  injection → terminal-idle consumption receipt.
- The full-turn benchmark harness now uses the OS temp dir for its throwaway
  workspace; on Windows a drive-relative `\tmp\...` path previously failed
  session-create validation with HTTP 400, blocking the cold-start journeys.
- Server/UI stream reconnect p95 now has an independent 30-sample mock-LLM
  benchmark (`server_stream_reconnect` in `dev/benchmarks/omnigent/journeys.py`):
  p95 780.5ms, p99 801.6ms, 0 failures on this machine, below the <5s gate.
  The sample drops an open stream, posts a gated turn, reattaches, releases the
  mock gate, and times to the first output delta with model block time excluded.

### P3 workspace/git coordination
- Persistent lease (SQLAlchemy), fencing token, managed-path validation,
  worktree safety, merge preview/execute with head/dirty guards exist and
  are tested.
- Two-implementer parallel isolation is now covered by a real-git test
  (`tests/host/test_git_worktree.py`) and a server integration test that
  creates two parallel worktree sessions off one repo
  (`tests/server/integration/test_session_worktree_create.py`).
- Real-git two-implementer acceptance is now proven through the full server
  API: each implementer commits in a distinct real worktree, the write lease
  is required, the persisted merge preview rejects a stale fencing token, and
  only the explicit confirm/execute POST merges both branches into `main`
  (`tests/server/integration/test_two_implementer_real_merge.py`).
- Remaining: a candidate desktop build manual walkthrough of the same flow.

### P4 recoverable workflow
- Fixed Plan -> Implement -> Review -> Test auto-advance, artifacts,
  pause/resume/cancel/retry/reassign, deadlines, DAG template, and behavior
  overrides exist; control-plane-native real E2E converged both on the
  documented Claude SDK smoke and on a fresh real local Codex provider run.
- Template upgrade isolation is covered: a newer DAG template instance
  leaves an already-running run's template, metadata and task set unchanged
  (`tests/test_coordination.py`).
- Resume-after-crash acceptance is now proven at server level: a fresh app and
  fresh store object rebuild after a torn dispatch (implementer running with
  no durable message), the workflow scheduler re-queues exactly the missing
  stage, and the dispatcher delivers it through RunnerRouter while a consumed
  receipt is never replayed (`tests/server/integration/test_workflow_restart_recovery.py`).
- Remaining: a wall-clock crash drill on the signed candidate build.

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
- Linux smoke artifacts were produced on 2026-09-02 in WSL Ubuntu from main
  (`71d31bfc`): `AgentNexus-0.12.0-dev.0.AppImage` (124 MB,
  `dca2a35254bde19542bf7366e5cd8b8af860eec70fe1341d8b9aad685a9899fe`) and
  `agentnexus-desktop-electron_0.12.0-dev.0_amd64.deb` (97 MB,
  `0a3873d417675bb7b560dfe78476afd2a80f6e7fda74f6af647f6c2582daff88`),
  copied to `U:\AI\MultiAgent\artifacts\linux-smoke`.
- Two operator-gated real Codex control-plane workflows now succeeded against
  the local Codex provider (`run_0ad00931f77646f9` and
  `run_7068d5c4539f43ee`), each with `consumed: 4` and zero effect-unknown
  messages.
- Remaining: real-provider 100-run samples, a macOS smoke artifact, and the
  real-user Gate D sample (3-5 users, at least 5 repos / 20 runs).

## External blockers

- Code signing requires a certificate + secrets not present locally.
- Real-provider 100-run samples consume provider quota/tokens and must be
  explicitly launched by the operator. An attempted real `claude-sdk` smoke
  was blocked by the local aggregator's 429 rate limit
  (`All credentials for model gemini-3.7-flash-high are cooling down`), not
  by the control plane; the machine's real local Codex provider route is
  healthy and completed two operator-gated control-plane workflows.
- GitHub Actions jobs currently do not start at all: the provider returned
  "recent account payments have failed or your spending limit needs to be
  increased" as an annotation on desktop-cross-platform-smoke run
  `33548075760`. Until the account billing state is fixed, CI cannot produce
  the macOS side of the platform smoke gate.
- 24h soak and Gate D require wall-clock time and real machines/users.
