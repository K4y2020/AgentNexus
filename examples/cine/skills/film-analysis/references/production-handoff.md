# Cine production handoff v1

Read this for a multi-skill production, not a standalone character or text analysis.
Default film intake is the full-scope adaptation-readiness profile: use story_plan
and the saved story draft, then cine_verify_report(scope="adaptation"). Per-shot
forensic certification is optional and never the default prerequisite for adaptation.
The existing five JSON formats and native seed/validate/render commands remain.
Do not build a second version of their schemas.

## Responsibility and modes

- Cine plans, writes and revises production artifacts. V3 is the canvas, asset
  store and generation workers. The V3 Agent is a QA role, not another author.
- `analysis`: original evidence only; stop before the novel production stages.
- `faithful`: preserve verified source facts; unknown identity, dialogue and
  motion stay unknown. Missing evidence blocks source-faithful claims.
- `adaptation`: user-selected changes are creative decisions, not source facts.
- `original`: no source-shot claims; do not invent source IDs or image receipts.

Record the user's mode, scope, style and target duration once. Do not ask again
when already supplied. If adaptation degree is ambiguous, ask before rewriting;
never silently select extraction/reinvention. A text-only original story does
not need video analysis. Short-drama hook gates are not universal film rules.

Block the affected stage, not the whole task. Missing source evidence blocks
source-faithful claims, not an explicitly authorized original/adapted draft.
Offer the usable draft now and list which claims or submissions remain blocked.
Do not quietly erase a source correspondence to gain approval; an independently
invented beat may instead be labeled new with its creative decision made explicit.
On a review-only request, return findings and a proposed patch, not unsolicited
rewrites. Missing historical hashes do not permanently invalidate a project:
reconcile against today's approved inputs, review descendants, then establish pins.

## Native stage entry

Routine canvas production is skill-led, not software repair. Read/write the native
creative artifacts directly; do not generate ad hoc bridge/repair scripts or inspect
service repositories. Use seedance_read_canvas action=models/job for the generation
catalog and exact job state. Seedance submit_generation validates automatically.
Cast image delivery validates cast plus source text, not episode/outline gates;
art validates cast+art; storyboard delivery retains the complete upstream chain.
All native seed/check/submission entries use the same artifact resolver. If
production.json exists, artifacts.<stage>.path selects that stage's relative file
inside the production directory. A missing/invalid binding never falls back to
another filename. Without a manifest, exactly one canonical or *-<stage>.json file
is accepted; canonical plus a named version is ambiguous too. Do not rename/copy
files to force selection. Ambiguity requires an explicit version choice, never
invented upstream content. Technical failures remain technical failures.

Use the real directory returned by `load_skill` for `<film-analysis>` below.
Do not search the whole disk or read entire validator implementations to discover
their CLI. Keep native schemas; fill seeds rather than replacing them with
`meta/shots` or `meta/scenes` formats.

```powershell
python <film-analysis>/pipeline/production.py init <output> --source <title> --episodes 1 --seconds 30 --genre <genre> --adapt-mode <mode>
# Native adapt-mode is one of 忠实 / 抽核 / 借壳; use the user's choice.
# Fill outline.json, then seed its consumers:
python <film-analysis>/pipeline/production.py seed <output> --stage cast
python <film-analysis>/pipeline/production.py seed <output> --stage art
python <film-analysis>/pipeline/production.py seed <output> --stage script
# Fill script before seeding the storyboard:
python <film-analysis>/pipeline/production.py seed <output> --stage storyboard
python <film-analysis>/pipeline/production.py check <output> --source-text <source.txt>
python <film-analysis>/pipeline/production.py finalize <output> --source-text <source-transcript.txt>
```

If the script changes after an untouched storyboard seed was created, rerun the
storyboard seed with `--replace-empty`. It replaces only a storyboard whose every
`segments` list is still empty; authored storyboard content remains protected.

`init` creates an incomplete outline with no invented characters or observations.
Seeds retain upstream IDs through existing Node scripts and never overwrite
an existing canonical, named or explicitly bound output. With a manifest, declare
the target path before seeding; seeding does not update hashes or approval pins.
Use a new production revision directory for replacement seeds.
Use `check --stage outline|cast|art|script|storyboard` for a focused check.

`check` executes native validators with relevant upstream inputs. `finalize` is
the only routine path that refreshes artifact hashes and dependency pins: it
first reruns all five native validators, then rewrites the complete pin graph and
executes the handoff checker. Never hand-edit hashes after a revision. Unique reports
and a `latest.json` convenience copy under `<output>/.cine-validation/` record
commands, input hashes, validator hash, stdout/stderr and actual exit code.
Changed inputs, missing tools, timeouts, bad shapes and native failures cannot
pass. A printed PASS is never parsed as approval. Missing source text yields
`incomplete` for cast quote checks; use actual source or approved original treatment,
not invented original-film dialogue. Never manufacture `inputs/source.txt` from a
story draft or sparse screenshots and label it a transcript. Store machine speech
recognition as `inputs/source-transcript.txt` with `dialogue_provenance.status=asr`;
store a newly written adaptation treatment under a clearly different name and label.
`native_validated` does not authorize generation
or establish visual quality/provider compatibility. Reports are not authorization
tokens: every check re-executes the tools rather than reusing an old report.
The native report fingerprints production.json, including its absence. Changing
or adding bindings during validation invalidates the submission proof. Path
selection does not authenticate the manifest's upstream pins: use the stage
handoff check below as well. Native validation does not establish source reading
or adaptation quality.

The AgentNexus direct generation bridge now re-executes native checks before
submitting. Supply `production_dir`, `source_text`, `production_stage` and
`production_pointer` to `seedance_edit_canvas`. For review-only/preflight requests,
use `action=validate_generation`; authorized submission already runs the gate. Example for
a character image: stage `cast`, pointer `/characters/0/image/sheet`, kind `image`.
For a video use stage `storyboard` and `/episodes/0/segments/0/h3Prompt`; for its
keyframe use `/episodes/0/segments/0/cuts/0/frame`, kind `image`. Submitted prompt
must exactly match the selected field; files must stay in this Topic workspace.
Cast images validate cast/source text, art validates cast/art, and storyboards check
all five artifacts. No episode gates are required for a character image revision.
Native validation still does not prove provider compatibility or visual quality.
Legacy `seedance_agent_message` is disabled, including draft requests, because
the generic V3 Agent has no read-only enforcement. Use `initialize` on the direct
canvas tool for binding and `seedance_read_canvas` for snapshot QA.

This controls AgentNexus-managed tool submissions, not an unrestricted shell,
manual V3 UI operations or separately authenticated calls to V3's own API.

## Sequence and artifacts

1. `film-analysis`: canonical source index, actual image inspection and separate
   review sidecar. Source PTS and review status never become new-film timing.
2. Write `source-material.json` for a video-derived production. This is an
   editorial index into evidence, not new evidence. Format below. Separate the
   observation from its interpretation; receipt strings alone prove nothing.
   `python <film-analysis>/pipeline/handoff.py --source-project <bound-project>`
   prints this material from the committed ledger and review sidecar. Capture it
   in the new output directory; absent reviews stay unverified. The adapter does
   not authenticate receipts or infer characters, dialogue or story facts. When
   story_plan.json exists, it also exports the matching story/<revision>.json as
   adaptation_story with source input hashes. Missing or mismatched story drafts
   cannot be replaced by an older revision. Exported story content stays unverified;
   cine_verify_report(scope="adaptation") is still required for readiness claims.
   Extracted images or indexed_unreviewed alone never mean the film was understood.
3. `novel-outline`: use reviewed material plus approved creative decisions.
   `novel-characters` and `novel-art` consume that outline, retaining its IDs.
4. `novel-script`: consume outline, cast and art; changed cast/scene/light/prop
   requirements go back upstream. Standalone drafts may omit inputs, production
   handoff cannot silently skip them.
5. `novel-storyboard`: consume the current script and asset definitions. Write
   `shot-mapping.json`: original narrative function -> adapted script beat range
   -> new cut. Allow split/merge/new cuts; do not mechanically replace nouns in
   the original shot list. A new cut has no fabricated source correspondence.
6. Run native validators with all relevant upstream arguments, then the sidecar
   checker. Hash pins detect stale files, not whether the story is good.
   Before downstream stages exist, use `handoff.py production.json --stage outline`
   or `--stage script`: only that stage's dependency closure is required, including
   source_material for video-derived work. The result is stage_inputs_validated,
   not whole-production approval. The default command still requires the full
   storyboard/mapping handoff. Do not fabricate future stages merely to run a check.
7. Inspect the bound V3 canvas; synchronize only authorized changes directly
   with `seedance_edit_canvas`, using `expected_revision` for updates. Use returned
   node/asset/job IDs. Local paths are not remote reference assets. Confirm asset
   upload/reference availability before submitting generation. Missing capability
   means blocked, not a fictional upload tool or a fake asset ID.
8. QA is against an identified artifact hash/canvas revision. Report issue,
   affected ID, evidence and proposed correction; Cine performs approved fixes.
   Until a read-only V3 Agent endpoint is enforced, Cine uses read-only snapshots
   for QA. Do not call the generic mutating Agent endpoint and call it sandboxed.

## Sidecar contract

Empty `unreviewed`/`unverified` review rows are normalized to unverified during
read-only source export. Rows contradicting that status with observations or
receipts fail explicitly; no source review file is rewritten or promoted.

Keep `production.json` beside the artifacts in a new production output directory.
Use actual SHA-256 of file bytes, never a model-invented checksum. Paths are relative
to this directory and cannot escape it. Each derived artifact pins all direct
inputs when authored/reviewed. Do not refresh pins just to silence a stale error:
review/regenerate affected descendants, run validators, then update the pins.

```json
{
  "schema_version": 1,
  "mode": "adaptation",
  "artifacts": {
    "source_material": {"path":"source-material.json","sha256":"<actual hash>","inputs":{}},
    "outline": {"path":"outline.json","sha256":"<actual hash>","inputs":{"source_material":"<actual hash>"}},
    "cast": {"path":"cast.json","sha256":"<actual hash>","inputs":{"outline":"<actual hash>"}},
    "art": {"path":"art.json","sha256":"<actual hash>","inputs":{"outline":"<actual hash>"}},
    "script": {"path":"script.json","sha256":"<actual hash>","inputs":{"outline":"<actual hash>","cast":"<actual hash>","art":"<actual hash>"}},
    "storyboard": {"path":"storyboard.json","sha256":"<actual hash>","inputs":{"script":"<actual hash>","cast":"<actual hash>","art":"<actual hash>"}},
    "mapping": {"path":"shot-mapping.json","sha256":"<actual hash>","inputs":{"source_material":"<actual hash>","script":"<actual hash>","storyboard":"<actual hash>"}}
  }
}
```

For `original`, omit source_material and its input pins; mapping is still required
and every cut is `new`. `analysis` uses the canonical pipeline, not this checker.

`source-material.json`:
```json
{"source_id":"<canonical source>","revision_id":"<committed revision>","shots":[
  {"shot_id":"S001","review_status":"model_reviewed","observations":["visible observation"],"image_receipt_ids":["<actual receipt>"],"unknowns":["identity","motion"]},
  {"shot_id":"S002","review_status":"unverified","observations":[],"image_receipt_ids":[],"unknowns":["image not inspected"]}
]}
```

`shot-mapping.json` is a list, one row per storyboard cut. `scene_index`, `cut`
and beat endpoints are 1-based, matching existing storyboard/script conventions:
```json
[{"ep":1,"segment_id":"E01-01","cut":1,"scene_index":1,"beats":[1,2],
  "kind":"adapted","source_shot_ids":["S001"],
  "narrative_function":"notice a threat","creative_change":"doorway becomes an airlock"}]
```

Use `kind=retained|adapted|new`. Retained needs reviewed source; adapted may cite
unverified source as inspiration with a recorded warning, without blocking handoff.
Do not call that inspiration verified source fact. To create independently, use `new`, no source IDs and an explicit creative
decision, never present the result as a verified reconstruction. Faithful mode
allows only retained rows. Mapping covers every cut exactly once and its beat
range must equal the cut and refer to existing script beats.

```powershell
python <film-analysis-directory>/pipeline/handoff.py <output>/production.json
```

Success is `handoff_validated`, **not production PASS or authorization**. The
checker checks hashes, dependency pins and mapping, not natural-language truth,
asset presence, all five native schemas or generation provider compatibility.
Runtime source-shot receipt checks still apply; this sidecar cannot bypass them.

## Provider and completion boundary

Preserve narrative rhythm; configure supported duration parameters rather than
forcing every cut to 2-5 seconds. The existing `novel-storyboard export` is an H3
exporter; it does not certify Seedance compatibility. Do not forge H3 fields to
make a Seedance-only draft pass. Report the missing provider adapter separately.
Still deliver a provider-neutral plan: duration, continuous/cut structure,
prompt, reference local path, remote asset ID (null until available), and pending
checks. Label it planning-only, not an executable API payload. Do not force the
user through incompatible H3 gates just to obtain a useful creative draft.
Preserve supplied audio intent: no dialogue is not necessarily no ambience;
explicit silence means no invented sound. Timing estimates are not measured output.
Generation authorization, provider success, actual returned media and visual QA
are separate states. A prompt pack or empty placeholder is not generated media.

Do not propagate unknown identities as confirmed characters, relabel invented
dialogue as transcription, refresh stale hashes without review, or call a
validator PASS a full-film/visual/production PASS.
