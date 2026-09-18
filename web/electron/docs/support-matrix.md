# AgentNexus support matrix

## Distribution channels

| Channel | Status |
| --- | --- |
| Source checkout (`uv run python -m agentnexus.server`) + web UI | Supported for developers |
| Windows portable zip | Internal/signed beta; portable user data kept per-user |
| Windows NSIS installer | Signed internal beta path |
| macOS DMG/zip | Buildable; notarization requires Apple credentials |
| Linux AppImage/deb | Buildable Tier 2 |

## Platform matrix

| Platform | Level | Required coverage |
| --- | --- | --- |
| Windows 11 x64 | Tier 1 | Server/Host/SDK harness, paths, installer/upgrade/backup/uninstall |
| macOS ARM64/x64 | Tier 2 | Web/Desktop, SDK/native, keychain, worktree |
| Ubuntu x64 | Tier 2 | Server/Host, bwrap, tmux, headless |
| WSL2 | Tier 3 | Documented compatibility, not an alternative Windows promise |

Tier 1 means an automated and manual gate before a public Windows beta. Tier 2
means tested but outside the first public beta commitment. Tier 3 means
documented best-effort.

## Harness support boundary

AgentNexus discovers capabilities from installed harness CLIs. Support depends
on the vendor binary being installed, signed, and updated. The capability
bench (`tests/harness_bench/`) probes declared capabilities and reports drift
between declaration and observed behavior. A harness that is not installed or
not signed is reported as unavailable rather than silently proxied.

## Known limitations

- Full automatic rollback of a running new desktop binary is not present;
  rollback is implemented for upgrades that do not take effect, plus explicit
  snapshot restore for the rest.
- macOS/Linux installers are buildable but not the first public beta target.
- Telemetry dashboards, enterprise offline bundles, and full
  automatic upgrade rollback are public-beta hardening items, not current
  commitments.
- WSL2 is not the Windows support surface for host/runner process trees.

## Open-source and commercial boundary

The core upstream project is open source. AgentNexus is a branded fork with a
separate product name; external trademarks, logos, and vendor CLI binaries are
not redistributed by this repository. Enterprise support, SLA, and commercial
licensing terms, when published, are separate from this repository and must be
reviewed before a commercial deployment.

## Public-beta exit gates

Public beta closes only when the plan's metrics and soak gates pass: 24-hour
soak without orphan processes, 100 independent reliability runs at < 2%
infrastructure failure, five real repositories with 20 collaboration runs, and
at least three non-author users for a week. Those are operational measurements
and require real machines and users, not just code checks.

The reproducible mock-LLM 100-run harness lives in `docs/reliability-runs.md`
and is used for regression attribution, not as a substitute for the
real-provider sample.
