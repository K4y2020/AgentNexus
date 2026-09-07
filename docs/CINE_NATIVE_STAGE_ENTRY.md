# Cine Native Stage Entry

Date: 2026-09-07

Deployment update: Cine v10 and the AgentNexus-managed generation gate are now
live. See `docs/CINE_DEPLOYMENT_ACCEPTANCE.md` for the actual positive/negative
no-generation checks and the remaining boundary. The release notes below describe
the earlier CLI-only stage.

## Changes

`examples/cine/skills/film-analysis/pipeline/production.py` is the single local
entry for drafting and native validation:

- `init`: creates an incomplete native outline, with user-supplied parameters,
  no invented story or characters. Refuses to overwrite existing work.
- `seed --stage cast|art|script|storyboard`: calls the corresponding existing
  Node seeder and retains upstream IDs. No second schema or copied sample story.
- `check`: resolves and executes real native validators, passing upstream files.
  It does not interpret a printed PASS as approval or run a model-written substitute.
- Reports include actual commands, input and validator hashes, stdout/stderr,
  process exit codes, missing-check notices and timeout/change failures. Unique
  reports and a latest convenience copy live under `.cine-validation/`.
- Bad root schemas fail preflight with `invalid_shape`; a null process exit code
  means the native process was not invoked, not that it succeeded.
- Missing quote source is `incomplete`, not a full validation pass. Every check
  re-executes validators; old reports are not authorization tokens.

`load_skill` now includes the actual skill directory, avoiding recursive disk
searches for bundled scripts. The previously fixed UTF-8 reader is retained.

The source-material adapter accepts empty `unreviewed`/`unverified` review rows
as unverified without modifying source files. Contradictory rows with observations
or image receipts remain errors; nothing is promoted to reviewed automatically.

## Use

Run from the repository root, replacing `<output>` with a production directory:

```powershell
uv run python examples/cine/skills/film-analysis/pipeline/production.py init <output> --source "Film title" --episodes 1 --seconds 30 --genre "Sci-fi rescue" --adapt-mode "借壳"
```

Fill the outline using the existing outline schema, then seed cast, art and script:

```powershell
uv run python examples/cine/skills/film-analysis/pipeline/production.py seed <output> --stage cast
uv run python examples/cine/skills/film-analysis/pipeline/production.py seed <output> --stage art
uv run python examples/cine/skills/film-analysis/pipeline/production.py seed <output> --stage script
```

After filling script, seed storyboard; after completing the native documents, check:

```powershell
uv run python examples/cine/skills/film-analysis/pipeline/production.py seed <output> --stage storyboard
uv run python examples/cine/skills/film-analysis/pipeline/production.py check <output> --source-text <actual-source-or-approved-treatment.txt>
```

The native adapt-mode values remain 忠实 / 抽核 / 借壳; do not infer the user's
choice from this example. Seeds are incomplete drafts, never validation results.
Existing short-drama/H3-specific limitations remain real failures where applicable;
no new provider support or gate exemption was invented to make a short film pass.

## Verification

- Cine tests: **117 passed**.
- Skill reader/loader, Cine bundle and Seedance tests: **52 passed**.
- Ruff passed for touched code.
- Actual CLI replay of the native example: **5 validators passed** with source text.
- Actual CLI replay of the previous live failure: **5 invalid-shape rejections**,
  command exit 1, without rewriting those artifacts.
- Actual previous review sidecar now exports **11 rows**, while the same **3**
  reviewed source contracts pass receipt checks; source indexes remain unchanged.
- Tests cover refusal to overwrite, missing Node, timeout, changed inputs,
  printed PASS with nonzero process exit, missing quote verification, and aliases.

Local replay reports are under `.codex-tmp/cine-production-acceptance/`.
Run focused regressions with:

```powershell
uv run pytest examples/cine/tests/test_production.py examples/cine/tests/test_handoff.py -q
```

## Release Boundary

The repository's Cine config and shared skill instructions point to this entry.
The running Cine v8 has not been re-registered or restarted in this change.
No live generation, canvas mutation, film artifact repair or model A/B run occurred.

The entry enforces real checks when used; it is not a sandbox preventing arbitrary
shell writes and has not been inserted into every server/API generation path.
`native_validated` does not mean generated assets exist, that a provider is compatible,
that a story is good, or that the user authorized generation.
