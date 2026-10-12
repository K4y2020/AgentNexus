# Cine media execution checkpoint — 2026-10-10

Later quality-loop work and its actual evidence are recorded in
[`CINE_MEDIA_QUALITY_CHECKPOINT.md`](CINE_MEDIA_QUALITY_CHECKPOINT.md).

The automatic controller can now continue an explicitly authorized production from
canvas cards through generation, exact-job polling, local export and optional video
assembly. Its terminal media state is `review_media`, with `acceptance: pending`.
Creative and audiovisual acceptance remain separate work.

## Implemented

- An immutable generation plan records selected nodes, models, dependencies, allowed
  submissions and attempts per item. Existing text/canvas-only delivery stays available.
- Actions and tool receipts are saved beside the production. Interrupting and rerunning
  `next` preserves the request/job identities and reserved allowance. Only a confirmed
  failed job can consume a new attempt; cancellation and unknown outcomes do not.
- The bridge journals the actual source hashes and effective generation inputs before
  submitting, then reuses V3's transactional idempotency key. Changed inputs and attempts
  to bypass an unknown request with another ID are rejected.
- Already-submitted jobs can be polled and exported even if another item has exhausted
  its allowance. Limits are submission counts, not a monetary spending cap and not a
  limit on provider-internal attempts.
- Continued segments verify the exact predecessor job and output. V3 now stamps that
  provenance on the extracted frame and checks `expectedContinuationJobId` inside the
  submission transaction. A successful new video with a failed tail-frame extraction
  cannot silently inherit the previous video's frame.
- Exported bytes must match the job's storage hash. Assembly verifies current local
  files, requires compatible streams, concatenates without re-encoding and fully
  decodes the result. Export/assembly never implies artistic approval.

## Verification

The final targeted AgentNexus/Cine run passed **353 tests**, with one existing skip.
The V3 worker/application/contracts suites passed **49 tests**; all three affected
packages passed TypeScript checks. Ruff checks and `git diff --check` passed.

Tests cover lost responses, restart, concurrency guards, reused/conflicting receipts,
input changes during preflight, request-ID bypasses, attempt limits, draining paid
jobs, corrupt state, dependency cycles, stale continuation frames, media integrity,
assembly recovery and incompatible streams. All generation API calls in these tests
are simulated.

The assembler was also run against the five existing H3 clips from the earlier
`shaoye-attempt-03` case. It produced a 63.407-second file and passed full decoding;
no new media generation was submitted. The local verification receipt is
`.codex-tmp/cine-media-assembly-check.json`. This is a reassembly of the earlier
five-clip version, not generation of the later 127-second storyboard.

```powershell
.\.venv\Scripts\python.exe -m pytest examples/cine/tests/test_generation.py examples/cine/tests/test_autopilot.py examples/cine/tests/test_media_assembly.py examples/cine/tests/test_production.py tests/seedance tests/runner/test_seedance_tool_dispatch.py tests/test_cine_bundle.py tests/tools/builtins/test_cine_skill_entrypoints.py --import-mode=importlib -q
```

For an authorized production, follow
[`media-production.md`](../../../examples/cine/skills/film-analysis/references/media-production.md).
Interrupt after a submission and rerun `next`: it must preserve the same request/job,
not create another. Change a source artifact: it must stop and retain the old jobs.
Play an assembled output through its joins and listen to its full soundtrack;
technical decoding alone is not audiovisual acceptance.

## Deployment and remaining scope

Both repositories must be deployed together: AgentNexus/Cine in `omnigent`, and the
API/contracts plus worker in the sibling `seedance-v3` repository. Re-register the
Cine bundle and restart the relevant services at a safe point. This change has not
restarted production services or replaced the currently registered runtime bundle.
Old frame slots without provenance are deliberately rejected for continuation.

The controller advances when Cine calls `next`; it does not provide an independent
background supervisor. New reference-image acceptance, independent creative review,
full audiovisual judging, quality-driven regeneration, monetary budget enforcement,
Unraid deployment and an overnight real-provider acceptance run remain outstanding.
Do not label this checkpoint as fully unattended production.
