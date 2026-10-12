# Cine first real local repair and GPT reinspection

The deployed Cine session ran one real repair generation on October 11, 2026.
DeepSeek V4.1 authored the revision, used the managed canvas tools, submitted the
video, polled its exact job and exported it. GPT-6.1 Sol subsequently reinspected
the new output with separately recorded audiovisual observations.

## Result

- Original job: job_3f39fa44a118452f999054ef8304b0c6, 14.375 seconds.
- New job: job_032729c0d6ae4d409846217ad6d06f1b.
- Model: minimax-h3-autodl-lightx2v-v5-15s, 768p, 16:9, the original three image references.
- Submission: one new job; provider attempts: one; status: succeeded.
- New output: 1344×768, 24 fps, 362 frames, 6,920,728 bytes.
- Video stream duration: 15.083333 seconds; container/assembled duration: 15.115315 seconds.
- Output SHA-256: 20806c64d414c837b2d6e673f52bc7f97a81143b90a3cccf17d9cc7f1b6c099a.

The real GPT receipt returned **review**, not pass:

| Prior issue | Reinspection | Meaning |
| --- | --- | --- |
| I01: missing actual closure and departure | resolved, confidence 0.83 | The envelope is held by the landlord, the observer reports the door closing, and final frames face the corridor/stairs. |
| I02: hard cuts violate the continuous take | review, confidence 0.65 | Prompt changes do not prove execution. Observer evidence did not explicitly establish no cuts throughout, especially the tail. |

No new definite defect was established. The reported improvement does not establish
whole-film quality or precise lip sync. No second video was generated.

## What required operator maintenance

This was not an intervention-free run:

1. A local revision targeting an independent video card encountered the controller's
   full-storyboard import requirement. Cine attempted text-only, then imported.
   The import changed the old summary card, the EP01 table and a scene prompt binding,
   and added one derives edge. Old video nodes and media were unchanged.
2. Maintenance restored those exact metadata fields from the existing
   h3-complete-visits/canvas-after.json snapshot. All 41 prior node data objects and
   all 93 prior edges matched after restoration. The new video card and its three
   references remained.
3. The controller now supports explicit canvas_strategy=existing_nodes: all native
   checks remain and provider preflight precedes submission, without global import.
4. The existing tool required reviews to belong to the same plan. A new authorized
   revision now pins quality.previous_review and verifies the original receipt hash,
   signature, old snapshot, session/project, requirement preservation and issue IDs.
5. Review execution was interrupted to load the new baseline handling. Its two
   reserved review calls remained in the original ledger. A separate read-only
   recovery revision reused the already generated video and allowed only one
   observer-plus-GPT reinspection, with zero new generation allowance.

Cine carried out the creative and generation steps. Maintenance changed software,
restored unintended metadata and prepared the read-only recovery bookkeeping; it
did not author the revised creative JSON or submit the video.

## Evidence and reproduction

App session: 8bf7d2f1c8864ae7da2ceeac51610aad, executor deepseek-v4.1-flash.
Production revision: production-revision-01.
Read-only recovery: production-revision-01-review.

The deployment evidence directory contains:

- real-repair-job.json and real-repair-status.json;
- repair-quality-result.json, repair-quality-report.md and repair-signed-quality-receipt.json;
- restoration-receipts.json, canvas-before-restoration.json and canvas-after-restoration.json;
- review-recovery-provenance.json and retained original call ledgers.

GPT call: resp_0a7069e9a735389a016acb5e3cbd3487d0b4600688bc44793d.
Final judge: gpt-6.1-sol. AV observer: gemini-3.8-flash-n.
Review method remains observer_assisted; GPT's direct input was images and text.

For verification, play the new file through the handover, door closure and final
turn toward the corridor. Compare the signed regression_checks for I01 and I02;
do not relabel the outstanding continuous-take question as resolved. A future
evidence-only check can inspect that same existing video without another generation.

Focused software regressions passed 175 tests. Service state, receipt acceptance
and generation count were independently read back from the running application.

## Follow-up: measured hard cuts (October 11, 2026)

The I02 question was later measured offline, without a model call or generation. The
same PySceneDetect content detector and threshold (27) that cuts source films was run
over every decoded frame:

| Clip | Measured hard cuts |
| --- | --- |
| Original, job_3f39fa44a118452f999054ef8304b0c6 | 5.708 s and 10.125 s |
| Repair, job_032729c0d6ae4d409846217ad6d06f1b | none |

Frames on both sides of the two original cuts show framing jumps, matching the first
review's cuts around 5.5 and 10 seconds. FFmpeg scene scores agree: 0.13 and 0.34 at
those points against at most 0.05 elsewhere, and at most 0.039 in the repaired clip.

The signed receipt still records I02 as review; it is unchanged. The runner now measures
cuts before each review and supplies them as the item's cut IDs, so the next
reinspection can settle a continuous-take requirement from measurement.
