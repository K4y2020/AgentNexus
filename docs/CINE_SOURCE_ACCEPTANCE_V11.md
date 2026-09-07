# Cine Source Acceptance v11

2026-09-07. Deployed and checked against the real redo Topic.

## Selected Scope

The user chose not to migrate execution into WSL. Filesystem and shell access are
unchanged. The Windows Job Object backend is not a filesystem sandbox; this change
does not claim to enforce read/write grants on arbitrary shell commands.

This release controls evidence acceptance, not whether the model can physically
read an old file. Historical material may still be read as reference. Its old
`model_reviewed` labels no longer count as evidence in the new acceptance tool.

## Implemented

- Added `cine_verify_report`, routed through the runner and current server session.
  It resolves only the Topic's bound canonical source revision, checks current
  session image receipts, and calculates requested coverage from source intervals.
- `scope=full` cannot silently be shortened to 30 seconds. `scope=sample` requires
  explicit, finite bounds within the source duration.
- A report ledger supplied via `ledger_path` is checked against verified canonical
  shot IDs and observations. Supplied source/revision/timing fields must also agree.
  Old or changed rows receive effective `historical_unverified` status, regardless
  of their claimed status. Original files are not modified.
- Repaired sequential reuse of tool call IDs. Calls and results are paired within
  a response and in recorded order. Overlapping ambiguous calls and shell-produced
  fake image results remain rejected. This restored the two real opening receipts
  that the previous global-name lookup incorrectly rejected.
- Source-material CLI export now explicitly returns `exported_unverified` and
  `can_claim_reviewed=false`; exporting a JSON file is not an acceptance check.
- Cine instructions call the new acceptance tool before source-report delivery.
  Rejected results must be presented as unverified drafts, not verified completion.

## Real Verification

Topic: http://127.0.0.1:6767/c/9d352108608eeeca7cab77d57e0a7a8d

| Request | Actual result |
| --- | --- |
| Sample 0-30 seconds | `accepted_visual_scope`, 11 scoped shots with current receipts |
| Full source + `source_shots_full.json` | `rejected`; only 11 canonical shots indexed; 115 imported rows historical/unverified |

The primary video stream's recorded duration is approximately 505.50 seconds.
This differs slightly from the container/audio duration previously quoted as 505.52.
Neither is remotely covered by a 30-second canonical revision.

Live result item IDs:

- Sample: `48911d4565314a9e964da9573f63b544`.
- Full report: `51d0437815c74f9fa8cc0be30ce1c075`.

Registered Cine version: **11**, same agent ID as before. The service was restarted
only after checking that no tasks or user questions were active. The test returned
to idle; no media generation or changes to existing report files were performed.

## Tests

- 82 tests passed across Seedance/source-review, dispatch, bundle and skill tools.
- 117 Cine tests passed; existing PySceneDetect deprecation warnings remain.
- Targeted Ruff checks passed.
- Regressions include old-label inheritance, changed observations, missing current
  receipts, shortened full scope, invalid sample bounds, sequential duplicate call
  IDs, ambiguous overlapping calls and shell output masquerading as an image receipt.

Reproduce without generating media: ask Cine in the above Topic to call
`cine_verify_report(scope="full", ledger_path="projects/wozaixiandai_ep1/source_shots_full.json")`.
It must reject the full-film claim. The sample call with 0 and 30 seconds should
accept only the visual snapshot scope. Model inference itself still uses model quota.

## Remaining Boundary

`accepted_visual_scope` means receipt-backed still-image inspection per shot, not
continuous viewing, audio review, motion verification, accurate character identity
or semantic truth. The tool explicitly forbids claiming complete audiovisual QA.

The canonical source metadata is an application artifact, not a tamper-proof store.
No physical read restriction or adversarial filesystem integrity guarantee is added.
Arbitrary shell-written HTML and natural-language answers can still contain false
claims or omit the acceptance tool. This release supplies an independent acceptance
result; it does not pretend those unrestricted output channels are sandboxed.

Old reports remain intact for diagnosis. No files were deleted, no WSL packages
were installed, no other Bots' workspace policy changed, and no commit was made.
