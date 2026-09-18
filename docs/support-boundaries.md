# Support boundaries

## Supported platforms

- **Tier 1**: Windows x64. `agentnexus server`, the web UI, SDK-based
  harnesses (`claude-sdk`, `codex`, `openai-agents`, cursor), the Electron
  desktop shell, and the Job Object process-tree containment are the
  supported path.
- **Tier 2 (beta)**: macOS ARM64 and Linux via the desktop smoke workflow.
  Unsigned artifacts only until signing/notarization secrets are attached.
- **Not supported on Windows**: native tmux/PTY wrappers
  (`agentnexus claude`, `agentnexus codex`, `agentnexus cursor`), `bwrap`/
  `seatbelt` filesystem and network sandboxing, and the L7 egress proxy.
  Use an SDK harness or the web UI instead.

## Harness model

- The durable control plane treats every harness as a capability set:
  live injection, queue, terminal-idle receipts, and effect semantics are
  reported honestly. A harness without a receipt shows `unconfirmed` /
  `unknown` rather than claiming delivery.
- Native TUI agents are best-effort observation; structured
  SDK/ACP adapters are the primary supported integration path.

## Provider and rate limits

- Gateway or provider throttling (for example HTTP 429) is infrastructure
  behavior, not a control-plane defect. Retry after the rate-limit window
  and use the deterministic mock LLM for credential-free regression tests.
- `effect_unknown` is a designed state: if an external side effect cannot
  be confirmed, the system records it and does not auto-replay.

## Reliability gates

- The mock 100-run baseline is credential-free and CI-runnable.
- A real-provider 100-run sample, a candidate-release 24h soak, and a
  clean-machine 15-minute install + first collaboration run are release
  gates, not supported by the mock suite alone.
- Desktop signing only happens when `WIN_CSC_LINK` /
  `WIN_CSC_KEY_PASSWORD` secrets are configured; without them builds are
  explicit unsigned artifacts intended for internal testing.

## Data and upgrade support

- Upgrades run Alembic migrations against the configured database; back up
  the data dir before upgrading (see `docs/migration-guide.md`).
- The desktop updater makes a pre-upgrade backup and guards an interrupted
  upgrade, but complete automatic rollback is deferred to P6/public beta.
- Coordinating agents on dirty workspaces: dirty state is never implicitly
  reset, checked out, or deleted; users must confirm merge/cleanup actions.

## What is out of scope

- General-purpose workflow canvas, marketplace, multi-user SaaS, and
  remote execution are later-phase items, not supported in the local MVP.
- Model private chain-of-thought is not stored or displayed.
