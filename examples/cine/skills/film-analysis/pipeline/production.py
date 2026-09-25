"""Native Cine draft seeding and validation. Never submits media generation."""

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2]
STAGES = {
    "outline": "cine-outline",
    "cast": "cine-characters",
    "art": "cine-art",
    "script": "cine-script",
    "storyboard": "cine-storyboard",
}
INPUTS = {
    "outline": (),
    "cast": (),
    "art": ("cast",),
    "script": ("outline", "art", "cast"),
    "storyboard": ("script", "outline", "cast", "art"),
}
# Stage whose artifact is a JEV speaker attribution rather than a skill document,
# so it has no node validator and its inputs are the cast plus the ASR rows.
ATTRIBUTION_STAGE = "attribution"
ATTRIBUTION_INPUTS = ("cast",)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(path):
    return digest(path) if path.exists() or path.is_symlink() else None


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def stage_path(directory, stage, *, must_exist=True):
    """Resolve an explicit binding or one unambiguous legacy artifact."""
    directory = directory.resolve()
    manifest = directory / "production.json"
    if manifest.exists() or manifest.is_symlink():
        if not manifest.resolve().is_relative_to(directory):
            raise ValueError("production.json must stay inside the production directory")
        document = read(manifest)
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ValueError("production.json schema_version must be 1")
        artifacts = document.get("artifacts") if isinstance(document, dict) else None
        entry = artifacts.get(stage) if isinstance(artifacts, dict) else None
        relative = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(relative, str) or not relative.strip():
            raise ValueError(f"Missing explicit {stage} binding in production.json")
        if Path(relative).is_absolute():
            raise ValueError(f"{stage} binding must be a relative path")
        selected = (directory / relative).resolve()
        if not selected.is_relative_to(directory) or selected == manifest.resolve():
            raise ValueError(f"{stage} binding escapes or aliases production.json")
        if selected.exists() and not selected.is_file():
            raise ValueError(f"{stage} binding is not a file: {selected}")
        if must_exist and not selected.is_file():
            raise FileNotFoundError(f"Bound {stage} artifact is missing: {selected}")
        return selected
    canonical = directory / f"{stage}.json"
    candidates = sorted(
        [path for path in [canonical, *directory.glob(f"*-{stage}.json")] if path.exists()]
    )
    if any(
        not path.is_file() or not path.resolve().is_relative_to(directory) for path in candidates
    ):
        raise ValueError(f"Invalid or escaping {stage} artifact")
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        raise ValueError(
            f"Ambiguous {stage} artifacts: {[p.name for p in candidates]}; "
            "select artifacts.<stage>.path in production.json"
        )
    if not must_exist:
        return canonical
    raise FileNotFoundError(f"Missing {stage}.json or a single *-{stage}.json in {directory}")


def atomic_json(path, value):
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as file:
        temp = Path(file.name)
        json.dump(value, file, ensure_ascii=False, indent=2)
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _atomic_bytes(path, raw):
    """Replace ``path`` with exactly ``raw``, so its sha256 is known in advance."""
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, delete=False, suffix=".tmp"
    ) as file:
        temp = Path(file.name)
        file.write(raw)
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def source_transcript_candidates(directory):
    """Find a transcript beside production or in its containing workspace."""
    bases = [directory, directory.parent]
    if directory.parent.name == "projects":
        bases.append(directory.parent.parent)
    if directory.parent.parent.name == "projects":
        bases.append(directory.parent.parent.parent)
    return (
        directory / "source-transcript.json",
        *(base / "inputs" / "source-transcript.json" for base in bases),
    )


def _source_identity():
    try:
        from .source_identity import fingerprint_of, transcript_matches_source
    except ImportError:  # Direct CLI execution from the pipeline directory.
        from source_identity import fingerprint_of, transcript_matches_source
    return fingerprint_of, transcript_matches_source


def bound_source_fingerprint(directory):
    """The film this production is bound to: (fingerprint, status, material path).

    Only a faithful/adaptation production that binds ``source_material`` in
    production.json has one. That material is exported by
    ``handoff.py --source-project`` from the analysis project's committed
    source.json and pinned by hash. Statuses:

    - ``unbound``: original or text-only production; no film is invented.
    - ``material_pin_mismatch``: production.json pins a different sha256 than
      the material file now has — a stale or edited binding.
    - ``material_without_fingerprint``: bound to a film, but the export
      predates fingerprints, so nothing can be verified against it.
    - ``bound`` / ``bound_unpinned``: a usable fingerprint, from a pinned
      material or from a legacy manifest that records no pin.
    Only the last two carry a fingerprint.
    """
    fingerprint_of, _matches = _source_identity()
    manifest = directory / "production.json"
    if not manifest.is_file():
        return None, "unbound", None
    document = read(manifest)
    artifacts = document.get("artifacts") if isinstance(document, dict) else None
    if (
        not isinstance(artifacts, dict)
        or document.get("mode") == "original"
        or "source_material" not in artifacts
    ):
        return None, "unbound", None
    material_path = stage_path(directory, "source_material")
    entry = artifacts["source_material"]
    pinned = entry.get("sha256") if isinstance(entry, dict) else None
    if pinned is not None and pinned != digest(material_path):
        return None, "material_pin_mismatch", material_path
    material = read(material_path)
    expected = (
        fingerprint_of(material.get("source_fingerprint")) if isinstance(material, dict) else None
    )
    if expected is None:
        return None, "material_without_fingerprint", material_path
    return expected, "bound" if pinned is not None else "bound_unpinned", material_path


def transcript_identity(directory, payload):
    """(status, problem) of an ASR transcript against the production's film.

    With a bound film, only a transcript recording that film's fingerprint may
    reach JEV or the script validator: another video's transcript, or a legacy
    one that records no fingerprint, is refused — and so is every transcript
    while the binding itself is stale or cannot identify the film. Only an
    original or text-only production (``unbound``) uses the transcript
    unverified, as before.
    """
    fingerprint_of, matches = _source_identity()
    expected, binding, path = bound_source_fingerprint(directory)
    if binding == "unbound":
        return binding, None
    name = path.name if path is not None else "source_material"
    # finalize cannot repin while this gate refuses the binding; rebind-source
    # re-exports the material, checks it is the same film and repins it.
    recover = (
        f"`python production.py rebind-source {directory} --source-project <analysis project>`, "
        f"then `python production.py finalize {directory}`"
    )
    if binding == "material_pin_mismatch":
        return binding, (
            f"production.json pins source_material to a different sha256 than {name} now has, so "
            "the film binding is stale or was edited. Restore the pinned file, or rebind it "
            f"from the analysis project it came from: {recover}."
        )
    if expected is None:
        return binding, (
            f"This production is bound to a film through {name}, but that export records no "
            "source fingerprint, so no transcript can be verified against it. Rebind it from "
            f"the analysis project it came from: {recover}."
        )
    if not isinstance(payload, dict) or fingerprint_of(payload.get("source_fingerprint")) is None:
        return "transcript_unverified", (
            "source-transcript.json records no source fingerprint, so it cannot be tied to this "
            "production's source material; re-transcribe the film with film_analyze."
        )
    if not matches(payload, expected):
        return "source_mismatch", (
            "source-transcript.json was made from a different video than this production's "
            "source material; transcribe the bound film before checking the script against it."
        )
    # A legacy manifest without a pin still verifies, but says so.
    return ("verified" if binding == "bound" else "verified_unpinned"), None


def rebind_source(directory, source_project):
    """Re-export a bound source_material from its analysis project and repin only it.

    The recovery for a legacy (no fingerprint) or stale film binding. finalize
    cannot refresh pins while the identity gate refuses the binding, so this
    re-exports the material with ``handoff.source_material`` (read-only on the
    analysis project), requires that it records the film's fingerprint and is
    the same source_id and revision_id the production was built from — a
    different film or revision needs a new production — and only then writes.
    It replaces the material file and its own sha256 pin; the outline and
    mapping pins that name the old material are left for ``finalize``. No JEV
    or media generation runs.
    """
    try:
        from .handoff import source_material
    except ImportError:  # Direct CLI execution from the pipeline directory.
        from handoff import source_material
    fingerprint_of, _matches = _source_identity()

    directory = Path(directory).resolve()
    manifest_path = directory / "production.json"
    if not manifest_path.is_file():
        raise ValueError(
            "rebind-source needs a production.json that binds source_material; "
            "a production without one has no film to rebind"
        )
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("production.json schema_version must be 1")
    if manifest.get("mode") not in ("faithful", "adaptation"):
        raise ValueError(
            "rebind-source applies only to faithful/adaptation productions; an original "
            "production has no film binding"
        )
    artifacts = manifest.get("artifacts")
    entry = artifacts.get("source_material") if isinstance(artifacts, dict) else None
    if not isinstance(entry, dict):
        raise ValueError(
            "production.json has no source_material binding to rebind; this production is "
            "not bound to a film"
        )
    material_path = stage_path(directory, "source_material")
    material_raw = material_path.read_bytes()
    current = json.loads(material_raw)
    if not isinstance(current, dict) or not all(
        isinstance(current.get(key), str) and current[key].strip()
        for key in ("source_id", "revision_id")
    ):
        raise ValueError(
            f"{material_path.name} records no source_id/revision_id, so the film it was built "
            "from cannot be confirmed; start a new production from the analysis project"
        )

    exported = source_material(Path(source_project))
    fingerprint = fingerprint_of(exported.get("source_fingerprint"))
    if fingerprint is None:
        raise ValueError(
            "the analysis project's source.json records no size/head/tail fingerprint; index "
            "the film again with film_analyze before rebinding"
        )
    for key in ("source_id", "revision_id"):
        if exported.get(key) != current[key]:
            raise ValueError(
                f"{key} differs: this production was built from {current[key]!r} but the "
                f"analysis project exports {exported.get(key)!r}. A different film or "
                "revision needs a new production."
            )

    material_bytes = (json.dumps(exported, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    new_hash = hashlib.sha256(material_bytes).hexdigest()
    previous = entry.get("sha256")
    if manifest_path.read_bytes() != manifest_raw or material_path.read_bytes() != material_raw:
        raise ValueError("the production changed during rebind-source; retry")
    # Pin first, then the file: until both are written the pin names bytes the
    # material file does not have, so an interruption leaves the identity gate
    # refusing the binding (material_pin_mismatch) rather than trusting it.
    entry["sha256"] = new_hash
    atomic_json(manifest_path, manifest)
    _atomic_bytes(material_path, material_bytes)
    return {
        "status": "source_rebound",
        "production": str(directory),
        "material_path": str(material_path),
        "source_id": exported["source_id"],
        "revision_id": exported["revision_id"],
        "source_fingerprint": fingerprint,
        "previous_sha256": previous,
        "sha256": new_hash,
        "next_step": (
            f"Run `production.py finalize {directory}` to refresh the dependency pins that "
            "still name the previous material (outline, mapping)."
        ),
    }


def run_attribution(directory, *, model="jev-latest"):
    """Assign each ASR utterance to a cast character using JEV.

    The ASR transcript's own speaker labels are unreliable — one segment can
    merge several people under a single label. The video pass identifies the
    characters correctly, so this stage uses those features to decide who
    speaks each line, and writes the result for the script stage to consume.
    """
    try:
        from .attribute_speakers import attribute_speakers
    except ImportError:  # Direct CLI execution from the pipeline directory.
        from attribute_speakers import attribute_speakers

    cast_path = stage_path(directory, "cast")
    rows_candidates = source_transcript_candidates(directory)
    rows_path = next((p for p in rows_candidates if p.is_file()), None)
    if rows_path is None:
        return {
            "status": "blocked",
            "reason": "source-transcript.json not found",
            "looked_in": [str(p) for p in rows_candidates],
        }
    payload = json.loads(rows_path.read_text(encoding="utf-8-sig"))
    identity, problem = transcript_identity(directory, payload)
    if problem:
        return {
            "status": "blocked",
            "reason": problem,
            "transcript": str(rows_path),
            "transcript_identity": identity,
        }
    segments = payload.get("segments") if isinstance(payload, dict) else payload
    scenes = payload.get("scenes") if isinstance(payload, dict) else None
    cast = json.loads(Path(cast_path).read_text(encoding="utf-8"))

    # Scene descriptions let the attribution weigh who is present and what just
    # happened; without them an address line can be credited to the addressee.
    if not scenes:
        scene_source = directory / "scene-notes.json"
        if scene_source.is_file():
            notes = json.loads(scene_source.read_text(encoding="utf-8-sig"))
            scenes = {int(k): v for k, v in notes.items()} if isinstance(notes, dict) else None

    result = attribute_speakers(segments, cast, scenes=scenes, model=model)
    if "error" in result:
        return {"status": "error", **result}
    if len(result.get("attributions", [])) != len(segments):
        return {
            "status": "error",
            "reason": "speaker attribution did not cover every transcript segment",
        }
    result["input_sha256"] = {
        "source_transcript": digest(rows_path),
        "cast": digest(Path(cast_path)),
    }
    result["transcript_identity"] = identity
    target = directory / "source-transcript-attributed.json"
    atomic_json(target, result)
    needs_review = [a["id"] for a in result["attributions"] if a["needs_review"]]
    return {
        "status": "attributed",
        "path": str(target),
        "model": result["model"],
        "segments": len(result["attributions"]),
        "needs_review": needs_review,
        "transcript_identity": identity,
    }


def script_path(stage):
    name = STAGES[stage]
    return SKILLS / name / "scripts" / f"{name}.mjs"


def shape_errors(stage, doc):
    """Catch contract drift early; semantic validation belongs to the native CLI."""
    if not isinstance(doc, dict):
        return ["root must be an object"]
    errors = []
    if not isinstance(doc.get("source"), str) or not doc["source"].strip():
        errors.append("source must be a non-empty string")
    required = {
        "outline": ("characters", "scenes", "beats", "episodes"),
        "cast": ("characters",),
        "art": ("scenes",),
        "script": ("episodes",),
        "storyboard": ("episodes",),
    }
    for key in required[stage]:
        if not isinstance(doc.get(key), list):
            errors.append(f"{key} must be an array (use native seed, not a custom schema)")
    return errors


def init_outline(directory, source, episodes, seconds, genre, adapt_mode):
    if (
        not source.strip()
        or not genre.strip()
        or episodes < 1
        or not math.isfinite(seconds)
        or seconds <= 0
    ):
        raise ValueError(
            "source, genre, positive episodes and finite positive seconds are required"
        )
    directory.mkdir(parents=True, exist_ok=True)
    path = stage_path(directory, "outline", must_exist=False)
    value = {
        "source": source,
        "lang": "zh",
        "params": {
            "episodes": episodes,
            "minutesPerEpisode": seconds / 60,
            "genre": genre,
            "adaptMode": adapt_mode,
            "preferences": [],
        },
        "adaptation": {"core": "", "keep": [], "cut": [], "merge": [], "risks": []},
        "characters": [],
        "scenes": [],
        "props": [],
        "beats": [],
        "episodes": [
            {
                "ep": ep,
                "synopsis": "",
                "hook": "",
                "suspense": "",
                "sceneIds": [],
                "characterIds": [],
                "propIds": [],
                "crowdPlan": "",
                "warnings": [],
            }
            for ep in range(1, episodes + 1)
        ],
    }
    # Exclusive creation prevents a retry from replacing a user's filled outline.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
    return {"status": "draft_seeded", "path": str(path), "validated": False}


def seed(directory, stage, timeout=30, *, replace_empty=False):
    if stage == "outline":
        raise ValueError("use init for the initial outline")
    manifest = directory / "production.json"
    if not manifest.resolve().is_relative_to(directory.resolve()):
        raise ValueError("production.json must stay inside the production directory")
    binding_hash = fingerprint(manifest)
    target = stage_path(directory, stage, must_exist=False)
    replace_target = False
    if target.exists():
        existing = read(target)
        empty_storyboard = stage == "storyboard" and all(
            isinstance(ep, dict) and ep.get("segments") == []
            for ep in existing.get("episodes", [])
        )
        if not replace_empty or not empty_storyboard:
            raise FileExistsError(f"refusing to overwrite {target}")
        replace_target = True
    upstream = "script" if stage == "storyboard" else "outline"
    path = stage_path(directory, upstream)
    problems = shape_errors(upstream, read(path))
    if problems:
        raise ValueError("invalid upstream shape: " + "; ".join(problems))
    node = shutil.which("node")
    if not node:
        raise ValueError("node is unavailable; no substitute seeder was run")
    tool = script_path(stage)
    hashes = {str(p): fingerprint(p) for p in (path, tool, directory / "production.json")}
    hashes[str(manifest)] = binding_hash
    command = [node, str(tool), "seed", str(path)]
    result = subprocess.run(
        command,
        cwd=directory,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.returncode:
        raise ValueError(f"native seed exited {result.returncode}: {result.stderr}")
    document = json.loads(result.stdout)
    problems = shape_errors(stage, document)
    if problems:
        raise ValueError("invalid native seed output: " + "; ".join(problems))
    if any(fingerprint(Path(p)) != h for p, h in hashes.items()):
        raise ValueError("upstream changed during seeding; retry after edits settle")
    target.parent.mkdir(parents=True, exist_ok=True)
    if replace_target:
        target.unlink()
    with target.open("x", encoding="utf-8") as file:
        json.dump(document, file, ensure_ascii=False, indent=2)
    return {
        "status": "draft_seeded",
        "path": str(target),
        "validated": False,
        "command": command,
        "input_hashes": hashes,
    }


def check_stage(
    directory,
    stage,
    source_text=None,
    timeout=30,
    *,
    require_jev=False,
    advise_jev=False,
    jev_model="jev-latest",
):
    tool = script_path(stage)
    row = {
        "stage": stage,
        "status": "blocked",
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "command": [],
        "input_hashes": {},
        "skipped_checks": ["exact_source_quotes"]
        if stage == "cast" and source_text is None
        else [],
    }
    try:
        manifest = directory / "production.json"
        if not manifest.resolve().is_relative_to(directory.resolve()):
            raise ValueError("production.json must stay inside the production directory")
        binding_hash = fingerprint(manifest)
        paths = {key: stage_path(directory, key) for key in (stage, *INPUTS[stage])}
        files = [*paths.values(), directory / "production.json"]
        if stage == "cast" and source_text is not None:
            files.append(source_text)
        row["input_hashes"] = {str(p): fingerprint(p) for p in files}
        row["input_hashes"][str(manifest)] = binding_hash
        row["validator_sha256"] = digest(tool)
        problems = []
        for key in (stage, *INPUTS[stage]):
            problems.extend(f"{key}: {p}" for p in shape_errors(key, read(paths[key])))
        if problems:
            row.update(status="invalid_shape", errors=problems)
            return row
        node = shutil.which("node")
        if not node:
            row["stderr"] = "node is unavailable; no substitute validator was run"
            return row
        command = [node, str(tool), "validate", str(files[0])]
        if stage == "cast" and source_text is not None:
            command.append(str(source_text))
        for key in INPUTS[stage]:
            command.extend([f"--{key}", str(paths[key])])
        if stage == "script":
            # The attributed transcript is what lets the script validator check
            # line order and speaker against the original instead of judging the
            # document only on its own internal coherence.
            attributed = directory / "source-transcript-attributed.json"
            rows_path = next(
                (p for p in source_transcript_candidates(directory) if p.is_file()), None
            )
            if rows_path is not None:
                # Checked on every run, before a cached attribution can be
                # reused: a transcript of another film must never reach the
                # validator as this production's source.
                identity, problem = transcript_identity(
                    directory, json.loads(rows_path.read_text(encoding="utf-8-sig"))
                )
                row["transcript_identity"] = identity
                _expected, _binding, material_path = bound_source_fingerprint(directory)
                if material_path is not None:
                    row["input_hashes"][str(material_path)] = fingerprint(material_path)
                if problem:
                    row.update(status="attribution_error", stderr=problem)
                    return row
                expected_hashes = {
                    "source_transcript": digest(rows_path),
                    "cast": digest(paths["cast"]),
                }
                try:
                    current = read(attributed) if attributed.is_file() else {}
                except (OSError, ValueError):
                    current = {}
                if current.get("input_sha256") != expected_hashes:
                    try:
                        attribution = run_attribution(directory, model=jev_model)
                    except Exception as exc:  # noqa: BLE001
                        row.update(
                            status="attribution_error", stderr=f"{type(exc).__name__}: {exc}"
                        )
                        return row
                    if attribution.get("status") != "attributed":
                        row.update(
                            status="attribution_error",
                            stderr=str(
                                attribution.get("reason")
                                or attribution.get("error")
                                or attribution
                            ),
                        )
                        return row
                row["input_hashes"][str(rows_path)] = expected_hashes["source_transcript"]
                command.extend(["--source", str(attributed)])
                files.append(attributed)
                row["input_hashes"][str(attributed)] = fingerprint(attributed)
            elif attributed.is_file():
                row.update(
                    status="attribution_error",
                    stderr="source transcript for attribution is missing",
                )
                return row
        if stage == "storyboard":
            command.append("--no-log")
        row["command"] = command
        result = subprocess.run(
            command,
            cwd=directory,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        row.update(
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            status="passed" if result.returncode == 0 else "failed",
        )
        if row["status"] == "passed" and (require_jev or advise_jev):
            try:
                from .jev_gates import run_stage_gate, write_receipt
            except ImportError:  # Direct CLI execution from the pipeline directory.
                from jev_gates import run_stage_gate, write_receipt
            receipt = run_stage_gate(stage, paths, model=jev_model, source_text=source_text)
            receipt["receipt_path"] = str(write_receipt(directory, receipt))
            row["jev"] = receipt
            row["next_action"] = (receipt.get("scheduler") or {}).get("next_action")
            # Advisory JEV only reports; the native validator alone decides the stage status.
            if receipt["status"] != "passed" and require_jev:
                row["status"] = f"jev_{receipt['status']}"
        if (
            any(fingerprint(Path(p)) != h for p, h in row["input_hashes"].items())
            or digest(tool) != row["validator_sha256"]
        ):
            row["status"] = "inputs_changed"
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or b""
        row.update(
            status="timeout",
            stderr=str(exc),
            stdout=partial.decode("utf-8", errors="replace")
            if isinstance(partial, bytes)
            else partial,
        )
    except (OSError, ValueError, TypeError) as exc:
        row.update(status="blocked", stderr=str(exc))
    return row


def check(
    directory,
    stage="all",
    source_text=None,
    timeout=30,
    *,
    require_jev=False,
    advise_jev=False,
    jev_model="jev-latest",
):
    report_dir = directory / ".cine-validation"
    report_dir.mkdir(exist_ok=True)
    if not report_dir.resolve().is_relative_to(directory.resolve()):
        raise ValueError("validation report directory must stay inside the production directory")
    stages = list(STAGES) if stage == "all" else [stage]
    rows = [
        check_stage(
            directory,
            s,
            source_text,
            timeout,
            require_jev=require_jev,
            advise_jev=advise_jev,
            jev_model=jev_model,
        )
        for s in stages
    ]
    for row in rows:
        if row["status"] == "passed":
            try:
                if any(fingerprint(Path(path)) != h for path, h in row["input_hashes"].items()):
                    row["status"] = "inputs_changed"
            except OSError:
                row["status"] = "inputs_changed"
    passed = all(r["status"] == "passed" for r in rows)
    skipped = any(r["skipped_checks"] for r in rows)
    jev_ran = require_jev or advise_jev
    jev_all_passed = all(row.get("jev", {}).get("status") == "passed" for row in rows)
    report = {
        "schema_version": 1,
        "run_id": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "native_validated"
        if passed and not skipped
        else "incomplete"
        if passed
        else "failed",
        "stages": rows,
        "production_authorized": False,
        "jev_required": require_jev,
        "jev_mode": "required" if require_jev else "advisory" if advise_jev else "off",
        # "advisory_findings": JEV ran without blocking and some stage did not pass it;
        # the stage rows carry each receipt for the report.
        "jev_status": (
            "passed"
            if jev_ran and jev_all_passed
            else "failed"
            if require_jev
            else "advisory_findings"
            if advise_jev
            else "not_required"
        ),
        "unverified": [
            "source_readiness",
            "upstream_pins",
            "adaptation_quality",
            "visual_quality",
            "provider_compatibility",
            "generated_media",
        ],
    }
    target = report_dir / f"{report['run_id']}.json"
    atomic_json(target, report)
    # This is a convenience pointer, not a cached authorization decision.
    atomic_json(report_dir / "latest.json", report)
    return {**report, "report_path": str(target)}


def finalize(
    directory,
    source_text=None,
    timeout=30,
    *,
    require_jev=False,
    advise_jev=False,
    jev_model="jev-latest",
):
    """Revalidate native artifacts, refresh the manifest graph, then hand off."""
    report = check(
        directory,
        "all",
        source_text,
        timeout,
        require_jev=require_jev,
        advise_jev=advise_jev,
        jev_model=jev_model,
    )
    if report["status"] != "native_validated":
        raise ValueError("native validation must pass before finalizing production pins")
    manifest_path = directory / "production.json"
    manifest = read(manifest_path)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("production.json artifacts are required")
    dependencies = {
        "source_material": (),
        "outline": ("source_material",),
        "cast": ("outline",),
        "art": ("outline",),
        "script": ("outline", "cast", "art"),
        "storyboard": ("script", "cast", "art"),
        "mapping": ("source_material", "script", "storyboard"),
    }
    if manifest.get("mode") == "original":
        dependencies.pop("source_material")
        dependencies["outline"] = ()
        dependencies["mapping"] = ("script", "storyboard")
    if set(artifacts) != set(dependencies):
        raise ValueError("production.json artifact set does not match its mode")
    hashes = {}
    for name, inputs in dependencies.items():
        relative = artifacts[name].get("path")
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError(f"invalid artifact path: {name}")
        path = (directory / relative).resolve(strict=True)
        if not path.is_relative_to(directory.resolve()):
            raise ValueError(f"artifact escapes production directory: {name}")
        hashes[name] = digest(path)
        artifacts[name]["sha256"] = hashes[name]
        artifacts[name]["inputs"] = {upstream: hashes[upstream] for upstream in inputs}
    atomic_json(manifest_path, manifest)
    try:
        from .handoff import validate
    except ImportError:  # Direct CLI execution from the pipeline directory.
        from handoff import validate

    result = validate(manifest_path)
    return {"status": "production_finalized", "native_report": report, "handoff": result}


def main(argv=None):
    # Windows inherits a legacy console code page even though every artifact
    # and child validator is UTF-8. Keep the CLI contract machine-readable.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init")
    init.add_argument("directory", type=Path)
    init.add_argument("--source", required=True)
    init.add_argument("--episodes", type=int, required=True)
    init.add_argument("--seconds", type=float, required=True)
    init.add_argument("--genre", required=True)
    init.add_argument("--adapt-mode", choices=["忠实", "抽核", "借壳"], required=True)
    attribute = sub.add_parser(
        "attribute",
        help="Assign ASR utterances to cast characters with JEV.",
    )
    attribute.add_argument("directory", type=Path)
    attribute.add_argument("--jev-model", default="jev-latest")
    for action in ("seed", "check"):
        parser = sub.add_parser(action)
        parser.add_argument("directory", type=Path)
        parser.add_argument(
            "--stage",
            choices=[*STAGES, "all"] if action == "check" else list(STAGES),
            default="all" if action == "check" else None,
            required=action == "seed",
        )
        if action == "seed":
            parser.add_argument(
                "--replace-empty",
                action="store_true",
                help="Replace only an untouched storyboard seed with empty segments.",
            )
        if action == "check":
            parser.add_argument("--source-text", type=Path)
            parser.add_argument(
                "--jev-advisory",
                action="store_true",
                help="Run the TypeSafe JEV semantic review and report it without blocking any stage.",
            )
            parser.add_argument(
                "--require-jev",
                action="store_true",
                help="Require a passing TypeSafe JEV semantic gate for every checked stage.",
            )
            parser.add_argument("--jev-model", default="jev-latest")
    rebind = sub.add_parser(
        "rebind-source",
        help="Re-export a bound source_material from its analysis project and repin only it.",
    )
    rebind.add_argument("directory", type=Path)
    rebind.add_argument("--source-project", type=Path, required=True)
    final = sub.add_parser("finalize")
    final.add_argument("directory", type=Path)
    final.add_argument("--source-text", type=Path)
    final.add_argument(
        "--jev-advisory",
        action="store_true",
        help="Run the TypeSafe JEV semantic review and report it without blocking finalization.",
    )
    final.add_argument(
        "--require-jev",
        action="store_true",
        help="Require a passing TypeSafe JEV semantic gate before refreshing pins.",
    )
    final.add_argument("--jev-model", default="jev-latest")
    args = p.parse_args(argv)
    try:
        directory = args.directory.resolve()
        if args.action == "init":
            result = init_outline(
                directory, args.source, args.episodes, args.seconds, args.genre, args.adapt_mode
            )
        elif args.action == "seed":
            result = seed(directory, args.stage, replace_empty=args.replace_empty)
        elif args.action == "attribute":
            result = run_attribution(directory, model=args.jev_model)
        elif args.action == "rebind-source":
            result = rebind_source(directory, args.source_project.resolve())
        elif args.action == "check":
            result = check(
                directory,
                args.stage,
                args.source_text.resolve() if args.source_text else None,
                require_jev=args.require_jev,
                advise_jev=args.jev_advisory,
                jev_model=args.jev_model,
            )
        else:
            result = finalize(
                directory,
                args.source_text.resolve() if args.source_text else None,
                require_jev=args.require_jev,
                advise_jev=args.jev_advisory,
                jev_model=args.jev_model,
            )
        print(json.dumps(result, ensure_ascii=False))
        return int(
            (args.action == "check" and result["status"] != "native_validated")
            or (args.action == "finalize" and result["status"] != "production_finalized")
            or (args.action == "rebind-source" and result["status"] != "source_rebound")
        )
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
