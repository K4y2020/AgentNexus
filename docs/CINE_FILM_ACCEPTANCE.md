# Cine Film Acceptance

## Objective

Deliver a watchable episode with complete story beats, consistent characters,
intelligible dialogue, intentional pacing, and an exported video. Passing JSON
validation or creating canvas cards is not evidence of finished production.
User visual approval remains required; perfect output is not guaranteed.

## Verified Baseline (2026-09-08)

- Topic: `a5a6835bd2e1e298fbf03c05e3d755c6`; project:
  `proj_99541a8a1b3d4c94b22bc6d2fe471448`. Session was idle and runner online.
- Current local storyboard: 35 segments, 73 cuts; episode cut counts 27/23/23.
  These differ from the last assistant report (28/22/23). Pin source hashes
  before accepting any generated batch; do not trust historical counts.
- V3 video-card conversion now preserves Cine segment boundaries and original
  H3 prompts. Real-file conversion yields 12/12/11 cards. The web suite passed
  73 tests and TypeScript passed. Browser creation remains unverified.
- Existing cards, media and jobs were not modified during these checks.

## Confirmed Integration Gap

`novel-storyboard/references/h3-prompt.md` emits I2VA-style sections including
`integrated_multimodal_description`. V3 `prompt-format.ts` instead requires
Ref2VA sections when image references use `reference_image` roles.

Read-only validation of the first segment of all three actual episodes with
one ordinary image reference fails with missing `subject_definitions`,
`summary`, `retention_analysis`, `detailed_description`, and a mode mismatch.
This is a reproduced contract mismatch, not proof that the remote model fails.
Actual node references, chosen workflow and outgoing payload still need review.
Do not suppress validation or silently change roles/models to make it pass.

### Live Follow-up

- One existing video job is `succeeded`:
  `job_ee5a1c80b1034b0db80e3dfb225c8b09`, on the EP1 first card.
  It used `minimax-h3-autodl-lightx2v-v5-15s`, 11 seconds, five ordinary
  image references, and Ref2VA sections. This disproves any claim that no
  video generation has ever worked. Output quality is not yet reviewed.
- The current first card retains that model but now resolves six references
  and contains I2VA sections. A live POST to `generation/validate` with its
  node ID and no input overrides returns HTTP 422 `PROMPT_FORMAT_INVALID`.
  No job was created. A historical success is not current readiness.
- Other eleven video cards have no explicit model/provider. Do not silently
  populate them from the first card or invoke an implicit fallback.
- Reference labels include both earlier character cards and the new portrait
  sheet. Whether these are visually compatible remains unverified; do not
  delete references based on labels alone.

Next: reconcile the current creative package with a provider-specific prompt
revision, retain the successful job as immutable evidence, and visually check
the referenced assets before changing bindings or authorizing generation.

## Remaining Acceptance Gates

1. Pin storyboard/script/asset revisions and reconcile visible canvas state.
2. Align the selected H3 workflow, reference semantics and skill prompt format
   against provider documentation; cover both accepted and rejected payloads.
3. Verify reference identity, ordering, availability, and duration using the
   same preparation function as submission. Dry-run does not prove quota.
4. Verify repeated card creation does not duplicate or overwrite approved cards.
5. Obtain explicit model, quantity and budget authorization before generation.
6. Generate and visually review one segment before authorizing a full episode.
7. Assemble the episode, inspect audio/video timing and story coverage, and
   deliver the real exported video with unresolved defects stated explicitly.
8. Repeat on another story before claiming the workflow is reliably reusable.

## Status Vocabulary

Keep `planned`, `cards_created`, `generated`, `visually_reviewed`, and
`film_exported` distinct. Technical validation must never imply visual review
or user acceptance. No paid generation has been submitted by this audit.
