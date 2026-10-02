# Cine production handoff v1

Read this for a multi-skill production, not a standalone character or text analysis.
Default film intake is the full-scope adaptation-readiness profile: use story_plan
and the saved story draft, then cine_verify_report(scope="adaptation"). Per-shot
forensic certification is optional and never the default prerequisite for adaptation.
The existing five JSON formats and native seed/validate/render commands remain.
Do not build a second version of their schemas.

## Responsibility and modes

- Cine plans, writes and revises production artifacts. V3 is the canvas, asset
  store and generation workers. V3 Agent delegation is disabled; QA reads
  `seedance_read_canvas` snapshots.
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

## Unattended runs: production.py next

```powershell
python <film-analysis>/pipeline/production.py next <output>
```

After `init`, an automatic run asks the controller for one action at a time and
does only that. The controller walks outline → cast → art → script → storyboard,
seeds a missing stage, binds the visual people table to cast cards in
`person-map.json`, and runs each stage's check with the
advisory JEV review. A stage that passed is not rechecked until one of its inputs
changes. After writing or repairing the named file, call `next` again; do not run
separate `seed`, `attribute`, `check` or native `validate` commands during this
automatic loop. The manual commands below are for standalone work. It returns:

- `edit` — rewrite `file` with `skill` against the validator's `errors`. Each stage
  allows `max_edits` edits (authoring plus four repairs); after that, or
  after repeated calls without an edit, it escalates. The passed stages listed in
  `frozen` stay as they are: the next call restores any of them that changed, keeps
  the changed copy under `.cine-validation/reverted/` and queues a review item.
- `import_canvas` — call the given tool, save the receipt, then run
  `next --canvas-synced <receipt>`; the sync is keyed to the storyboard's hash.
  When the user wants text only, run `next --text-only` instead; the choice is
  remembered for the production.
- `escalate` — stop. A person fixes the cause; editing the named file or
  `next --retry` (for technical causes) reopens the stage with a fresh budget.
- `human_review_speakers` — stop in faithful mode when the script uses a line whose
  speaker is unresolved. `lines` contains source ids, timings, scene ids, evidence
  and reasons. Confirm the actual speaker against the source, record the resolution
  in the attributed source row (`speaker`, `speaker_name`, `confidence`,
  `speaker_needs_review: false` and a review record identifying the evidence), then
  run `next` again. Do not change text or evidence to make an uncertainty disappear.
- `done` — the current storyboard is on the canvas and no used faithful line has
  an unresolved speaker. With `delivery: text` nothing was imported: it lists the
  stage files and each validator's pass line (`receipts`) to quote. Stop there;
  finalize and production.json belong to the manual flow below.

`review-queue.json` beside the production lists blocking escalations and
review items: advisory JEV findings and unresolved evidence. Used unsettled speakers
stop faithful completion through `human_review_speakers`, even with a prior canvas
receipt. Unused unsettled lines do not stop it. Review items never trigger creative
edits. State lives in `.cine-validation/autopilot.json`.

`source-transcript-attributed.json` carries visual, present, voice and constrained
text evidence. The script's native source-order gate checks resolved speaker ids
against this same file. The additional people gate uses outline scene `from` ids:
roles speaking in a source location or appearing in its majority of shots must be
in the corresponding script scene's `characters`; extra unseen/unheard roles fail.
Faithful mode blocks, extraction queues review, and free adaptation skips. Incomplete
visual or identity evidence cannot prove absence and is queued for review. Old
30-second `scene-notes.json` descriptions are no longer used for attribution.

## Manual native stage entry

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
Use `check --stage outline|cast|art|script|storyboard` for a focused check. Add
`--jev-advisory` to run the matching TypeSafe semantic review after the native
validator and report it without blocking; `--require-jev` makes that review a
blocking gate and is used only when the user asks for a JEV-gated handoff.

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
`--jev-advisory` and `--require-jev` write a receipt under `.cine-validation/jev/`
with the actual JEV model version, input hashes, typed answers and thresholds.
In advisory mode the report's `jev_status` is `passed` or `advisory_findings` and
the stage status comes from the native validator alone. With `--require-jev`,
missing credentials, provider errors, incomplete answers, low-confidence answers
and failed judgments block the selected stage. JEV receives structured text only;
it does not replace image/audio/video inspection. `native_validated` does not authorize generation
or establish visual quality/provider compatibility. Reports are not authorization
tokens: every check re-executes the tools rather than reusing an old report.

Pass `--source-text` alongside the JEV flag so the script review judges the
adaptation against the original rather than on its own internal coherence. With
a source supplied, the script stage also runs source-comparison questions
(speaker attribution, reaction order, setup/payoff, overall order fidelity) and
records `source_compared: true` in the receipt. Without it, a script can
reshuffle or reassign the source's lines and still read as coherent, because
nothing in the state contradicts it. When `source-transcript-attributed.json`
is in use, JEV is given that cast-attributed text instead of the raw ASR labels,
so a line the attribution moved to its real speaker is not scored as unfaithful;
`source_baseline` in the receipt records the choice.

The source-comparison questions are only meaningful if the baseline is
trustworthy, so the gate first asks whether each bracketed speaker label covers
exactly one character. When that check fails the stage reports
`baseline_unusable` with `repair_scope: upstream` and does not issue a fidelity
verdict — a script must not be marked unfaithful for declining to reproduce a
transcription error. That verdict means re-run `production.py attribute` (for a
raw transcript, the `film_transcribe` tool) before re-checking; a segment mashing
several speakers into one label will otherwise mislead every downstream stage.

The storyboard stage checks in code, with the canvas importer's own rule, that
every referenced character has an `image.sheet` and every scene an `image.prompt`.
A gap reports `assets_missing` with the problem list under `asset_anchors`, the
same list `import_storyboard` would refuse with.

ASR speaker labels are unreliable in a second way: the same character appears as
`[女儿]`, `[大姐]` and `[主角]` in different lines, and a line can be credited to
the person being addressed rather than the person speaking. Run the attribution
stage before the script so the labels are resolved against the cast:

```powershell
python <film-analysis>/pipeline/production.py attribute <output>
```

It reads `source-transcript.json` and `cast.json`, binds the current visual P ids
through `person-map.json`, and writes `source-transcript-attributed.json` with
cast ids, confidence and review flags. Name captions match directly; appearance
matches use bounded observations from their own shots, co-visible identities and
confirmed captions, cached per person with those dependencies. Co-visible people
cannot map to one cast role; another costume without co-visibility remains possible.
Only remaining unresolved lines use the bounded text fallback against present roles,
declared narrators and confirmed extras or unnumbered live crowds in overlapping shots.
Crowd presence only adds an option: a protagonist's inner voice remains that person's
voice even when the shot shows a crowd. The question receives
the full ordered transcript with original ids and confirmed speakers, and overlapping
shots plus one neighbor on either side; neighbors never expand presence. `scene-notes.json`
is not used for attribution. Automatic production performs this through `next`.

Segments are grouped into exchanges by silence (or by an explicit `scene`
field). Judging the whole episode as one block measurably degrades accuracy,
because each line is then weighed against neighbours from a different exchange.
Treat any `needs_review` line as unresolved: it means JEV was not confident, or
could not tell, and a human or a stronger model should decide it.

Rows inside an opening teaser (`teaser: true` in the transcript, speaker `预告片段`;
see `opening_teaser` in `story_plan.json` and in `source-material.json`) are left
out of this file. That footage was cut in from later in the film, so its lines are
heard again where they belong; attributing them against the neighbours of a
montage is guesswork, and requiring the script to repeat them would generate the
same scene twice. They take no part in the gates below.

The script stage then reads that file automatically — `check_stage` appends
`--source <output>/source-transcript-attributed.json` when it exists — so the
script validator can compare line order, speaker and delivery against the
original instead of judging the document only on its own internal coherence.
Three gates depend on it:

- `speaker` / `source-fidelity` order check: a matched line that appears out of
  the source's order is reported as swapped. A script can reshuffle the
  original's beats and still read as internally coherent, which is exactly the
  failure this catches. Speakers are compared after resolving cast ids, names
  and aliases to the outline id. A source line whose speaker or delivery is
  flagged for review is not compared — a guess is not source fact — and the
  gate label counts those lines instead.
- `source-coverage` (faithful mode only): every source line must be in the script
  or quoted verbatim in `outline.adaptation.cut`. Lines are aligned in order by
  longest common runs, so trimmed wording still counts as kept, a heavy rewrite
  or a swapped order does not, and a refrain that occurs twice needs two lines.
  Sung lyrics and off-story audio are exempt only when JEV is confident about
  the call: a chanted slogan once came back as "lyrics" at 0.17 and would have
  been exempted. 抽核 and 借壳 skip the gate, since dropping lines is their job.
- `dialogue-causality` ghost check: a character named in an *action* beat must
  be declared in that scene's cast list. It deliberately ignores names in
  dialogue, since a line may address or discuss someone who is not present
  (「听见没？老周哥这一嗓子」 does not put 老周 in the scene). Whether one line
  follows causally from the last is a semantic question left to the JEV script
  gate (`causal_continuity`), not to keyword rules.
- `performance` (faithful mode only; run by `production.py` after the native
  validator passes): each source line carries the emotion its own frames show
  (`performance`, read by `film_transcribe`; see below). The shared visual speaker
  attribution determines whether the frame shows the speaker, including a confirmed
  anonymous `extra`. JEV asks only whether the script's `delivery` and the
  action before it keep that emotion. P(contradicts) >= 0.9 fails the stage with
  the line named; 0.6-0.9, or a line with no performance cue, goes to the review
  queue. Each line is its own small request: asked together, later judgments drifted.

Lines the script invents are not failures — adaptation may add connective
material. Only matched lines are checked, and unmatched source lines are
reported without blocking.
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

This controls AgentNexus-managed tool submissions, not an unrestricted shell,
manual V3 UI operations or separately authenticated calls to V3's own API.

## Sequence and artifacts

1. `film-analysis`: canonical source index, actual image inspection and separate
   review sidecar. Source PTS and review status never become new-film timing.
2. Write `source-material.json` for a video-derived production. This is an
   editorial index into evidence, not new evidence. Format below. Separate the
   observation from its interpretation; receipt strings alone prove nothing.
   `python <film-analysis>/pipeline/handoff.py --source-project <bound-project> --output <production>/source-material.json`
   writes this material from the committed ledger and review sidecar, as UTF-8, in
   the new output directory. Do not redirect its output (`>`, `Out-File`): on
   Windows the shell re-encodes the text and every Chinese character becomes a
   replacement character, which once left the story draft and the scene notes
   built from it unreadable while every check passed. `production.py` now refuses
   such a file, and `handoff.py` refuses a story draft that is already damaged.
   Absent reviews stay unverified. The adapter does
   not authenticate receipts or infer characters, dialogue or story facts. When
   story_plan.json exists, it also exports the matching story/<revision>.json as
   adaptation_story with source input hashes. Missing or mismatched story drafts
   cannot be replaced by an older revision. Exported story content stays unverified;
   cine_verify_report(scope="adaptation") is still required for readiness claims.
   Extracted images or indexed_unreviewed alone never mean the film was understood.
3. `cine-outline`: use reviewed material plus approved creative decisions.
   `cine-characters` and `cine-art` consume that outline, retaining its IDs.
4. `cine-script`: consume outline, cast and art; changed cast/scene/light/prop
   requirements go back upstream. Standalone drafts may omit inputs, production
   handoff cannot silently skip them.
5. `cine-storyboard`: consume the current script and asset definitions. Write
   `shot-mapping.json`: original narrative function -> adapted script beat range
   -> new cut. Allow split/merge/new cuts; do not mechanically replace nouns in
   the original shot list. A new cut has no fabricated source correspondence.
6. In manual production, run native validators with all relevant upstream arguments, then the sidecar
   checker. Hash pins detect stale files, not whether the story is good.
   Before downstream stages exist, use `handoff.py production.json --stage outline`
   or `--stage script`: only that stage's dependency closure is required, including
   source_material for video-derived work. The result is stage_inputs_validated,
   not whole-production approval. The default command still requires the full
   storyboard/mapping handoff. Do not fabricate future stages merely to run a check.
   Automatic production uses `next` for these checks. Faithful script performance
   judgments are cached per source evidence, script delivery/action, model setting
   and question; unchanged lines reuse them and failed requests are retried. Native
   checks still execute at managed submission boundaries.
7. Inspect the bound V3 canvas; synchronize only authorized changes directly
   with `seedance_edit_canvas`, using `expected_revision` for updates. Use returned
   node/asset/job IDs. Local paths are not remote reference assets. Confirm asset
   upload/reference availability before submitting generation. Missing capability
   means blocked, not a fictional upload tool or a fake asset ID.
8. QA is against an identified artifact hash/canvas revision. Report issue,
   affected ID, evidence and proposed correction; Cine performs approved fixes.
   QA uses `seedance_read_canvas` snapshots.

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

视频来源额外导出 `source_locations` 对象（`schema_version`、`source_id`、
`revision_id`、`model`、`status`、`locations`、`shots`、`errors`）。逐镜头的 `place`
是 `Lx` / `insert` / `?`；地点的 `spans` 使用视频相对秒数，区间为 `[start, end)`。
schema v3 的镜头还包含 `people: ["P1", ...]` 和必填布尔值 `crowd`。群像/反应镜头
仍列出能认出的已有编号；未能可靠编号的现场人群用 `crowd: true`，两者可同时存在。
照片或屏幕人物不算现场人群。v1/v2 视觉缓存不补猜该字段，而是按当前配置重读。
地点 `line_numbers` 从 1 开始，引用原始 `source-transcript.json.segments`（teaser 行占
原始序号但不归入地点）；`lines` 附对应原句和起止时间。台词按整句时间区间与地点
spans 的重叠时长归属，占整句时长至少 30% 的地点都包含该句。插入/未知画面的时间
仍计入整句时长；没有足够重叠或缺少有效起止时间时，地点无法判断，剧本检查进 review。
`transcript_status` 是
`matched` / `missing` / `mismatch`，只绑定指纹匹配的转写。
忠实大纲必须覆盖有台词或累计不少于 3 秒的地点，剧本台词必须放在大纲 `from`
含任一对应地点的场景；跨地点台词在两边均达到 30% 时，放在任一边都通过。
剧本拆分一条原片台词时，各句仍使用原片整句的起止时间。
抽核删除地点用 `adaptation.cut[].locations` 声明；借壳跳过。
场景表缺失/部分失败和未知台词地点进入 review 队列。
旧项目生成场景表后用 `production.py rebind-source <production> --source-project <project>`
重新导出并更新来源绑定，再补已有场景的 `from` 并运行阶段检查。
```json
{"source_id":"<canonical source>","revision_id":"<committed revision>",
 "source_fingerprint":{"size_bytes":734003200,"sha256_head":"<from source.json>","sha256_tail":"<from source.json>"},
 "shots":[
  {"shot_id":"S001","review_status":"model_reviewed","observations":["visible observation"],"image_receipt_ids":["<actual receipt>"],"unknowns":["identity","motion"]},
  {"shot_id":"S002","review_status":"unverified","observations":[],"image_receipt_ids":[],"unknowns":["image not inspected"]}
]}
```

`source_fingerprint` is copied by `handoff.py --source-project` from the analysis
project's `source.json`; never write it by hand. It is how `production.py
attribute` and `check --stage script` confirm that the ASR transcript they find
belongs to this film. While a faithful/adaptation production's material cannot
identify the film — an export from before fingerprints were recorded
(`material_without_fingerprint`) or a file whose bytes no longer match its pin
(`material_pin_mismatch`) — no transcript reaches JEV or the script validator.
Original productions and productions without `source_material` are unaffected.

### Recovering a film binding

`finalize` re-runs the checks before it refreshes pins, so it cannot repair a
binding the checks refuse. Rebind the material from the analysis project it was
exported from, then finalize:

```powershell
python <film-analysis-directory>/pipeline/production.py rebind-source <output> --source-project <analysis-project>
python <film-analysis-directory>/pipeline/production.py finalize <output>
```

`rebind-source` re-exports with the same read-only adapter and writes nothing
unless the export records the film's fingerprint and has the same `source_id`
and `revision_id` as the production's current material — a different film or
revision needs a new production. It replaces the material file and updates only
its own `sha256` pin; `finalize` then refreshes the outline and mapping pins that
still name the previous material. It runs no JEV and no media generation. Do not
edit hashes by hand.

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

## Managed generation

Submission requires the user's explicit authorization scope, retries included.
Submit runs validation itself: a separate `validate_generation` is optional for a
preflight/review request, not a loop.

- Jobs: read the exact returned job (`seedance_read_canvas` action=job, job_id) at
  its suggested polling interval; never resubmit merely because it is pending.
  Submission, output availability and visually accepted delivery are distinct.
- Completion: use V3 nodeMedia/latest_job/output_url, not node.status=draft or
  activeOutputRef alone. Missing activeOutputRef is not a repair task: V3 can
  resolve an image from assets or succeeded jobs. Never patch output references
  or guess revision numbers merely to make status look complete.
  When all requested jobs are terminal, stop polling. Review each output once
  and deliver the result, or report a specific visual/access failure. Optional
  local report rendering must not delay the primary delivery; do not loop on
  HTML link checks, guessed endpoints or repeated unchanged canvas snapshots.
- Image export: a local://hash is a V3 storage identifier, NOT a Windows file path.
  After an image job succeeds, call `seedance_edit_canvas` action=export_image with
  job_id and optional output_path inside this Topic. The tool downloads and verifies
  the image and returns its exact local path; use `sys_os_view_image` on that path.
  Never glob drives, walk user folders, scan repositories or guess localhost ports
  to locate a generated image. If export fails, report its error directly.
- Character portraits and turnaround sheets follow cine-characters
  `references/sheet.md`; scene/prop sheets follow cine-art `references/sheet.md`.

## Provider and completion boundary

Preserve narrative rhythm; configure supported duration parameters rather than
forcing every cut to 2-5 seconds. The existing `cine-storyboard export` is an H3
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
