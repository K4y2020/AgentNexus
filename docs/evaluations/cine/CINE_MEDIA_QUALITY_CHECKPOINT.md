# Cine story, performance and rhythm quality loop

Deployment and subsequent real GPT adjudication are recorded in
[`CINE_DEPLOYMENT_GPT_20261011.md`](CINE_DEPLOYMENT_GPT_20261011.md).

The media controller now supports independent audiovisual review, timed defect
localization, bounded local rework and explicit regression checks. Code and offline
integration tests are in place. Real-model trials have demonstrated useful defect
localization, but have not yet demonstrated a completed regenerate-and-improve cycle.

## Behavior

1. A plan pins actual user requirements, review-call allowance, rework rounds and
   design-edit allowance alongside generation limits.
2. The runner verifies V3 job identities and media hashes, reconstructs the assembled
   film from verified clips, and sends the real video with its soundtrack to a separate
   model context. The reviewer sees the actual job prompts and creative documents.
3. The report assesses each requirement and each item, then checks story causality and
   rhythm across the complete assembly. Defects carry item IDs, time ranges, observations,
   expectations and repair objectives. Cut IDs require actual mapped timing.
4. A generation-execution defect needs an exact source-prompt quotation before it can
   authorize a same-prompt retry. Design omissions go back to the named native stage.
   When that same item/stage already has a proven design repair, its execution fix may
   join the edit instead of pointlessly generating an intermediate version.
5. Repairs retain earlier outputs and reviews. Only affected items and dependency
   descendants are reset. Native checks and canvas synchronization precede regeneration.
   Upstream changes can affect all derived media; structural item/order changes require
   a new production revision and plan.
6. New output hashes invalidate the assembly and previous acceptance. Reinspection must
   classify every old issue as resolved, persistent or uncertain, using current evidence,
   and look for new problems. A signed current pass returns `model_reviewed`, not human
   approval. Missing evidence and uncertain defects never silently pass.

The runner signs the review with Ed25519. A workspace-authored `pass` is not accepted.
Review reservations are also stored in a signed ledger outside the Topic; deleting a
Topic summary cannot reset the allowance. The tool policy protects that trust directory;
it is not an OS sandbox against other same-user processes.

## Verification and real-model evidence

The broad regression run passed 578 tests with one existing skip. The final focused
run passed 251 tests, covering mixed-repair handling and bounded review timeout. Tests include
actual FFmpeg media, request/receipt tampering, stale bytes, cached-result races, budget
reset attempts, localized rework, continuation invalidation, native-stage edits and
the mandatory reinspection of old issues.

Live inference was bounded and did not submit any image/video generation:

- A six-second local audiovisual calibration used red then blue frames and a spoken
  random sentence absent from the prompt. The model correctly identified both the
  order and exact speech. This establishes this gateway's video/audio path, not film
  judgment accuracy. Evidence: `.codex-tmp/cine-quality-calibration/`.
- The first review of the old 63.407-second film located missing visits, omitted door
  actions and internal hard cuts. Its response failed the contract. The trial exposed
  a schema mismatch and an unsafe proposed same-prompt retry; both were corrected in code.
- The second complete-film call timed out after 180 seconds. Production review timeout
  is now bounded and configurable from 30 to 540 seconds, with a 300-second default.
  No success is inferred from timeout or a missing response.
- A final 14.375-second first-segment review identified cuts around 5.5 and 10 seconds,
  missing closing/leaving actions, and a handover that remained unfinished around
  12.2–14.375 seconds. It cited actual prompt text for the release action and supplied
  more specific vocal/behavioral observations. The initial action-combination rejection
  exposed an overly rigid contract for an execution fix joining an already-required
  design edit. After that rule was corrected, the unchanged saved response validated
  as **revise**, with whole-film rhythm still uncertain. No response content was altered
  and no extra model call was made to revalidate it.

The last report is `.codex-tmp/cine-quality-replay/segment-01/review-current.md`, backed
by `raw-report.json`, `request.json`, `invocation.json` and `validated-current.json`.
The historical attempts and their failures remain preserved. They are not evidence
that a new render improved or that the entire film passed.

## Run and acceptance

Use [`media-production.md`](../../../examples/cine/skills/film-analysis/references/media-production.md)
to add quality requirements to an authorized video plan. The existing `next` loop returns
`cine_review_media` as a tool action; save the unaltered tool receipt and resume normally.

```powershell
.\.venv\Scripts\python.exe -m pytest examples/cine/tests/test_quality_loop.py tests/seedance/test_media_quality.py tests/seedance/test_media_quality_contract.py tests/seedance/test_media_quality_provider.py tests/seedance/test_media_quality_receipts.py --import-mode=importlib -q
```

For human acceptance, compare the first segment's marked intervals against the actual
video. Then conduct an explicitly authorized bounded rework and compare old/new media:
the intended hand release and closing action must occur, hard cuts must be removed,
and the next whole-film review must address the earlier issue IDs without new regressions.

The running Cine bundle and services have not been restarted or re-registered by this
work. Deployment and a real-provider rework/reinspection acceptance run remain pending.
Subjective performance judgment still needs calibration against human review. This
checkpoint does not certify fully unattended artistic production.
