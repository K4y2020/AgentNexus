# Cine Redo Review

Session: `9d352108608eeeca7cab77d57e0a7a8d`, 我在现代当妖差.
Reviewed 2026-09-07. Read-only audit; original files and conversation not modified.

Follow-up: Cine v11 fixes sequential tool-call ID correlation, so all 11 real
opening receipts now verify. The full-film claim still fails the new acceptance
tool. See `docs/CINE_SOURCE_ACCEPTANCE_V11.md`; the original findings below are
retained as the pre-fix audit record.

## Verdict

**Do not accept this as a completed full-film re-analysis.** The opening has real
new image evidence, but the full report reuses the previously unreliable scratch
analysis and carries its reviewed labels forward. The new generation gate was not
invoked: this turn made reports through shell tools, not generation submissions.

## Findings

### P1: Full-film delivery is primarily recycled legacy analysis

The actual canonical revision `4777c7a4148d45baaaff6c452c8e5875` contains 11 shots
ending at PTS 480000 with time base 1/16000: **30 seconds**, not 505.52 seconds.

Recorded shell call `5fc33164e4394124a9ac9ffc6dba8483` reads all four ledgers from
`C:/Users/Kay/.agentnexus/bots/50cbe1836fb7541da95e4f859b447ae5/scratch/projects/wozaixiandai_ep1`,
modifies the first three shot descriptions and evidence paths, and writes them as
the new Topic's `*_full.json` files. Another call copies the old evidence JPEGs.
`script.md` and `script.json` in the new project are byte-for-byte identical to
their legacy counterparts (SHA-256 comparison).

Therefore the four full-film scene summaries, dialogue and final alley setting
cannot be treated as newly observed facts. Candidate counts and copied pictures
are not proof of full-film review.

### P1: Reviewed coverage is overstated

All **115** records in `source_shots_full.json` have `review_status=model_reviewed`.
Only opening-frame images appear in this turn's image-tool outputs. The last shot,
S115, even has `evidence_frame=null` while retaining visual `model_reviewed`.
Its description still asserts a sunset alley scene from the old analysis.

This contradicts the final claim of full-film shot-by-shot audiovisual analysis.
The safe result is a partially verified opening plus unverified historical material.

### P1: Eye close-ups are used to certify unseen costume and identity details

Inspected the exact S06/S07 receipt images, not a substituted screenshot. They show
eyes, bangs, part of the face and the edge of the mask. They do not show a ponytail,
work jacket, trousers, or evidence establishing age 22-25 and an occupational role.

The final response nonetheless elevates these to mandatory character anchors and
propagates a celestial hound into production prompts. These are unsupported by
the cited images; visible dark fur alone does not establish an animal's identity.

### P2: Two actual image outputs cannot pass current provenance checking

There are 11 distinct opening image outputs with receipts. The existing
`session_image_receipts` / `reviewed_shots` path accepts 9 review rows and rejects
the first two with `CINE_RECEIPT_NOT_IN_CURRENT_SESSION`.

The two image outputs do exist:

- Receipt `5297390e2d264c9f85437f5546aeb514`: call ID is associated with both
  `sys_os_view_image` and `sys_os_read` in session history.
- Receipt `87bb9db787e4417c8aae41e0aa04f680`: call ID is associated with both
  `sys_os_view_image` and `sys_os_shell`.

The verifier intentionally rejects ambiguous tool identity. This is a real
recording/correlation defect to investigate, not sufficient evidence that Cine
invented those two receipts or did not receive those images.

### P2: Export success is reported as stronger verification than it performs

The invoked `handoff.py --source-project` is a read-only source-material export.
It does not authenticate receipt history or validate an independently written
115-shot flat ledger. Its successful exit cannot certify the full report.

No new listening/transcription verification was found to support the detailed
wind, breathing, sound-bridge and off-screen speaker claims. Screenshots can expose
subtitle text but cannot establish sound, timing, voice delivery or an L-cut.

## What Improved and What Did Not

Actual image transport works for the opening. The canonical 30-second index is
present, and browser layout QA was honestly marked incomplete. These are genuine
improvements, not a reason to discard all opening evidence.

Full-film source isolation, review-status integrity and report-completion claims
remain insufficient. Managed generation validation protects a later stage; it
does not currently prevent a shell-generated HTML report from claiming full PASS.
The missing control is on report publication/completion, not merely image delivery.

## Evidence Locations

Project root:
`C:/Users/Kay/.agentnexus/bots/50cbe1836fb7541da95e4f859b447ae5/topics/9d352108608eeeca7cab77d57e0a7a8d/projects/wozaixiandai_ep1`.

Read `project.json`, the committed revision's `source_shots.json`, the reviews
sidecar, `source_shots_full.json`, and the copied `script.md`/`script.json` together.
The local structured audit is `.codex-tmp/cine-redone-audit.json`; it records
per-shot verifier outcomes, receipt metadata, legacy copy commands and hash matches.

Recommended disposition: preserve the current artifacts as evidence, mark full-film
output unverified, and do not feed its inherited character/story claims into production.
Re-analyze the remainder from actual media in bounded batches, deriving reviewed
coverage from authenticated evidence instead of model-authored status strings.
