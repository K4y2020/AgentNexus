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

Use the directory returned by load_skill; do not recursively search for scripts.

```powershell
uv run --with "scenedetect[opencv]>=0.6.4" --with "pydantic>=2,<3" --with "jinja2>=3.1,<4" --with "filelock>=3" python <skill>/media_project.py --video <source> --output <workspace>/projects/<name> --workspace <workspace> --profile adaptation
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

1. Read story_plan.json, evidence_index.json and the generated story draft path.
2. Process every batch in order. Call sys_os_view_image with indexed path,
   evidence_index and evidence_id for representative images actually inspected.
   Saved paths, ASCII art or pixel statistics are not image understanding.
3. Use available subtitles/transcripts for dialogue and causal detail. If a current
   Topic contains SRT/VTT/ASS/SSA subtitles, declare `subtitle_path` in
   `dialogue_provenance`; when ASR is also available use `status: "subtitle_asr"`
   and declare `asr_path`. Subtitle wording is shown beside ASR timing/text and
   disagreements remain visible for Cine to review. ASR output remains qualified;
   without audio tools, do not claim listening or invent quotes.
   If a key event is unclear, inspect extra frames/short clips locally around it.
   When the source has speech but no trusted subtitles, create a reusable local ASR
   transcript instead of improvising dialogue from stills:
   `uv run --with "faster-whisper>=1.2,<2" python <skill>/pipeline/transcribe.py
   --media <source> --output <workspace>/inputs/source-transcript --workspace <workspace>
   --model large-v3-turbo --language zh`. For heavy BGM, singing or music-masked dialogue, add
   `--separate-vocals` (uses Demucs) and `--initial-prompt "<context-summary-and-character-names>"`.
   This automatically generates `.json`, `.txt` and `.srt` in the workspace. Never create `inputs/source.txt` from visual guesses.
4. Save each batch into <project>/story/<revision>.json immediately. Resume saved
   progress after interruptions. Do not ask permission to continue each batch.
5. Complete the ending and whole-film summary before final delivery. Noncritical
   ambiguity goes in uncertainties; only missing source or essential unresolved
   input needs a user question.
6. Call cine_verify_report(scope="adaptation"). Missing batches/fields mean keep
   working, not a polished final answer. Noncritical uncertainty does not block
   readiness. This check is not a guarantee that no semantic detail was missed.
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

Use pipeline/production.py init/seed/check and the five native JSON formats.
Read [production-handoff.md](references/production-handoff.md) for native commands.
Do not replace validators with printed PASS messages. Provider-specific export
limitations do not block a usable draft, but cannot be mislabeled as ready-to-submit.

Cine owns production; V3 provides the canvas and workers. Read before editing,
preserve revisions, and require explicit authorization for paid generation or
private-media uploads. seedance_agent_message is disabled. Do not use imagegen
fallbacks or turn a local path into a fabricated remote asset ID.

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
- Noncritical errors may remain as warnings. Do not call missing capabilities PASS.
- Whole-film temporal coverage is necessary for the brief, but not proof of perfect comprehension.
