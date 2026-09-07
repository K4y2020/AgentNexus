---
name: film-analysis
description: Whole-film story reading and reference-shot analysis for adaptation; optional forensic shot auditing.
---

# Film analysis for adaptation

Default to a practical whole-film working brief, not frame-perfect replication.
The user's approximate accuracy tolerance is not a measured 80% score. Preserve the
main events, causal links, characters, turning points and ending; disclose uncertainty
rather than inventing facts. A supplied full video is not an invitation to stop at 30s.

## Default: adaptation readiness

Use the directory returned by load_skill; do not recursively search for scripts.

```powershell
uv run --with "scenedetect[opencv]>=0.6.4" --with "pydantic>=2,<3" --with "jinja2>=3.1,<4" --with "filelock>=3" python <skill>/media_project.py --video <source> --output <workspace>/projects/<name> --workspace <workspace> --session-id <session> --profile adaptation
```

Omitting --end covers the source duration. An explicit --end selects a narrower
scope and must not be reported as full-film work. --resume preserves an unchanged
adaptation revision and its saved story progress; changed scope creates a new revision.
Source identity changes require a new project. Never copy old scratch analyses.

The pipeline generates a canonical cut index plus story_plan.json under its revision.
It samples three representative stills per 30-second temporal batch, independent of
cut density, including the final batch. The times are sampling seeks, not a claim of
frame-exact boundary detection. Cut detector failure is a warning in this profile:
temporal sampling can still proceed. Missing media/decoding capability is a real blocker.

1. Read story_plan.json, evidence_index.json and the generated story draft path.
2. Process every batch in order. Call sys_os_view_image with indexed path,
   evidence_index and evidence_id for representative images actually inspected.
   Saved paths, ASCII art or pixel statistics are not image understanding.
3. Use available subtitles/transcripts for dialogue and causal detail. ASR output
   remains qualified; without audio tools, do not claim listening or invent quotes.
   If a key event is unclear, inspect extra frames/short clips locally around it.
4. Save each batch into <project>/story/<revision>.json immediately. Resume saved
   progress after interruptions. Do not ask permission to continue each batch.
5. Complete the ending and whole-film summary before final delivery. Noncritical
   ambiguity goes in uncertainties; only missing source or essential unresolved
   input needs a user question.
6. Call cine_verify_report(scope="adaptation"). Missing batches/fields mean keep
   working, not a polished final answer. Noncritical uncertainty does not block
   readiness. This check is not a guarantee that no semantic detail was missed.

## Story draft contract

Keep the generated source_id/revision_id. Each section names a batch from the plan.
Timings and shot references are derived from that plan, not hand-written ranges.

```json
{
  "schema_version": 1,
  "source_id": "<current source>",
  "revision_id": "<current revision>",
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
