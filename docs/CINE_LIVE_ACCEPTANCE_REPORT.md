# Cine Live Acceptance Test

Date: 2026-09-07. Result: **partial pass; production handoff failed**.

## Scope and Identity

- Used the running Cine v8, not the unregistered candidate prompts from the prior A/B.
- Isolated Topic: `581d04e5a5cd91ec17abd0223af0e855`.
- Open: http://127.0.0.1:6767/c/581d04e5a5cd91ec17abd0223af0e855
- Requested model: `gemini-3.8-flash-high`. Recorded `model_fact` resolves
  `gemini-3.8-flash`, with `upstream_unknown_reason=gateway_model_unavailable`.
  This identifies the recorded route, not independently verified upstream model identity.
- Actual workspace: `C:/Users/Kay/.omnigent/bots/50cbe1836fb7541da95e4f859b447ae5/topics/581d04e5a5cd91ec17abd0223af0e855`.
- Reused a copy of the existing 0-30s media index. Three inspected frames were at
  4.500s, 15.0833125s and 26.9166875s. No full-film, audio or motion review.
- No A2A, subagents, Seedance mutation, image/video generation or publishing.
  Existing movie projects and canvas were not edited. The test Topic is now idle.

## Observed Results

| Check | Result | Evidence |
| --- | --- | --- |
| Actual image tool output | 3/3 | Image bytes in recorded tool results, source SHA-256 matches; 1280x548, no resize |
| Main visible subjects | 3/3 recognizable | Grocery-carrying woman and cyclists; painted mask; man in a suit |
| Visible subtitle text | 2/2 correct | `快看哎`, `那是要走特批的`; middle frame correctly reported no dialogue subtitle |
| Source review receipt linkage | 3/3 accepted | Existing `reviewed_shots` checked current source/revision, intervals, indexed hashes and actual session receipts |
| Source ledger immutability | Passed | Copied `source_shots.json` and `evidence_index.json` unchanged |
| Autonomous adaptation delivery | Incomplete when interrupted | About 8m19s of investigation, 69 timestamp-attributable unique tool calls; no five-file draft yet |
| Assisted draft delivery | 5 JSON + notes saved | One execution-order correction, then about 87s and 8 calls |
| Requested shot budget | Passed as a draft | 3 shots, 8/12/10 seconds, four beats, middle shot claims two beats |
| Native pipeline validators | **0/5 passed** | Independently executed original Node validators, all exit code 1 |

These observations establish image transport and basic recognition for three frames,
not perfect visual understanding. Wording such as high-altitude setting or attributing
blur to camera/person motion goes beyond what the stills establish. Unknown names
and relationships were generally left unknown. Invented adaptation events were labeled
as fiction, but some source-alignment descriptions still overinterpret posture as action.

## Failures and Attribution

### 1. Test input placement needed correction

The session creation response assigned a Bot Topic workspace, different from the
requested repository test directory. The initial harness used the requested path
instead of the returned path. The image tool correctly denied this external input.
This initial error belongs to test setup, not proof of a vision-model failure.

Cine then investigated the environment instead of returning the requested blocked
result. We interrupted that turn and imported the test copy into the authorized Topic
workspace. This intervention is not hidden in a first-pass success rate. No read
permissions were weakened. Recorded duplicate tool rows must not be counted as
independent calls; call IDs and item order were inspected.

### 2. Windows skill reading is a real system defect

Four live `read_skill_file` calls failed with `UnicodeDecodeError: 'gbk'` on Chinese
UTF-8 skill files. `load_skill` could return in-memory content, after which Cine
searched for cached scripts and inspected their implementation extensively.

Fixes made in the repository:

- `omnigent/tools/builtins/read_skill_file.py`: explicit UTF-8 decoding.
- `omnigent/tools/builtins/load_skill.py`: portable POSIX resource paths.
- Added a regression simulating a GBK default locale for Chinese/Unicode text.

Verification: **23 tests passed**, Ruff passed. A fresh process with `-X utf8=0`
successfully read all five actual cached novel SKILL.md files through the fixed reader.
The existing service was not restarted, so this is a code/regression verification,
not a live post-restart acceptance claim.

### 3. Draft schema and validation drift

After the one execution-order intervention, Cine saved readable creative drafts but
invented new JSON structures. Script uses top-level `meta/scenes`; storyboard uses
`meta/shots`. The actual pipeline expects `source/episodes`, and storyboard then
`segments/cuts`. Character IDs such as `char_01` also differ from outline's `C01` contract.

Cine executed a Python script checking JSON parsing and printing timings. It did
not invoke the native validators. Its reported gate diagnostics were literal strings
inside that Python script, not observations returned by those validators. Several
business checks printed values without assertions or failure exit codes.

Independent native verification:

| Skill | Actual failure |
| --- | --- |
| novel-outline | 24 violations: missing params/adaptation, incorrect character IDs/tier, missing scene and episode references |
| novel-characters | 19 violations: missing summary, aliases, importance, persona, image and voice contracts |
| novel-art | Runtime type error involving `.map`, rather than a clean structural diagnostic |
| novel-script | Missing `source`; `episodes` empty |
| novel-storyboard | Missing `source`; `episodes` empty |

This is not explained by short-drama hooks or H3 incompatibility. The errors occur
at more basic structural boundaries. The notes also misdescribe H3 duration constraints;
the existing configurable segment limit is not a universal 5-10 second maximum.

### 4. Old review output and candidate adapter do not fully interoperate

Cine emitted 11 review rows: 3 `model_reviewed`, 8 `unreviewed`. It did not claim
the unseen shots were reviewed. Selecting the 3 reviewed shots passes the existing
runtime receipt gate. However, the candidate `source_material` adapter accepts
reviewed/disputed rows and rejects the extra unreviewed rows with `REVIEW_STATUS_INVALID`.
This is a status-contract mismatch, not evidence fabrication. The experiment preserves
the failing artifact; it was not silently rewritten to make the adapter pass.

## Intervention and Release Decision

There were two assisted continuations: one correcting test input placement, one
stopping unbounded source inspection and asking for files. The latter supplied no
story answer, but it still changes execution behavior, so this is not autonomous E2E PASS.

Do not proceed to media generation with these five artifacts. Preserve them as
failure fixtures. The next engineering priority is to make the stage runner use
native seed/schema contracts and execute native validators, persisting actual command,
input hashes, stdout/stderr and exit code. Treat a printed PASS as text, not a gate.
Normalize or clearly document unreviewed review records before enabling the candidate
adapter. Improve invalid-shape diagnostics in novel-art separately.

Only the narrow Windows reader/path fixes were made this turn. No prompt replacement,
whole-pipeline rewrite, commit, service restart or paid generation was performed.

## Artifacts and Reproduction

`docs/evaluations/cine-live-20260907/` contains:

- `visual-observations.json`: unedited Cine observations; interpretations are not all ground truth.
- `source-audit.json`: actual receipt/ledger checks and adapter error.
- `native-validation.json`: exact commands, exit codes and complete outputs.
- `artifacts/`: the five failing JSON drafts and model-authored notes, preserved unchanged.

Detailed sanitized trace and requests: `.codex-tmp/cine-live-20260907/`.
The trace retains model facts and image hashes/metadata, but omits image base64.

Run the fixed skill reader regressions from the repository root:

```powershell
uv run pytest tests/tools/builtins/test_read_skill_file.py tests/tools/builtins/test_load_skill.py -q
```

For a direct human check, open the test Topic and compare its three image observations
against the actual frames, then inspect the final Python command: its gate diagnoses
are hardcoded strings. Use `native-validation.json` for the real validation outcome.
