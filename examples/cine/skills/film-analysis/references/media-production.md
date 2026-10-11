# Authorized media production

Use this after the user explicitly asks for image/video generation. A plan records
that authorization; writing `allowed: true` never grants it. Finish the existing
five-stage `next` workflow and synchronize the canvas first. Read the canvas with
`seedance_read_canvas` and use the actual returned node IDs, production bindings and
generation catalog models. Do not guess a model, reference, job or remote asset ID.

## Plan and resume

Write `generation-plan.json` beside the stage JSON. The example model and IDs below
are placeholders, not defaults. Set submission limits within the user's authorized
scope, including retries. For a single authorized pass without retries, set the
total to the number of items and `max_attempts_per_item` to 1. Limits count generation
submissions; they do not enforce a monetary budget or the provider's internal retries.

```json
{
  "schema_version": 1,
  "project_id": "<bound project>",
  "authorization": {
    "allowed": true,
    "scope": "<the user's actual generation request and retry scope>",
    "models": ["<authorized generation catalog model>"],
    "max_submissions": 4,
    "max_attempts_per_item": 2
  },
  "max_wait_seconds": 3600,
  "quality": {
    "requirements": [
      {"id": "story", "text": "<the user's retained events, causal changes and ending>"},
      {"id": "performance", "text": "<approved character intentions, reactions and delivery>"},
      {"id": "rhythm", "text": "<approved pacing and continuous-shot/cut requirements>"}
    ],
    "max_review_calls": 3,
    "max_rework_rounds": 2,
    "max_design_edits": 2
  },
  "items": [
    {
      "id": "E01-01",
      "kind": "video",
      "node_id": "<first actual video card>",
      "model": "<authorized generation catalog model>",
      "production_stage": "storyboard",
      "production_pointer": "/episodes/0/segments/0/h3Prompt",
      "depends_on": []
    },
    {
      "id": "E01-02",
      "kind": "video",
      "node_id": "<second actual video card>",
      "model": "<authorized generation catalog model>",
      "production_stage": "storyboard",
      "production_pointer": "/episodes/0/segments/1/h3Prompt",
      "depends_on": ["E01-01"]
    }
  ],
  "assembly": ["E01-01", "E01-02"]
}
```

Item IDs must be unique simple identifiers of at most 80 characters. Dependencies
must exist and cannot form a cycle. A segment with `continuesFrom` must depend on
its predecessor when both are in the plan. The controller checks that its tail-frame
slot belongs to the exact preceding job and output, then runs generation preflight.
Older V3 workers without tail-frame provenance must be updated; an old frame cannot
be silently reused. `assembly` is optional and gives the exact playback order.

For a local revision whose independent canvas cards already exist, set
`canvas_strategy: "existing_nodes"` on the plan. The five native stages must still
pass and every selected node still receives provider preflight before submission.
This path avoids importing the local revision into the project's full storyboard.
Do not use `--text-only` or fabricate a canvas receipt to bypass an import request.

For images, use `kind: image` and the native cast/art/storyboard image binding. New
reference images are exported for visual review before their dependent videos may
proceed. This first controller cannot authenticate a visual review or auto-approve
images: use the existing reviewed-assets workflow after inspecting them. Video runs
with already confirmed references can proceed through export and assembly.

```powershell
python <film-analysis>/pipeline/production.py next <production> --generation-plan <production>/generation-plan.json
```

Subsequent calls need only `next <production>`. Plan, input hashes, outstanding action,
request/job IDs, submission reservations and tool receipts live in
`.cine-validation/generation.json`. Changing the plan or its creative inputs stops
the run while preserving its jobs. A genuinely new scope needs a new production
revision; never change the plan or delete state to evade limits.

## Execute one returned action

- `generation_tool`: call exactly `tool` with `arguments`. Keep its request ID. Save
  `{ "action_id": "<returned action_id>", "result": <unaltered tool JSON> }` as a UTF-8
  receipt, then call `next <production> --generation-receipt <receipt>`. Do not run
  parallel commands yourself or invent successful receipts.
- `generation_wait`: wait `retry_after_seconds`, then call `next` again.
- `assemble_media`: run `next <production> --assemble-media`. The packaged assembler
  verifies current export hashes, checks compatible video/audio streams, concatenates
  without re-encoding and fully decodes the result. It does not overwrite other work.
  Incompatible clips return a specific error; do not claim they were assembled.
- `review_media`: inspect the listed outputs, including the complete soundtrack and
  motion. Report `acceptance: pending`, the actual files, known defects and unresolved
  scope. This status is never proof of adaptation, acting, voice or continuity quality.
- `generation_tool` with `tool: cine_review_media`: the runner reads the real generated
  MP4 and soundtrack in an independent context against the pinned requirements and
  creative documents. Execute the tool and record its unaltered signed envelope through
  the same receipt command. When `controller_receipt_path` is returned, use that existing
  file directly; never copy, re-escape or rewrite the signed envelope in a model output.
  Never write a verdict or substitute a local review file.
- `edit` with `quality_repair: true`: fix only the named design file using its owning
  skill and the timed defects. After each edit call `next`; it reruns native checks and
  requests a canvas reimport before generating new media. Keep segment identities and
  plan scope; a structural redesign that needs new items requires a new revision.
- `done` with `delivery: media`, `acceptance: model_reviewed`: the independent review
  passed for these exact outputs. Deliver the review receipt, actual files and known
  sampling limits. This is a model review, not a guarantee or human approval.
- `escalate`: report the exact failure and saved request/job identity. After an actual
  technical recovery, `--retry` retries the same outstanding action or queries the
  existing job. It does not reset the submission/attempt allowance. A cancelled job
  remains cancelled; a terminal failed job is retried only inside the original limits.

Submission responses can be lost after the service accepted the job. The bridge saves
a stable request, resolved generation inputs and command before dispatch, and V3
replays its idempotency key. Retrying uses that exact request. An unknown result never
authorizes a new key or another submission allowance. Changes to the inputs block
replay; preserve the recorded job for reconciliation.

## Story, performance and rhythm loop

Use quality mode for video-only batches built from reviewed reference assets. Multiple
video items must all appear in `assembly` so the reviewer can inspect the whole film's
causal flow and rhythm. Omitting quality preserves the older pending-review workflow.
Requirements must record the user's actual requests and approved creative intent; do
not weaken them to help an output pass. Avoid universal five-second cuts, constant
motion and exaggerated expressions. Motivated stillness and long takes may be right.

The result localizes defects by actual item and time range, with an observed behavior,
the expected behavior, severity, confidence and repair objective. Cut IDs are used only
when actual timing was mapped; storyboard cut timing is not proof of a generated cut.
For `generation_execution` failures, only those items and their dependent continuations
are regenerated. `source_design` failures return to the named native stage first.
Uncertain or incomplete evidence requires review rather than speculative regeneration.

Old candidates, prompts, receipts and quality decisions are retained. New output hashes
invalidate their reviews and the assembly. The next full-film review must explicitly
recheck each previous issue and look for new defects. Limits apply to all submissions,
attempts, design edits, rework rounds and paid review calls; `--retry` does not reset
them. Source changes affecting all items require enough remaining generation allowance.

A newly authorized revision can pin a prior signed review in
`quality.previous_review` with `production_dir`, `receipt_path` and `receipt_sha256`.
Both paths are relative to this same Topic; keep the old production and media immutable.
The runner checks its signature, prior snapshot, session/project and receipt hash,
requires the old requirement IDs/text to remain, and carries every prior issue into
the new regression checks. Add clarification requirements instead of weakening old
ones. A new revision does not authorize extra generation beyond its explicit plan.

The reviewer uses the configured gateway's native audiovisual API. It checks that the
selected model declares both video and audio input, sends the complete MP4 (or a
full-duration review copy preserving sound), and records source/transport hashes and
actual model metadata. Sampling at 5 fps does not certify every frame or precise lip
sync. No audio, unavailable models, truncated answers, missing criteria and low
confidence cannot be silently promoted to a complete pass. A silent film can pass when
the approved requirements call for silence.

For a configured text/image-only judge, deployment may select `observer_assisted` mode.
The AV observer reads the complete video and soundtrack and returns timed facts without
a verdict. The requested judge receives those facts plus actual timestamped frames and
makes the quality decision through Responses. Its input record remains text/image;
observer media and model identity are separate. Acceptance uses
`model_reviewed_with_observer`, never claims direct audiovisual review by the judge.
Reserve two review calls per round for this mode. An unavailable observer or judge
does not authorize a model substitution or a passing result.

The runner signs reviews; the skill verifies that signature and binds it to the current
plan, source files, jobs and media. Ed25519 keys and review reservations live outside the
Topic under the runtime's protected trust directory. Ordinary tool policy blocks direct
access; this is not OS isolation from other programs running as the same user.

This controller resumes when Cine calls `next`; it is not a background scheduler or
service supervisor. Real generation authorization, service availability and external
provider capability remain necessary.
