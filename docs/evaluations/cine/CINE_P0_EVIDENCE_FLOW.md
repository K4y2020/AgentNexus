# Cine P0 Evidence Flow

## Canonical Entry

`examples/cine/skills/film-analysis/media_project.py` delegates to `pipeline.entry`.
The default sample ends at 30 seconds; shorter clips need an explicit end time.
Use a new output path; `--resume` requires the same source. The `cine` optional
dependencies enable real PySceneDetect tests rather than skipping them.

Successful indexing returns `indexed_unreviewed`, never a visual PASS. It creates
an immutable revision and a separate `reviews/<revision-id>.json` file. Source
shots link to overlapping evidence records; the physical index validator rejects
visual review claims embedded in the index itself.

## Session Binding

With `--session-id` and `--workspace`, successful indexing writes
`<workspace>/.cine/sessions/<session-id>.json` containing `session_id` and an
absolute `project_path`. Failed indexing/rendering does not bind. Existing
session bindings cannot silently switch projects. Bridge resolution follows only
that project's `project.json.current_revision`; no project/version directory scan
or process-CWD fallback remains. Legacy flat ledgers require explicit migration.

## Images and Reviews

Call `sys_os_view_image` with `path`, `evidence_index`, and `evidence_id`. The
reader verifies format, size, indexed path and SHA-256 before constructing image
content. Metadata includes a receipt ID, source/revision/evidence IDs and source
interval. `image_prepared` describes tool output construction, not provider
acknowledgement, visual understanding, or semantic correctness.

Store observations separately as a JSON list of records with `shot_id`,
`source_id`, `revision_id`, `status` (`model_reviewed` or `disputed`),
`observations`, and `image_receipt_ids`. This is a model review claim, not human
approval. Image delivery alone never upgrades it. Motion/audio remain separate.

## Dispatch Gate

Explicit source-shot dispatch requires the current session binding, current
revision, unique shot IDs, model-reviewed observations and matching image-tool
results in that session's recorded history. The bridge checks source/version,
evidence ownership, time overlap and current image bytes. Missing, stale,
ambiguous or incomplete inputs fail before V3 creation or message submission.

Free-form no-shot requests remain unverified drafts; generation requests need
reviewed source-shot IDs. This does not certify free-form prose or prevent an
agent with unrestricted shell access from bypassing supported APIs. It is a
workflow integrity gate, not a security sandbox or an automatic truth detector.

## Verification

2026-09-07: real 0-30s sample of the supplied EP1 produced 10 candidate cuts,
11 indexed shots and 40 evidence records, with zero reviewed shots. The linked
image read produced text+image content at 1280x548. No inference, media generation,
existing-project edits or full-film reanalysis occurred in this smoke test.

Run the bundle tests separately from root tests to avoid the duplicate `tests`
package import collision:

```text
uv run --extra cine pytest examples/cine/tests -q
uv run pytest tests/seedance tests/inner/test_image_tool.py tests/test_cine_bundle.py tests/runtime/test_prompt.py -q
```
