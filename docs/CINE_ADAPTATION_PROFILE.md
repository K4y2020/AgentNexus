# Cine Adaptation-First Profile

Deployed: Cine v12, 2026-09-07.

## Default Workflow

The default is now whole-film preparation for adaptation, not exhaustive forensic
shot certification. Omit `--end` to index the whole source; use
`--profile forensic` for the previous detailed auditing workflow.

The pipeline creates a temporal story plan with three representative image seeks
per 30-second batch, including the final batch. Sampling is independent of cut
density. If cut detection is unavailable, temporal sampling can still proceed with
an explicit warning; it does not invent cut candidates. Missing usable media or
images is still reported, not replaced with fictional observations.

Cine fills `<project>/story/<revision>.json` batch by batch: events, connection to
adjacent events, actual image receipts and uncertainties. It finishes the main
characters/relationships, conflict, turning points and ending before delivery.
Repeated batch approval questions are removed from the default instructions.

Unchanged `--resume` reuses the current adaptation revision and saved story draft.
A changed source is rejected; a changed scope creates a new revision. The source
end is clamped in integer PTS, fixing the prior one-tick overrun.

## Acceptance

`cine_verify_report(scope="adaptation")` checks temporal coverage, current source/
revision identity, representative image evidence for every batch, story fields and
the ending. It does not require every original shot to be reviewed.

- Complete working brief: `ready_for_adaptation`.
- Missing batches, ending or usable evidence: `needs_story_completion`, with issues.
- Noncritical uncertainties remain warnings, not an approval barrier.
- Time windows and reference shots come from the plan, not hand-written ordinal maps.
- `accuracy_percent` stays null. No automatic metric claims 80% semantic accuracy.

Original plot understanding and proposed adaptation remain separate. New storyboards
serve the adapted script. Optional adapted source-shot references may be unverified
inspiration with warnings; explicitly retained/faithful source claims remain strict.
Native production JSON validation, prompt binding and generation authorization are
unchanged. This does not add H3-to-Seedance provider conversion.

## Verified

- Actual ~505.50-second source: 131 candidate intervals, **17 story batches / 51
  representative images**, instead of the earlier 520 boundary evidence artifacts.
- Terminal source PTS is now capped at 8,087,999; no 8,088,000 overrun.
- Actual unchanged resume reused revision `cb84dcde01a545c9bb65e2a88b6bc8a4`.
- Cine tests: **124 passed**. Source/Seedance/dispatch/bundle/skill tests: **87 passed**.
- Regressions cover whole-film default, ending sampling, missing detectors/images,
  saved progress, changed source, missing story sections/ending, stale receipts,
  and permissive adaptation versus strict retained-source references.
- Targeted Ruff checks passed. Cine was registered as v12 after an idle-service restart.

The real smoke run tested indexing, sampling and resume, not a new model-generated
story brief or a measured creative-quality A/B. No old user project was overwritten,
no paid media generation occurred, and Windows/WSL execution policy was not changed.

## Try It

In a new Cine Topic, supply a video and say: “按改编用途完成全片剧情梳理，
给出人物关系、主要转折、结局和参考镜头，疑点标注，不需要逐镜精确复刻。”
It should use the full adaptation profile and save progress across all batches,
not stop at the first 30 seconds to ask whether to continue.

Approximate cuts are allowed. Temporal coverage and filled fields cannot guarantee
that every plot detail was understood; use transcripts/subtitles when available
and keep important uncertainties explicit. Existing model/SDK interruptions are
not claimed to have been eliminated by this workflow change.
