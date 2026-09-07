# Cine v10 Deployment Acceptance

2026-09-07, 18:38 Asia/Shanghai.

## Deployed

The prior running bundle was v9. AgentNexus was restarted while no sessions were
running/waiting and no questions were pending. The same Cine ID
`7236cdb3ad73464b947752d732dcd566` is now registered as **v10**.
The host daemon started successfully and an actual Cine turn loaded the new bundle.

AgentNexus's managed `seedance_edit_canvas` generation path now:

- Resolves the current session workspace from the server, not a model-supplied root.
- Rejects cross-workspace file paths and a different bound V3 project.
- Re-runs native validators from the registered skill bundle, never a script path
  supplied in tool arguments and never a prior PASS report.
- Requires the submitted prompt to match an allowed field in the validated artifact.
- Checks input hashes again before submission, including after a node snapshot check.
- Keeps explicit generation authorization separate from native validation.

Image production validates the relevant upstream chain: outline/cast for character
images, outline/cast/art for art assets, all five artifacts for storyboard images
and videos. Missing quote source, failed validation or missing capabilities blocks
submission. Native validation does not establish visual quality or provider support.

`validate_generation` exercises the same native gate without submitting a V3 command.
`initialize` performs project/session binding without invoking the V3 Agent.
The old `seedance_agent_message` endpoint now returns
`CINE_AGENT_DELEGATION_DISABLED` before any V3 call, even for draft requests.
This closes the alternative AgentNexus delegation route; it does not implement a
read-only V3 Agent. QA uses read-only canvas snapshots.

## Live Verification

Test Topic: http://127.0.0.1:6767/c/581d04e5a5cd91ec17abd0223af0e855

After deployment, Cine performed exactly the requested skill load and two direct
validation calls, then returned to idle. No media was generated or uploaded, no
canvas content was changed, and no paid generation was submitted.

| Fixture | Actual tool result |
| --- | --- |
| `gate-good-1837`, cast image | `validated`, `submitted=false`; outline and cast exited 0 |
| `gate-bad-1837`, cast image | `rejected`, `CINE_NATIVE_VALIDATION_FAILED`; cast exited 1: missing persona |

The bad fixture was a copy of the valid one with one required field removed. It
also contained old successful reports; those did not override the new failure.
The recorded command paths resolve to a runner-owned `...-v10/skills/...` bundle.

Live item IDs:

- Good tool result: `1adf11efbc034cb58f3a1926997b1761`.
- Bad tool result: `b13412025d294ff886a0c4bfbced2a5a`.
- Cine final response: `97815ea73fe943f18e5faffc000088a8`.

Local raw trace: `.codex-tmp/cine-live-20260907/trace.json`.
The recorded route requests `gemini-3.8-flash-high`, resolves `gemini-3.8-flash`,
and still reports upstream identity unavailable. This test does not certify the
gateway's ultimate upstream model identity.

## Regression Tests

- Cine suite: **117 passed**.
- Seedance bridge/gate, runner dispatch, Cine bundle, skill reader/loader: **72 passed**.
- Dedicated tests cover failed native data before V3 submission, no-generation
  validation, successful submission against a mocked V3, changed files, unrelated
  prompts, cross-Topic/project paths and rejection of legacy delegation.

For a manual no-cost-to-generation check in the test Topic, call
`seedance_edit_canvas` with `action=validate_generation`, `generation_kind=image`,
`production_stage=cast`, `production_pointer=/characters/0/image/sheet`, and
`production_dir=gate-bad-1837`, `source_text=gate-bad-1837/source.txt`.
It must reject rather than submit. Skill/model inference itself may use model quota.

## Boundary

These controls cover AgentNexus-managed Seedance tool submissions. They are not an
OS sandbox and do not intercept manual V3 UI actions or separately authenticated
V3 API calls from arbitrary shell code. They also do not force a model to author a
correct story: invalid artifacts now stop before managed generation instead of
being accepted because an assistant said PASS.

No successful paid image/video generation was tested. Provider-specific H3/Seedance
conversion, real asset-upload/reference checks and creative quality remain separate
acceptance requirements. This update was deployed but not committed or pushed.
