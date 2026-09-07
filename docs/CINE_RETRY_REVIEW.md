# Cine Retry Review

Session: `8281c6313d05ace580faba4945107375`.
Current revision: `e8dd47c09b0d4f35b33139208f36bfa4`.

## Conclusion

Material improvement over the recycled full report, but still a partially verified
draft, not a completed eight-minute shot-by-shot audiovisual analysis.
This review did not change the session or its artifacts.

## Confirmed Improvements

- A real full-range pipeline run created 131 candidate shot intervals and 520
  evidence records. The inspected history contains the full-range `--resume` call;
  no scratch-copy commands were found in this session's complete 272-item history.
- The current review sidecar has 23 model-reviewed rows and 108 unverified rows.
  All 23 reviewed rows pass current-session receipt/source/revision linkage checks.
- The final answer explicitly calls the deliverable an unverified draft and admits
  that it cannot claim full audiovisual PASS.
- Spot-checked the actual referenced frames for the capybara bath, rooftop ending
  and final credit. Their principal visible content supports those observations.
  These are not the previous invented sunset-alley ending.

## Remaining Findings

1. The request to finish the full eight minutes is not fulfilled: 108 current-revision
   shot intervals remain unverified. The previous 0-30s acceptance belongs to revision
   `2213b32a55044ff28e88530d14c394d6`, not automatically to the new full revision.
2. The stated rejection reason is inaccurate. Both recorded full-scope calls returned
   `CINE_SHOT_OUTSIDE_SOURCE`, not a coverage-count result. Source duration is
   8,087,999 PTS; the last interval ends at 8,088,000 PTS, a one-tick overrun
   (1/16000 second, 62.5 microseconds). Fix the pipeline's terminal bound rather
   than describing this as a different validation failure. Correcting this alone
   would still leave the 108 unverified rows.
3. Narrative chapter labels and ordinal shot ranges do not consistently match:
   the report labels S01-S30 as 0-125s, but interval 30 ends at 94.0416875s.
   It labels S31-S70 as 125-255s, while the actual range is 94.0416875-275.5416875s.
   These mappings should be derived from the ledger, not freely composed.
4. Audio and continuous camera movement remain unverified. The prose still makes
   concrete sound-effect/voice and motion assertions in places despite the closing
   disclaimer. Those passages need per-claim interpretation/unverified labels or
   actual clip/audio inspection.

## Disposition

Keep the new indexing and receipt-backed observations; no need to discard them and
start from scratch. Correct the one-PTS boundary, derive chapter ranges from actual
shot records, then finish the remaining review in bounded batches. The current
document is useful for understanding the broad narrative, not yet an authoritative
shot/audio reference for faithful production.

Evidence: `.codex-tmp/cine-retry-audit.json` contains the per-shot checks and selected
image paths. Full-scope error result item IDs are `bd6e3f1eef2744ca8fb945a7392ad2d4`
and `a14323e1cc2a457097e86bce09a0c353`.
