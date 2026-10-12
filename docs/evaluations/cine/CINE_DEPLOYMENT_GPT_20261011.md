# Cine deployment — DeepSeek execution and GPT quality adjudication

Deployed and checked on October 11, 2026, America/Los_Angeles.

## Running services

- AgentNexus server on loopback port 6767; `/health` returned `status: ok`.
- Cine builtin retained ID `8dd814e968b518fa12a3d1ebda4bd892`, updated to version 49.
- Host `3f7c01db9a4f4e1f9ace892b293b8b93` is online.
- Seedance API on 8893, worker healthy and idle, Web on 5173.
- Background processes remained alive after the launching shell returned. The final
  Seedance health sample reported approximately 37 minutes of uptime.

The launch script is `U:/AI/MultiAgent/cine-deployment-20261011/start-cine.ps1`.
It sets review routing, preserves the existing databases/assets and starts missing
services. It does not install a Windows boot-time scheduled task.

## Requested model roles

The deployment test session uses `deepseek-v4.1-flash` as its executor. Existing Bot
defaults and other conversations' model selections were not changed.

Quality adjudication is explicitly `gpt-6.1-sol`, through Responses. The configured
AV observer is `gemini-3.8-flash-high`; the actual review returned observer model
`gemini-3.8-flash-n`. The observer supplied only timed visible/audible facts. GPT saw
those facts and actual timestamped frames and made the quality decision.

The receipt records `review_method: observer_assisted` and `direct_av_review: false`.
GPT's direct evidence remains text/image; the observer's video/audio coverage and
identity are separately preserved. No direct GPT audiovisual capability is implied.

The three nonsecret routing variables are forwarded from Host to runner by the
standard allowlist. Missing or unavailable models do not silently fall back.

## Real acceptance test

Session: `8bf7d2f1c8864ae7da2ceeac51610aad`.

The isolated Topic held a copy of the existing 14.375-second E01-01 video, actual
V3 job and export receipts, and its same-version generation prompt. It had a zero
rework allowance and no authorization for additional image/video generation.

DeepSeek loaded the production workflow and called `cine_review_media`. One AV
observation and one GPT review produced a signed **revise** verdict. The main findings:

- Closing the door and leaving are absent from the end of the visit.
- The source prompt explicitly requests hard cuts, conflicting with a continuous-take
  requirement. The appropriate next step is a script/storyboard edit, not blind replay.
- Handover timing has conflicting image/observer evidence, so that detail remains
  uncertain rather than being certified.

The actual final judge was `gpt-6.1-sol`, response
`resp_06c5dd9dc573172d016acb4a0fbc6487d0bb4a55bc373f1d21`.

The smoke test exposed and fixed two deployment defects: system Python had no
`agentnexus` package for receipt verification, and the model was copying/escaping a
large signed JSON response. The bundle now carries a verification-only portable
helper, tested under isolated system Python 3.10; the tool directly writes a standard
controller receipt and returns its exact path and command.

After redeployment, DeepSeek resumed the same Topic, received `reused: true`, executed
the supplied command and recorded the verified receipt. The controller returned
`quality_rework_exhausted`, as expected for the test's zero rework allowance. The
session became idle; `active` was null, one receipt was recorded, and the historical
submission count stayed at one. No new image/video jobs were submitted.

## Evidence and checking

The deployment folder contains `deployment-status.json`, `quality-result.json`,
`signed-quality-receipt.json`, `quality-report.md`, and retained session/tool traces.
The matching Topic contains the original signed report and automatically generated
controller receipt under `production/.cine-validation/media-quality/`.

To verify the result, open the test Topic in Cine, compare its report with the end of
E01-01, and confirm the last turn says cached reuse and receipt acceptance. The same
response IDs must remain on the cached review. Check `/health`, `/v1/hosts` and V3
`/readyz` after closing the launch shell; the worker must remain healthy.

This acceptance covers deployed quality judgment, signed receipt handling, cache
reuse and bounded stopping. Real re-generation and an improved-output reinspection
were not part of this test.

## Redeployment with repair fixes (October 11, 2026, 06:15)

Only the Cine server was restarted, with
`U:/AI/MultiAgent/cine-deployment-20261011/restart-cine-server.ps1`. It refreshed the
builtin agent to version 53, bundle `a032838a0716`. The existing host reconnected; V3,
its worker and Web kept running. No session was active and no runner was live.

The bundle and the runner code now include three fixes:

- An `existing_nodes` quality repair syncs only the affected cards' prompts and never
  imports the storyboard.
- Reviews receive measured hard cuts.
- Lint and the registry test are fixed.

New runners pick up the runner-side code when they start. No generation or review call
was made during this redeployment.
