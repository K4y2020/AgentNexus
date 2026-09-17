# Cine Creative Canvas Workflow

Cine is a creative Bot: select the relevant installed skill, author creative
artifacts, and operate the Topic's Seedance V3 canvas through supported tools.
Routine production must not become source-code debugging or ad hoc script writing.
Packaged film indexing, seed, validation and rendering commands remain supported.
These are behavioral instructions and tool contracts, not an OS filesystem sandbox.

## Routine Asset Revision

1. Load the relevant skill and read the current artifact and target node once.
2. Preserve an existing portrait card unchanged. Create a separate image_prompt
   turnaround using image.sheet, set turnaroundSourceNodeId and connect the portrait
   to it with a references edge. Reuse that turnaround on retries. image.prompt is
   for portraits; both deliverables use the same node type, not the same node.
3. Reuse node.data.model or read seedance_read_canvas(action=models) and confirm
   the user's generation model once. The catalog is not proof of quota/login health.
4. submit_generation performs native validation itself. Separate preflight is
   optional, not mandatory repetition. Cast validates cast and source quotes;
   art validates cast+art; storyboard retains the complete upstream chain.
5. Read the exact returned job with action=job, following poll_after_seconds.
   Inspect the resulting image before claiming visual acceptance.

## Compatibility and Failure Handling

V3 owns generation preparation through prepareCanvasGeneration, shared by dry-run
validation and generation.submit. Node settings and omitted references resolve in
V3 using its existing canonical resolver; explicit UI reference selections remain
supported. A turnaroundSourceNodeId requires the corresponding portrait image.
AgentNexus only validates native creative artifacts, checks authorization and
forwards node ID, validated prompt and explicit user overrides. expectedPrompt is
checked in V3 at submission so edited nodes cannot silently replace that artifact.
Provider capabilities and image reference limits are enforced inside V3.

- Existing canonical files win. Otherwise a single *-stage.json is read in place;
  ambiguous/missing files are reported without copying, renaming or inventing data.
- Model selection errors return choices before submission. Other native failures
  return the affected stage and compact diagnostics, not instructions to debug code.
- One targeted correction is appropriate; repeated identical failures or missing
  capabilities should stop technical exploration and be reported to the user.
- Native validation, authorization, submitted jobs, available media and reviewed
  deliverables are distinct states. Local report placeholders are not canvas state.

## Verification

Python regression tests cover legacy cast filenames without outline creation,
source/prompt/freshness checks, authorization, model selection, exact job receipts,
and cross-project job access rejection. V3 exposes its existing generation catalog
through a read-only endpoint; API tests and typechecking cover that addition.
Live read/preflight against the current Topic returned the model catalog and a
model-selection diagnostic without submitting generation. Creative model behavior
and new generated image quality still require a subsequent user task.
