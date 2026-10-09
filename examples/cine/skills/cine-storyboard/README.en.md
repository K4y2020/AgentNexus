[中文](README.md) · **English**

# cine-storyboard

In Cine's multi-skill pipeline the [production handoff](../film-analysis/references/production-handoff.md) governs the handoff contract and the Cine prompt's Tool boundaries govern canvas edits and generation; the skill also works standalone.

Beat coverage does not prove that the video prompt includes the claimed actions. Check each scene's required initiation, response, interaction and ending against the actual `[Shot k]` text. If the script already omits them, repair it first, then update beat ranges, opening frames, continuity states, prop ownership and duration. Preserve approved ellipsis and shooting forms; report text checks separately from generated-video review.

Storyboarding for AI short drama: turns cine-script's beat flow into a video generation worklist. Shot duration and edits follow content and approved shooting forms. The default shot cap follows the provider's generation duration. Hence a three-level structure:

```
segment = one video-generation call, ≤ 15s, never crosses scenes
 ├─ cuts × 1–N = uninterrupted shots that may fill the segment, each claiming script beats
 ├─ frames    = one keyframe per cut: the master frame pinned at 0.00s,
 │              sub-frames pinned at their own cut marks
 └─ H3 prompt = one per segment; multi-picture alignment + [Shot k] cut times audited verbatim
```

- **Dialogue follows the chosen shooting form** — several speakers may speak sequentially in one shot; motivated shot–reverse-shot remains available. Every line still binds its speaker, voice and source ID
- **The alignment instruction is derived, not written** — the multi-picture line (`Picture 2 aligns with the 3.00-second mark…`) and every `[Shot k] At 00:0X.XXX` cut time are computed from cut durations, and validate audits them **character for character**: change a duration without updating the prompt and it blocks
- **Prompts follow the official spec: English by default, one shot per line** — each shot on its own line with its cut time; dialogue, lyrics and on-screen text keep their original language per the official rules (`<d>[Chinese] …</d>` verbatim). `promptLang: 'zh'` switches the whole prompt to Chinese. The writing spec is internalized as `references/h3-prompt.md` — **this skill is self-contained and depends on no external skill**
- **Frames are asset composition, not invention** — generation feeds the scene / character / prop sheets as references; available V3 tools and explicit authorization allow submission, with results separately verified (optional)

Outputs `storyboard.json`, a Markdown shot list, and a self-contained `storyboard-report.html`:

![storyboard-report.html](assets/report.webp)

H3 export and prompt validation remain H3-specific. Unsupported provider export (including Seedance) is blocked; a structured storyboard draft is allowed, but never fill fake `h3Prompt` values to pass validation. Short-drama style/hook gates do not justify rewriting a slow film; report native incompatibilities honestly.

## Seventeen quality gates, all code

Same stance as the other four skills in this repo: **a checklist the model grades itself on is worthless.**

| Gate | Rule |
| --- | --- |
| **Full beat coverage** | every script beat claimed exactly once, in order, contiguous, cut-level |
| Segment duration | 0 < Σcuts ≤ 15s (the single-generation cap; tune `params.maxSegmentSeconds`) |
| **Cut duration** | Default minimum 2s; maximum follows `maxSegmentSeconds` (15s for the current H3 workflow). Explicit `maxCutSeconds` may constrain the creative range |
| Dialogue fits | dialogue seconds of claimed beats ≤ cut seconds, per cut |
| Episode total | Σ segments within ±15% of the script's `targetSeconds` |
| On-screen cap | ≤ 3 characters per cut, more requires a breakdown note |
| Segment ID discipline | `E01-01` format, sequential — the segment ID is the asset filename |
| Size phrase | the English shot-size phrase must appear in the cut's frame prompt |
| Camera vocabulary | camera moves use H3's official terms (`Push In` / `Pan Left` / `Tracking Shot`…) and must appear inside **that cut's own [Shot k] passage** |
| **H3 structure** | the alignment line is **derived from the cut structure and audited verbatim**; three fields in order; every `[Shot k]` cut time equals the running sum of prior cut durations |
| **H3 dialogue verbatim** | every claimed line appears verbatim inside a `<d>` block — one changed punctuation mark fails |
| **Prompt language consistency** | prose audited both ways against `promptLang`: Chinese drama written in English fails, English mode mixing Chinese fails |
| **Style phrase** | the `style` preset's English phrase (realistic / ghibli, name-aligned with the character and art skills) must appear in every frame prompt — one drama, one look |
| Frame-prompt hygiene | English-only, non-empty |
| No character names | frame prompts always; the H3 prompt only in English mode (Chinese prompts allow names — identity is anchored by the frames). Checked with `--outline` / `--cast`; skipping is **announced** |
| Reference integrity | scene index / characters / props all audited against the script scene |
| **Shot recipe** (optional mount) | only checked with `--shots <cards dir>`: a cut's `recipe` id exists in the library, every must-phrase of that card appears in the cut's frame prompt, and a multi-cut recipe runs long enough. Without `--shots` the skip is **announced**; so is "no cut references a recipe" |

The selftest **defeats every gate on purpose** to prove each one actually blocks.

**Shot recipes are an optional vocabulary layer.** A cut may carry an optional `recipe` — a card id from an external optional card library, **per cut, not per segment**, with **multi-cut recipes expressed as a run of consecutive cuts sharing the id** rather than an array. Without an external card library installed everything still runs: cards are read by this skill's own 25-line restricted frontmatter parser, not imported from the card library; only small generic helpers come from `../_shared/cine-common.mjs`, which ships with the skills folder. A card's suggested sizes and cameras are **deliberately not gated** — the report's Recipe column marks deviations with `≠` (hover for the suggestion) and `checkup` prints a note. A recipe is vocabulary, not law; make an optional mount stricter and nobody mounts it, and **a gate that blocks wrongly is worse than no gate**.

## Gate failures accumulate, and `stats` tells you which rule the model breaks most

Every `validate` and `checkup` appends the gate outcome to `.gates.jsonl` in the **current directory**. After a few dozen runs:

```bash
node scripts/cine-storyboard.mjs stats
```

It answers three questions:

| Question | What it tells you |
| --- | --- |
| **Which gate fires most** | That rule is the one the model ignores — **the wording is what needs fixing, not the model** |
| **Which gate never fires** | Either a dead gate, or a rule the model has internalised |
| **What the failure details look like** | Problems that recur without a gate of their own only surface by reading this free text |

This is the one idea worth borrowing from SkillOpt's "the skill document is trainable state": **a document is not a spec written once, it is something you iterate from feedback** — but the iteration needs evidence rather than impressions. The log only accumulates evidence; what to change stays a human call.

Pass `--no-log` to skip it. If the file cannot be written the step is skipped silently and validation is unaffected. `.gates.jsonl` is already in `.gitignore`.

## The report

A single-page, 1600px-wide review document. Reports render with a Chinese UI by default; pass `--lang en` to `render` for a fully English report (zh / en built in). This only switches the UI labels — it is independent of `promptLang`, which controls the H3 prompt language (English by default). In English mode the quality-gate labels are translated too (thresholds kept as computed); failing-gate details and all data stay as authored.

- **KPI band**: segments / cuts with average length / total vs target / generation batches / segments carrying dialogue
- **Cut rhythm strip** (the signature chart): one band per episode, **thick separators = segment boundaries (one generation each)**, slice width = cut duration share, color depth = shot size; click a slice to jump to its segment card
- **Segment cards**: the master frame in 16:9 (an honest prompt placeholder when not generated), a sub-frame strip, then a **50/50 split**: cut rows on the left (start mark · seconds · size · camera · recipe · picture summary **auto-derived from the claimed script beats**), and an H3 prompt panel on the right — one shot per line, with one-click copy
- **Generation batch list**: segments sharing a scene + lighting state form one batch around one environment reference image
- **Audio alignment list**: every dialogue line mapped to **segment#cut** — the worklist for placing TTS audio, fully computed
- **Quality gates** panel + header badge + **Export JSON** (downloads `storyboard.json` verbatim)
- All graphics inline CSS/SVG, zero external resources, opens offline

## The relay — the pipeline closes here

```
cine-outline    → outline.json    (what: structure)
cine-characters → cast.json       (who: character sheets)
cine-art        → art.json        (where: scene & prop sheets)
cine-script     → script.json     (the drama: scenes, beats, lines)
cine-storyboard → storyboard.json (how to shoot: segments, cuts, frames, H3 prompts)
```

`seed <script.json> --eps 1-3` deterministically expands each scene's beat list (numbers, per-beat seconds, speakers) as the cutting worksheet. `validate --script` is mandatory; `--outline` / `--cast` enable the name ban, `--art` gets scene names and sheet thumbnails into the report. Frame generation uses direct V3 canvas tools with verified upstream reference assets; the export package is H3-specific and requires separate generation authorization.

## CLI

```bash
node scripts/cine-storyboard.mjs seed script.json --eps 1
node scripts/cine-storyboard.mjs validate sb.json --script script.json --outline outline.json --cast cast.json
node scripts/cine-storyboard.mjs checkup sb.json --script script.json
node scripts/cine-storyboard.mjs validate sb.json --script script.json --shots /path/to/cards   # optional: the 17th gate
node scripts/cine-storyboard.mjs render sb.json --html --script script.json --outline outline.json --art art.json > storyboard-report.html
node scripts/cine-storyboard.mjs render sb.json --html --lang en --script script.json --outline outline.json --art art.json > storyboard-report.html   # English report UI
node scripts/cine-storyboard.mjs export sb.json --script script.json   # per-segment folders: f1..fN.png + prompt.md
```

## Limits

- No writing or rewriting dialogue, no design sheets, no video generation or editing
- Lip-sync is out of scope for now — that belongs to the generation pipeline
- Seconds are a **generation order, not an estimate**; tune the segment cap and cut-rhythm range in `params` per your model
- Report UI ships in Chinese (default) and English — pick with `--lang`; the prompt language is controlled separately by `promptLang` (English by default)
- Generate the first segment's full frame set (3–5 images) for approval before committing — one episode is ~30–40 frames, and a wrong art direction wastes the batch

## Selftest

```bash
node scripts/selftest.mjs
```

254 assertions — beat expansion, H3 skeleton derivation, stats and batching, gate-defeating cases, recipe-card parsing and mounting, seed, rendering (both report UI languages), export. No model calls, runs in about a second.

The bundled example (`examples/渡口-storyboard.json`) is a complete episode-1 storyboard — 10 segments, 34 cuts claiming all 35 script beats at ~3.5s per cut, 119s against a 120s target, 2 generation batches, every segment carrying a fully audited H3 prompt.

**Only tested on macOS + Node 24.**
