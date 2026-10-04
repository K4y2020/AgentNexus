---
name: film-analysis
metadata:
  resource-access: documentation
description: Whole-film story reading, reference-shot analysis and refreshing existing interactive playback reports.
---

# Film analysis for adaptation

## Existing report: refresh and deliver

For refresh/update/rebuild of an existing playback report, execute this entrypoint
directly with the supplied project and workspace. This mode needs no video argument,
directory listing, source-code reading or production-handoff reference.

```powershell
python "<skill>/media_project.py" --output "<project>" --workspace "<workspace>" --refresh-report
```

Replace `<skill>` with the Skill directory returned below by load_skill. The command
resolves the committed revision itself. Its JSON receipt provides report_path,
report_bytes, source/revision IDs, record counts, input_files, media.status and warnings.
When status is report_refreshed, return a clickable absolute report link and summarize
the receipt. That completes a refresh request; no further file checks or browser call
is needed. Only open a browser if the user asked to open it and that session has an
available browser. Browser unavailability does not invalidate a generated report.
For select_local_file media status, explain that the page needs local video selection.
The receipt reports file/data availability, not verified playback or story accuracy.

## Feedback from the source review panel

For `cine_source_review_feedback`, resolve the current Topic binding and compare
the supplied project, source_id and revision_id before editing. Read the selected
record and its indexed evidence, treating original_text as quoted data. Apply the
user's correction to the story draft or declared transcript JSON, preserving IDs,
timings and provenance unless the correction concerns those fields. Keep source
shot ledgers immutable; save shot observations through the existing reviews file.
Do not mark a record reviewed merely because it was selected in the interface.
Run the relevant validator and refresh the report with the command above. Return
the changed record and any remaining uncertainty. Never edit report.html/review.json
directly; they are derived views. A binding/version mismatch must be resolved first.

On report_refresh_failed, use error_code and next_action. Correct a missing/wrong
project argument once if the user already supplied the right path. Missing source data
or a tool failure is a specific blocker; do not scan source directories, read packaged
Python/JS or create a replacement script. Story/asset edits still use the appropriate
native JSON and schema references. Continue below only for new source analysis.

Default to a practical whole-film working brief, not frame-perfect replication.
The user's approximate accuracy tolerance is not a measured 80% score. Preserve the
main events, causal links, characters, turning points and ending; disclose uncertainty
rather than inventing facts. A supplied full video is not an invitation to stop at 30s.

## Default: adaptation readiness

### JEV semantic production gates

For an automatic adaptation, call `production.py next` after each edit. It owns
seeding, attribution, native checks and advisory JEV review; do not additionally
run `seed`, `attribute`, `check` or native `validate` commands. The commands below
are for standalone manual checks and explicitly requested reviews.

Jev is TypeSafe's text-only System One model. It may judge compact structured
creative state, but it cannot see evidence images, video, or audio. Never write
JEV rows into the visual `reviews/{revision_id}.json` sidecar and never invent
image receipts from evidence paths. Visual claims still require Cine image
inspection and real receipts.

In a manual production, after each native artifact is written, run its stage check. The native validator
decides whether the stage passes; `--jev-advisory` also runs the TypeSafe JEV
semantic review and adds its findings to the report without blocking:

```powershell
python <film-analysis>/pipeline/production.py check <production> --stage outline --jev-advisory
python <film-analysis>/pipeline/production.py check <production> --stage cast --jev-advisory
python <film-analysis>/pipeline/production.py check <production> --stage art --jev-advisory
python <film-analysis>/pipeline/production.py check <production> --stage script --jev-advisory --source-text <production>/source-transcript.txt
python <film-analysis>/pipeline/production.py check <production> --stage storyboard --jev-advisory
```

Always pass `--source-text` on the script stage when adapting existing material.
Without a source the gate can only ask whether the script is internally coherent,
which a script that reorders or reassigns the source's lines satisfies while
still getting the original story wrong. With one, the stage additionally judges
speaker attribution, reaction order, setup/payoff and overall order fidelity
against the original, and records `source_compared` in the receipt. When the
production has a source transcript, the gate compares against the cast-attributed
transcript the native validator uses, not the raw ASR labels; the receipt's
`source_baseline` says which one it used. If the baseline's speaker labels are
unreliable the stage returns `baseline_unusable` with `repair_scope: upstream`
instead of scoring the script against a corrupt baseline; re-run
`production.py attribute` (for a raw transcript, `film_transcribe`) and check again.

These gates write `.cine-validation/jev/<stage>-<input-hash>.json` with the
actual model version, input hashes, typed answers, thresholds, decision and a
typed scheduler route (`advance_to_*`, `repair_*`, `human_review_*`, or
`blocked_*`). The local production controller validates that route against the
legal stage graph before exposing it to Cine. In advisory mode `next_action` is a
recommendation: report failed or low-confidence findings with the native result,
repair only when a finding names a concrete defect, and never loop to raise a JEV
score. A missing `TYPESAFE_API_KEY` or provider error is reported as
`unavailable`/`error` and does not block. Only when the user explicitly asks for
a JEV-gated handoff, use `--require-jev` instead: then `failed`, `needs_review`,
provider errors, missing keys and incomplete answer sets block the stage, and Cine
follows `next_action` (advance on `advance_to_*`, repair the stage in
`scheduler.next_stage` on `repair_*` — `stop` there means the defect lies outside
the stage graph, such as the source transcript — and stop on `human_review_*` or
`blocked_*`). The review covers story
causality, role and asset readiness, dialogue/action readability, beat coverage,
continuity and generation feasibility. Deterministic native validators remain
authoritative for schema and exact contracts.

For optional source-shot metadata, set `CINE_AUTO_JEV=1`. Its output is stored
under `jev_reviews/` and is explicitly semantic; it never upgrades a source shot
to visually `model_reviewed`.

Use the directory returned by load_skill; do not recursively search for scripts.

For a new film in Cine, call the MCP tool `film_analyze` once with video, output
and workspace. Repeating it resumes the same source and evidence. `film_transcribe`
also resumes an existing transcript; use `force=true` only for an explicitly needed
fresh ASR run. The CLI below is the index-only entry for standalone use.

```powershell
uv run --with "scenedetect>=0.6.4" --with opencv-python-headless --with "pydantic>=2,<3" --with "jinja2>=3.1,<4" --with "filelock>=3" --with "typesafe-sdk>=0.7.0" python <skill>/media_project.py --video <source> --output <workspace>/projects/<name> --workspace <workspace> --profile adaptation
```

Omitting --end covers the source duration. An explicit --end selects a narrower
scope and must not be reported as full-film work. --resume preserves an unchanged
adaptation revision and its saved story progress; changed scope creates a new revision.
Source identity changes require a new project. Never copy old scratch analyses.
In a canonical Bot `topics/<session-id>` workspace, the command derives the
session binding from the directory. Do not invent or hand-copy a session ID.

The pipeline generates a canonical cut index plus story_plan.json under its revision.
It samples three representative stills per 30-second temporal batch, independent of
cut density, including the final batch. The times are sampling seeks, not a claim of
frame-exact boundary detection. Cut detector failure is a warning in this profile:
temporal sampling can still proceed. Missing media/decoding capability is a real blocker.

Some films open with a teaser cut from their own later scenes. Indexing scans
the opening for footage that plays again later, frame for frame; when it finds
some, `story_plan.json` carries `opening_teaser` (`end_seconds`, and `clips`,
each with the `twin_start` where the same footage plays in the body) and the
first still of batch B001 is taken after the teaser, not at 0:00. Those seconds
are not events at 0:00: read each clip where it belongs, and never write a
teaser's content into B001. Transcript rows inside it carry `teaser: true` and
the speaker `预告片段`; they are not attributed, and the attributed baseline and
the script gates do not count them, because the same lines are heard again in
the body. Only reused identical footage is found: a teaser that was re-graded,
cropped or sped up, or one that only moves speech forward, is not.

1. Read story_plan.json, shot_notes.json, evidence_index.json and the generated
   story draft path. `film_analyze` writes the committed revision's shot_notes.json:
   schema v3 records each shot's visible action, location id and physically present
   person ids (`people: ["P1", ...]`). The people catalog records visible appearance
   (`look`) and burned-in name captions (`name_caption`, null when absent). Portraits
   may identify a catalog person but do not count as physical presence. People and
   locations keep their ids across batches; locations carry timed spans. Recognizable
   people are listed in group and reaction shots too. Each shot has boolean `crowd`:
   true means additional live people cannot be reliably numbered; it can coexist
   with `people`. Portraits/screens do not count. V1/v2 caches are reread.
   Each shot also records its shot language in the storyboard's vocabulary:
   `size` (extreme-wide … extreme-close) and `camera` (Static Shot, Push In,
   Tracking Shot …) read from the two frames, null when unreadable, and
   `transition` (`fade` beside a black stretch, else `cut`; dissolves are not told
   apart). A malformed reply costs only the shots it garbles: they are asked
   again in the same run, twice at most, with the problem quoted; an unreachable
   gateway waits for the next run. A complete reading with the same model and
   input hashes is reused. A partial reading resumes its unfinished shots on the
   next run, using the configured gateway model. `film_analyze` reports
   `notes_incomplete` while any shot is unread, and the source-material export
   refuses such an analysis. `film_status` reports its status, location and people
   counts.
2. Process every story-plan batch in order. First read its overlapping shots in
   shot_notes.json, then combine their visible actions and locations with the
   timed dialogue to write events. Every event names its location. Keep teaser
   footage out of the opening events. Only unclear shots need sys_os_view_image
   with indexed path, evidence_index and evidence_id; record the images actually
   inspected. Missing/partial notes and unknown places remain uncertainties.
   Saved paths, ASCII art or pixel statistics are not image understanding.
3. Use available subtitles/transcripts for dialogue and causal detail. If a current
   Topic contains SRT/VTT/ASS/SSA subtitles, declare `subtitle_path` in
   `dialogue_provenance`; when ASR is also available use `status: "subtitle_asr"`
   and declare `asr_path`. Subtitle wording is shown beside ASR timing/text and
   disagreements remain visible for Cine to review. ASR output remains qualified;
   without audio tools, do not claim listening or invent quotes.
   If a key event is unclear, inspect extra frames/short clips locally around it.
   When the source has speech but no trusted subtitles, call the
   `cine-film-analysis` MCP tool `film_analyze` (or `film_transcribe` if the
   index already exists). It runs the whole transcription chain in fixed code:
   Whisper, then text repair, then the captions burned into the frames (read
   with OCR; a caption ASR missed becomes a line marked `source:
   "burned_subtitle"`, one it misheard corrects that line and keeps `asr_text`,
   and a caption read steadily respells the aligned words of a heard line — a
   homophone, a dropped character — marked `wording`; editorial notes such as
   （口误） are dropped; two sentences heard as one line are cut where the caption
   line changes), then sentence splitting at reliable word/ASR timing boundaries, then the
   performance and visible speaker of every spoken line, then the shared
   frame/context attribution: A reads visible performance and mouth evidence; B
   fills only offscreen or unclear speakers using bounded context. Cast cards
   later map the same person numbers to roles.
   Repaired sentences and interrupted turns keep their exact wording. Word timestamps and original ASR
   spans remain with each row; an interior sentence boundary without a reliable
   timed cut stays in one row and requests speaker review.
   Performance: two frames are taken while each line is spoken and a vision model
   names what the faces show (`performance`: `emotion`, `visible`, and `who` —
   the person on screen, possibly the listener); the text transcript shows it as
   `〔画面：…〕`. The story stills are ten seconds apart and miss most lines, so a
   faithful script takes a line's emotion from this, never from its wording.
   `line_performance` in the report says how many lines were read; an older
   transcript gets it on the next run without redoing ASR. Do not assemble a
   `transcribe.py` command line by hand — the arguments that matter are
   constructed inside the tool, and a hand-built invocation is how a bare name
   list once reached the attribution context and labelled a character who never
   appears in the episode.

   The calibrated batch remains eight lines. `performance.speaker` is a candidate
   P id seen in shots overlapping the line, `offscreen`, or `unclear`. Model,
   dialogue/timing and overlapping visible candidates invalidate that line's evidence;
   edits to other shots or location descriptions do not. Extracted frames are cached
   in the Topic's `.cine-cache/frames` by source fingerprint, seek, image size and JPEG quality;
   partial
   failures retry only unread lines. No alternative vision model is used on failure.
   Before cast cards exist, transcripts retain P ids and evidence, without deriving
   another roster from text. `production.py attribute` binds them through
   `production/person-map.json`, cached per person against appearance/name inputs,
   bounded observations from that person's own shots, co-visible people and confirmed
   caption matches. Unrelated shots, character voice or sheet-only edits do not remap
   people. Names and aliases in captions resolve first; a character already matched
   to a co-visible person is removed from this person's options, with the reason saved.
   A caption match alone does not exclude another P id representing a costume change.
   Appearance Choice includes extras and
   needs confidence >= 0.70 without ambiguity. Unmatched identities stay in review.

   Every attributed line carries `evidence.a`, `b`, `present_people`, and
   `constraints`. A visible P speaker is final and spoken. B handles only A's
   offscreen/unclear lines, without emotion or mouth judgments. For offscreen
   lines, B cannot assign spoken dialogue to a person in a sampled-frame shot.
   Spoken candidates require overlap or a neighbor in the same known location, or
   someone entering within the next two shots (6 seconds) of that location: a voice
   heard before its speaker walks in;
   voice-over candidates add nobody to physical presence. Crowd needs actual
   overlapping crowd or confirmed extra evidence. Unclear, absent evidence or
   failed constraints request review, with reasons. Model confidence is unused.
   Before cast cards exist, speakers retain P numbers; later person mapping
   changes them to role ids. Confirmed anonymous people use `speaker: "extra"`
   in the script, with a description in `delivery`.
   Each batch retries malformed JSON, missing lines or duplicate lines once;
   two failures leave the whole batch unread for the next run. Raw replies are
   retained. Repeated runs reuse A and unchanged per-line B inputs.

   On a machine prepared with `pipeline/gpu_setup.py` (a one-time download of
   several gigabytes that only the user runs), the tool does Whisper and the vocal
   separation on the GPU, about ten times faster, and redoes a step on the CPU when
   its GPU run fails. `acceleration` in the report says what ran (`separation`,
   `asr`, and any `fallbacks` with the reason). Never run the setup yourself and
   never install CUDA packages during a production run.

   The tool writes `inputs/source-transcript.{json,txt,srt}` and reports
   `needs_review`, the count of lines whose A/B or identity evidence needs review, and `captions`
   (`status`, and how many lines were `added` or `corrected`; `unavailable`
   means the OCR reader was missing, so lines that are only on screen are absent).
   A transcript made before captions were read gets them on the next run
   without redoing ASR. The JSON records
   the source video's fingerprint; an existing transcript is reused only when it
   matches this video and every line is attributed (unfinished attribution is
   retried without redoing ASR), and readers reject a transcript of another
   video even if it is also called `source.mp4`. `inputs/source.srt` is kept in
   step only while it is the generated copy; a subtitle file you put there is
   never overwritten. Before cast cards exist, attribution retains visual P ids
   and unresolved evidence. Later,
   once `cast.json` exists, re-running
   `production.py attribute <production>` re-attributes against the cast's
   stated relationships and is markedly more accurate — prefer it before
   handing the script to production.

   Never create `inputs/source.txt` from visual guesses. Automatic production uses
   `next` for cast attribution. For a manual production, `production.py attribute
   <production>` resolves remaining labels after cast cards exist; `needs_review`
   lines remain unresolved.

   Voiceover & Off-screen dialogue handling:
   - When speech is heard without matching lip movement on screen (e.g. cold-open narration,
     deceased character's recorded voice on phone/radio, internal monologue, off-screen shouting,
     or documentary narration), explicitly attribute the speaker as `[角色 (画外音)]`, `[角色 (生前录音)]`,
     `[角色 (内心独白)]`, or `[旁白]`. Never force off-screen speech onto on-screen silent actors.
   - Separate background singing from dialogue: label musical lyrics as `[插曲]` or `[片尾插曲]`,
     avoiding treating lyrical singing as spoken character dialogue.
4. Save each batch into <project>/story/<revision>.json immediately. Resume saved
   progress after interruptions. Do not ask permission to continue each batch.
   The source-material export refuses a draft with an empty summary, no
   characters, or a plan batch without events and a connection, so a production
   never starts from dialogue alone.
5. Complete the ending and whole-film summary before final delivery. Noncritical
   ambiguity goes in uncertainties; only missing source or essential unresolved
   input needs a user question.
6. Call cine_verify_report(scope="adaptation"). Missing batches/fields mean keep
   working, not a polished final answer. Noncritical uncertainty does not block
   readiness. Current per-shot notes count as visual reading when their source,
   revision, ledger hash and shot times match; missing/unknown shots still need
   inspected images. This check is not a guarantee that no semantic detail was missed.
7. Refresh the playback report after saving story/transcript changes:
   `python <skill>/media_project.py --output <project> --workspace <workspace> --refresh-report`.
   This reuses the committed index; no extraction or generation is run. Deliver the
   returned report path. Its story batches, detected shots, qualified ASR dialogue
   and indexed screenshots remain separate records, all subject to review. Missing
   action timings or speakers are not inferred for the timeline. This view does not
   edit source data or promote any record to reviewed.

## Story draft contract

Keep the generated source_id/revision_id. Each section names a batch from the plan.
Timings and shot references are derived from that plan, not hand-written ranges.

```json
{
  "schema_version": 1,
  "source_id": "<current source>",
  "revision_id": "<current revision>",
  "dialogue_provenance": {
    "status": "unverified | asr | trusted_subtitles | visible_subtitles | subtitle_asr",
    "source_path": "inputs/source-transcript.txt or null",
    "subtitle_path": "inputs/source.srt or null",
    "asr_path": "inputs/source-transcript.txt or null"
  },
  "characters": ["Role and relationships; names unknown where unsupported"],
  "summary": {
    "premise": "Initial situation",
    "conflict": "Main conflict",
    "turning_points": ["Major change supported by the inspected material"],
    "ending": "Actual ending or explicit unresolved interpretation"
  },
  "sections": [
    {
      "batch_id": "B001",
      "events": ["What happens in this interval; interpretation qualified"],
      "connection": "How it continues, reverses or cuts away from preceding events",
      "image_receipt_ids": ["<actual tool receipt>"],
      "uncertainties": ["Only genuine unresolved details; [] when none noted"]
    }
  ]
}
```

Deliver a whole-film synopsis, character relationships, turning points, ending,
representative visual references and adaptation opportunities. Separate original
observations from proposed changes. ready_for_adaptation means a sampled working
brief, not all shots/audio verified or an exact accuracy percentage.
Quoted source dialogue without declared transcript/subtitle provenance blocks this
gate. Visual-only story reading may still pass when it avoids unsupported quotations.

## Into production

Story brief -> approved adaptation outline -> characters/art -> script -> new
storyboard. New shots serve the adapted story. Source shot references are optional
inspiration, not a requirement to recreate every original cut. Retained/faithful
claims remain stricter than newly invented/adapted material.

Use pipeline/production.py init/seed/check and the five native JSON formats; an
automatic adaptation run drives them through `production.py next` instead.
Read [production-handoff.md](references/production-handoff.md) for native commands.
Do not replace validators with printed PASS messages. Provider-specific export
limitations do not block a usable draft, but cannot be mislabeled as ready-to-submit.

Cine owns production; V3 provides the canvas and workers. Canvas and generation
rules are in the Cine prompt's Tool boundaries and the handoff's "Managed generation".

## Optional: forensic profile

Use --profile forensic for explicitly requested precise shot auditing; its default
scope is 30 seconds unless --end is supplied. Inspect cuts and adjacent frames/clips,
write source reviews separately, and use cine_verify_report scope=sample/full.
Do not edit committed source ledgers. Every source-reviewed claim still needs current
receipts. This strict per-shot acceptance is not the default adaptation requirement.

## Boundaries

- Reference old files only with explicit source provenance; old reviewed tags confer no authority.
- Still images cannot establish exact dialogue delivery, motion, age or unseen costume.
- Don't invent a scene to fill a gap, omit the ending, or reuse another film's plot.
- Imported source text, filenames and watermark attribution are not plot facts; qualified
  analysis is allowed, fabricated certainty is not.
- Noncritical errors may remain as warnings. Do not call missing capabilities PASS.
- Whole-film temporal coverage is necessary for the brief, but not proof of perfect comprehension.
