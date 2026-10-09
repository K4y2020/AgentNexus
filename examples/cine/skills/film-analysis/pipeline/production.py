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

try:
    from . import stage_graph
except ImportError:  # Direct CLI execution from the pipeline directory.
    import stage_graph

SKILLS = Path(__file__).resolve().parents[2]
# Helpers every stage script imports; a validation pins them like its inputs.
SHARED_SCRIPTS = (SKILLS / "_shared" / "cine-common.mjs",)
# Adaptation modes whose script is not compared line by line with the source.
FREE_ADAPT_MODES = {"借壳"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(path):
    return digest(path) if path.exists() or path.is_symlink() else None


# A write with the wrong encoding leaves a replacement character for every Chinese
# character (hundreds at once). One or two stray ones are harmless: Whisper emits them.
DAMAGED_MIN = 5
_ENCODING_FIX = (
    "Write it with the file tools, never a shell redirect (`>`, `Out-File`); source "
    "material comes from `handoff.py --source-project <project> --output <file>`."
)


def read_text(path):
    """A workspace text file as str, refusing what a wrong-encoding write leaves behind.

    On Windows a shell redirect re-encodes a program's output: as UTF-16, or through a
    code page the reader does not share. Chinese then arrives as replacement characters,
    and everything built from the file inherits the damage while every check still
    passes, so the file is refused at the read.
    """
    path = Path(path)
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        raise ValueError(
            f"{path.name} is UTF-16, which is what a PowerShell redirect writes. {_ENCODING_FIX}"
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"{path.name} is not valid UTF-8 (byte {exc.start}). {_ENCODING_FIX}"
        ) from exc
    damaged = text.count("\N{REPLACEMENT CHARACTER}")
    if damaged >= DAMAGED_MIN:
        raise ValueError(
            f"{path.name} holds {damaged} replacement characters (U+FFFD): its text was "
            f"damaged when it was written. {_ENCODING_FIX}"
        )
    return text


def read(path):
    return json.loads(read_text(path))


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

    exported = source_material(Path(source_project), require_complete=False)
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


def vision_model():
    try:
        from .transcribe import gateway_settings
    except ImportError:
        from transcribe import gateway_settings
    return gateway_settings()[2] or ""


def attribution_input_hashes(directory, rows_path, cast_path, *, mapper_model="jev-latest"):
    """Evidence and identity changes invalidate the same shared attribution."""
    hashes = {"source_transcript": digest(rows_path), "cast": digest(Path(cast_path))}
    hashes["source_material"] = fingerprint(source_material_path(directory))
    hashes["person_map"] = fingerprint(directory / "person-map.json")
    hashes["vision_model"] = vision_model()
    hashes["person_map_model"] = mapper_model
    for name in (
        "speaker_evidence.py",
        "attribute_speakers.py",
        "person_map.py",
        "line_judgment.py",
        "jev_client.py",
        "people_gate.py",
        "line_performance.py",
        "transcribe.py",
    ):
        hashes[name] = fingerprint(Path(__file__).with_name(name))
    return hashes


def spoken_rows(segments):
    """The transcript rows that are dialogue in their own right.

    A row inside the opening teaser (footage cut in from later) is heard again where
    it belongs, so it is neither attributed nor compared with the script.
    """
    return [s for s in segments or [] if not (isinstance(s, dict) and s.get("teaser"))]


def source_material_path(directory):
    """Where a production keeps its source material: its binding, else the usual name."""
    try:
        return stage_path(directory, "source_material")
    except (OSError, ValueError):
        # Without a manifest the material keeps its conventional hyphenated name.
        return directory / "source-material.json"


def run_attribution(directory, *, model="jev-latest"):
    """Merge each utterance's frame reading and bounded context judgment.

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
    source_rows = payload.get("segments") if isinstance(payload, dict) else payload
    try:
        from .transcribe import gateway_settings, split_repaired_rows
    except ImportError:
        from transcribe import gateway_settings, split_repaired_rows
    source_rows = split_repaired_rows(source_rows or [])
    segments = spoken_rows(source_rows)
    cast = read(Path(cast_path))
    try:
        from .person_map import ensure_person_map
    except ImportError:
        from person_map import ensure_person_map
    material_file = source_material_path(directory)
    material = read(material_file) if material_file.is_file() else {}
    notes = (
        material.get("source_locations")
        or (payload.get("visual_people") if isinstance(payload, dict) else None)
        or {}
    )
    try:
        from .line_performance import migrate_performance_inputs
    except ImportError:
        from line_performance import migrate_performance_inputs
    migrate_performance_inputs(notes, segments)
    mapping = ensure_person_map(directory, notes, cast_path, model=model)
    result = attribute_speakers(
        segments,
        cast,
        shot_notes=notes,
        person_map=mapping,
        model=model,
        context_rows=source_rows,
        vision_model=gateway_settings()[2] or "",
    )
    if "error" in result:
        return {"status": "error", **result}
    if len(result.get("attributions", [])) != len(segments):
        return {
            "status": "error",
            "reason": "speaker attribution did not cover every transcript segment",
        }
    result["input_sha256"] = attribution_input_hashes(
        directory, rows_path, cast_path, mapper_model=model
    )
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


def adapt_mode(directory):
    """The outline's adaptation mode (忠实 / 抽核 / 借壳), or None when unknown."""
    try:
        outline = read(stage_path(directory, "outline"))
    except (OSError, ValueError):
        return None
    params = outline.get("params") if isinstance(outline, dict) else None
    return params.get("adaptMode") if isinstance(params, dict) else None


def script_performance(directory, paths, attributed, jev_model):
    """A faithful script's delivery of each line against the emotion the source frames show."""
    try:
        from .performance_gate import check_performance
    except ImportError:  # Direct CLI execution from the pipeline directory.
        from performance_gate import check_performance

    rows = read(attributed).get("attributions") or []
    if not any(row.get("performance") for row in rows):
        return {"status": "skipped", "reason": "the source transcript records no line performance"}
    try:
        return check_performance(
            read(stage_path(directory, "script")),
            read(Path(paths["cast"])),
            rows,
            model=jev_model,
            cache_dir=directory / ".cine-validation" / "performance",
        )
    except Exception as exc:  # noqa: BLE001 - a broken check is a review item, not a verdict
        return {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}


def stage_locations(directory, stage, paths, input_hashes):
    """Location checks share the source binding and keep absent inputs in the cache key."""
    try:
        from . import location_gate
        from .source_identity import fingerprint_of
    except ImportError:
        import location_gate
        from source_identity import fingerprint_of

    for name in ("location_gate.py", "performance_gate.py"):
        path = Path(__file__).with_name(name)
        input_hashes[str(path)] = fingerprint(path)
    outline = read(paths["outline"] if stage == "script" else paths[stage])
    mode = (outline.get("params") or {}).get("adaptMode")
    if stage == "outline" and mode not in ("忠实", "抽核"):
        return location_gate.check_outline(outline, None)
    if stage == "script" and mode != "忠实":
        return location_gate.check_script(read(paths[stage]), outline, None, None)
    material_path = source_material_path(directory)
    input_hashes[str(material_path)] = fingerprint(material_path)
    material = read(material_path) if material_path.is_file() else None
    candidates = source_transcript_candidates(directory)
    input_hashes.update({str(path): fingerprint(path) for path in candidates})
    rows_path = next((p for p in candidates if p.is_file()), None)
    rows = None
    if rows_path is not None:
        input_hashes[str(rows_path)] = fingerprint(rows_path)
        payload = json.loads(rows_path.read_text(encoding="utf-8-sig"))
        expected = fingerprint_of((material or {}).get("source_fingerprint"))
        if expected is not None and fingerprint_of(payload.get("source_fingerprint")) == expected:
            rows = payload.get("segments")
        elif isinstance(material, dict) and isinstance(material.get("source_locations"), dict):
            material["source_locations"]["transcript_status"] = "mismatch"
    notes = (material or {}).get("source_locations")
    if rows is not None and isinstance(notes, dict):
        notes["transcript_status"] = "matched"
        location_gate.attach_dialogue(notes, rows)
    if stage == "outline":
        return location_gate.check_outline(outline, material)
    return location_gate.check_script(read(paths[stage]), outline, material, rows)


def storyboard_shot_states(directory, paths, input_hashes, jev_model):
    """Each shot's prompt text against the body position the storyboard plans for its people."""
    try:
        from .shot_state_gate import check_shot_states
    except ImportError:  # Direct CLI execution from the pipeline directory.
        from shot_state_gate import check_shot_states

    for name in ("shot_state_gate.py", "jev_client.py"):
        code = Path(__file__).with_name(name)
        input_hashes[str(code)] = fingerprint(code)
    try:
        return check_shot_states(
            read(paths["storyboard"]),
            read(paths["cast"]),
            outline=read(paths["outline"]) if "outline" in paths else None,
            model=jev_model,
            cache_dir=directory / ".cine-validation" / "shot-states",
        )
    except Exception as exc:  # noqa: BLE001 - a broken check is a review item, not a verdict
        return {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}


def storyboard_asset_anchors(_directory, paths):
    """Check in code that every referenced character/scene has a sheet or prompt.

    Uses the canvas importer's own rule (a byte-identical copy of its module),
    so this reports exactly what a later import_storyboard would refuse.
    """
    try:
        from . import asset_anchors as anchors
    except ImportError:  # Direct CLI execution from the pipeline directory.
        import asset_anchors as anchors

    storyboard = read(paths["storyboard"])
    segments = [
        (ep, seg)
        for ep in storyboard.get("episodes", [])
        for seg in ep.get("segments", [])
        if seg.get("id")
    ]
    cast = (paths["cast"], read(paths["cast"]))
    art = (paths["art"], read(paths["art"]))
    outline = (
        (paths["outline"], read(paths["outline"]))
        if anchors._needs_outline(cast[1], anchors._referenced_characters(segments))
        else None
    )
    problems = anchors._missing_visual_assets(
        segments,
        [],
        cast,
        art,
        script_doc=read(paths["script"]),
        cast_index=anchors._resolve_cast(cast[1], outline),
    )
    return {"status": "missing" if problems else "complete", "problems": problems}


def script_path(stage):
    name = stage_graph.STAGES[stage].skill
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
    try:
        from .form_gate import seed_forms
    except ImportError:  # Direct CLI execution from the pipeline directory.
        from form_gate import seed_forms
    material_path = source_material_path(directory)
    material = read(material_path) if material_path.is_file() else None
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
        "adaptation": {
            "core": "",
            "forms": seed_forms(material),
            "keep": [],
            "cut": [],
            "merge": [],
            "risks": [],
        },
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


def _seed_source_shots(directory, stage, document):
    """Put each scene's source shots in the seed, the file its writer reads first."""
    try:
        from .source_shots import add_source_shots
    except ImportError:  # Direct CLI execution from the pipeline directory.
        from source_shots import add_source_shots
    try:
        material = read(source_material_path(directory))
        outline = read(stage_path(directory, "outline"))
    except (OSError, ValueError):
        return 0
    rows = []
    attributed = directory / "source-transcript-attributed.json"
    if attributed.is_file():
        try:
            rows = read(attributed).get("attributions") or []
        except (OSError, ValueError, AttributeError):
            rows = []
    return add_source_shots(stage, document, material, outline, rows)


def _seed_forms(directory, document):
    """Put the shooting forms the outline keeps in the seed, the file its writer reads first."""
    try:
        from .form_gate import kept_forms
    except ImportError:  # Direct CLI execution from the pipeline directory.
        from form_gate import kept_forms
    try:
        kept = kept_forms(read(stage_path(directory, "outline")))
    except (OSError, ValueError):
        return
    if kept:
        document["keptForms"] = kept


def stage_forms(directory, paths, input_hashes):
    """The outline keeps or drops, with a reason, every shooting form the source uses."""
    try:
        from .form_gate import outline_form_errors
    except ImportError:  # Direct CLI execution from the pipeline directory.
        from form_gate import outline_form_errors
    code = Path(__file__).with_name("form_gate.py")
    input_hashes[str(code)] = fingerprint(code)
    material_path = source_material_path(directory)
    input_hashes[str(material_path)] = fingerprint(material_path)
    material = read(material_path) if material_path.is_file() else None
    errors = outline_form_errors(read(paths["outline"]), material)
    return {"status": "failed" if errors else "passed", "blocking": errors}


def storyboard_pov(directory, paths, input_hashes, jev_model):
    """A kept first-person POV never draws its holder's face."""
    try:
        from .form_gate import kept_pov_holder
        from .pov_gate import check_pov
    except ImportError:  # Direct CLI execution from the pipeline directory.
        from form_gate import kept_pov_holder
        from pov_gate import check_pov

    for name in ("form_gate.py", "pov_gate.py", "shot_state_gate.py", "jev_client.py"):
        code = Path(__file__).with_name(name)
        input_hashes[str(code)] = fingerprint(code)
    holder = kept_pov_holder(read(paths["outline"])) if "outline" in paths else None
    if not holder:
        return {"status": "skipped", "reason": "no kept POV form"}
    names = {c.get("id"): c.get("name") for c in read(paths["cast"]).get("characters") or []}
    try:
        return check_pov(
            read(paths["storyboard"]),
            holder,
            holder_name=names.get(holder),
            model=jev_model,
            cache_dir=directory / ".cine-validation" / "pov",
        )
    except Exception as exc:  # noqa: BLE001 - a broken check is a review item, not a verdict
        return {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}


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
    upstream = stage_graph.STAGES[stage].seed_from
    path = stage_path(directory, upstream)
    problems = shape_errors(upstream, read(path))
    if problems:
        raise ValueError("invalid upstream shape: " + "; ".join(problems))
    node = shutil.which("node")
    if not node:
        raise ValueError("node is unavailable; no substitute seeder was run")
    tool = script_path(stage)
    hashes = {
        str(p): fingerprint(p)
        for p in (path, tool, *SHARED_SCRIPTS, directory / "production.json")
    }
    hashes[str(manifest)] = binding_hash
    command = [node, str(tool), "seed", str(path)]
    if stage == "storyboard":
        bindings = read(manifest).get("artifacts", {}) if manifest.is_file() else None
        for reference in ("cast", "art"):
            if bindings is not None and reference not in bindings:
                continue
            reference_path = stage_path(directory, reference, must_exist=False)
            if reference_path.is_file():
                command.extend([f"--{reference}", str(reference_path)])
                hashes[str(reference_path)] = fingerprint(reference_path)
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
    if stage in ("script", "storyboard"):
        _seed_source_shots(directory, stage, document)
        _seed_forms(directory, document)
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
    jev_model="jev-latest",
):
    tool = script_path(stage)
    inputs = stage_graph.STAGES[stage].validator_inputs
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
        paths = {key: stage_path(directory, key) for key in (stage, *inputs)}
        files = [*paths.values(), directory / "production.json"]
        if stage in ("cast", "script") and source_text is not None:
            source_text = Path(source_text)
            files.append(source_text)
        row["input_hashes"] = {str(p): fingerprint(p) for p in files}
        row["input_hashes"][str(manifest)] = binding_hash
        row["input_hashes"].update({str(p): fingerprint(p) for p in SHARED_SCRIPTS})
        row["validator_sha256"] = digest(tool)
        if (
            stage in ("cast", "script")
            and source_text is not None
            and not read_text(source_text).strip()
        ):
            raise ValueError(f"--source-text is empty: {source_text}")
        problems = []
        for key in (stage, *inputs):
            try:
                document = read(paths[key])
            except json.JSONDecodeError as exc:
                if key != stage:
                    raise
                # The file being written is broken JSON: the agent repairs it, not a person.
                row.update(
                    status="invalid_shape",
                    errors=[f"{key}: {paths[key].name} 不是合法 JSON（{exc}），先修正语法再校验"],
                )
                return row
            problems.extend(f"{key}: {p}" for p in shape_errors(key, document))
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
        for key in inputs:
            command.extend([f"--{key}", str(paths[key])])
        script_source = None
        free_adaptation = stage == "script" and adapt_mode(directory) in FREE_ADAPT_MODES
        if free_adaptation:
            # 借壳 keeps the premise and rewrites the rest: the source's speakers,
            # order and lines are not what this script is meant to reproduce.
            row["source_comparison"] = {
                "status": "skipped",
                "reason": "free_adaptation",
                "scope": "source_dialogue",
                "source_text": str(source_text) if source_text is not None else None,
            }
        if stage == "script" and not free_adaptation:
            # The attributed transcript is what lets the script validator check
            # line order and speaker against the original instead of judging the
            # document only on its own internal coherence.
            attributed = directory / "source-transcript-attributed.json"
            candidates = source_transcript_candidates(directory)
            row["input_hashes"].update({str(p): fingerprint(p) for p in (*candidates, attributed)})
            rows_path = next((p for p in candidates if p.is_file()), None)
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
                expected_hashes = attribution_input_hashes(
                    directory, rows_path, paths["cast"], mapper_model=jev_model
                )
                try:
                    current = read(attributed) if attributed.is_file() else {}
                except (OSError, ValueError):
                    current = {}
                row["vision_model"] = expected_hashes["vision_model"]
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
                script_source = attributed
                files.append(attributed)
                row["input_hashes"][str(attributed)] = fingerprint(attributed)
                row["source_comparison"] = {
                    "status": "unchecked",
                    "reason": "native_check_not_completed",
                    "scope": "source_dialogue",
                    "source_text": str(source_text) if source_text is not None else None,
                    "source_transcript": str(attributed),
                    "transcript_identity": identity,
                    "message": "剧本原稿对账尚未完成：原生校验器还没有返回结果。",
                }
            elif attributed.is_file():
                row.update(
                    status="attribution_error",
                    stderr="source transcript for attribution is missing",
                )
                return row
            elif (
                source_text is None
                and manifest.is_file()
                and read(manifest).get("mode") == "original"
            ):
                row["source_comparison"] = {
                    "status": "skipped",
                    "reason": "original_without_source",
                    "scope": "source_dialogue",
                    "source_text": None,
                }
            else:
                row["source_comparison"] = {
                    "status": "unchecked",
                    "reason": "plain_text_requires_attributed_transcript"
                    if source_text is not None
                    else "missing_source_transcript",
                    "scope": "source_dialogue",
                    "source_text": str(source_text) if source_text is not None else None,
                    "message": (
                        "剧本原稿对账未检查：没有可用的角色归属转录 JSON。"
                        "纯文本不能核验说话人、台词顺序和台词保留；"
                        "请提供与绑定源片一致的 source-transcript.json 后重新归属、校验，"
                        "或人工核对纯文本原稿。"
                    ),
                }
        if stage == "storyboard":
            # A production ends on the canvas, where a video card references only the
            # character sheets and the scene art: the prompt must define and use each one.
            command.extend(["--no-log", "--ref2va"])
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
        if script_source is not None:
            comparison = row["source_comparison"]
            comparison.update(status="checked", native_exit_code=result.returncode)
            comparison.pop("reason")
            comparison.pop("message")
        if stage == "script" and adapt_mode(directory) == "抽核" and script_source is not None:
            reviewed = subprocess.run(
                [*command[:2], "source-review", *command[3:]],
                cwd=directory,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            if reviewed.returncode:
                row.update(status="failed", stderr=reviewed.stderr)
            else:
                row["source_review"] = json.loads(reviewed.stdout)
        if stage in ("outline", "script"):
            row["locations"] = stage_locations(directory, stage, paths, row["input_hashes"])
            if row["locations"]["status"] == "failed":
                row.update(status="failed", errors=row["locations"]["blocking"])
        if stage == "outline" and source_material_path(directory).is_file():
            row["forms"] = stage_forms(directory, paths, row["input_hashes"])
            if row["forms"]["status"] == "failed":
                row.update(
                    status="failed", errors=[*row.get("errors", []), *row["forms"]["blocking"]]
                )
        if stage == "script" and script_source is not None:
            try:
                from .people_gate import check_people
            except ImportError:
                from people_gate import check_people
            material_file = source_material_path(directory)
            material = read(material_file) if material_file.is_file() else {}
            person_map_file = directory / "person-map.json"
            mapping = read(person_map_file) if person_map_file.is_file() else {}
            row["input_hashes"][str(person_map_file)] = fingerprint(person_map_file)
            for name in (
                "people_gate.py",
                "form_gate.py",
                "speaker_evidence.py",
                "person_map.py",
                "line_judgment.py",
                "jev_client.py",
                "attribute_speakers.py",
                "line_performance.py",
            ):
                code = Path(__file__).with_name(name)
                row["input_hashes"][str(code)] = fingerprint(code)
            row["people"] = check_people(
                read(paths[stage]),
                read(paths["outline"]),
                material,
                read(paths["cast"]),
                mapping,
                read(script_source).get("attributions") or [],
            )
            if row["people"]["status"] == "failed":
                row.update(
                    status="failed", errors=[*row.get("errors", []), *row["people"]["blocking"]]
                )
        if stage == "storyboard" and row["status"] == "passed":
            row["asset_anchors"] = storyboard_asset_anchors(directory, paths)
            if row["asset_anchors"]["status"] == "missing":
                row["status"] = "assets_missing"
        # Also after a native failure: one repair then fixes both kinds of errors.
        if stage == "storyboard" and row["status"] in ("passed", "failed"):
            row["shot_states"] = storyboard_shot_states(
                directory, paths, row["input_hashes"], jev_model
            )
            if row["shot_states"]["status"] == "failed":
                row.update(
                    status="failed",
                    errors=[*row.get("errors", []), *row["shot_states"]["blocking"]],
                )
            row["pov"] = storyboard_pov(directory, paths, row["input_hashes"], jev_model)
            if row["pov"]["status"] == "failed":
                row.update(
                    status="failed", errors=[*row.get("errors", []), *row["pov"]["blocking"]]
                )
        if (
            stage == "script"
            and row["status"] == "passed"
            and script_source is not None
            and adapt_mode(directory) == "忠实"
        ):
            row["performance"] = script_performance(directory, paths, script_source, jev_model)
            if row["performance"]["status"] == "failed":
                row.update(
                    status="failed",
                    errors=[*row.get("errors", []), *row["performance"]["blocking"]],
                )
        if (
            any(fingerprint(Path(p)) != h for p, h in row["input_hashes"].items())
            or digest(tool) != row["validator_sha256"]
            or ("vision_model" in row and row["vision_model"] != vision_model())
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
    jev_model="jev-latest",
):
    report_dir = directory / ".cine-validation"
    report_dir.mkdir(exist_ok=True)
    if not report_dir.resolve().is_relative_to(directory.resolve()):
        raise ValueError("validation report directory must stay inside the production directory")
    stages = list(stage_graph.ORDER) if stage == "all" else [stage]
    rows = [
        check_stage(
            directory,
            s,
            source_text,
            timeout,
            jev_model=jev_model,
        )
        for s in stages
    ]
    for row in rows:
        if row["status"] == "passed":
            try:
                if any(
                    fingerprint(Path(path)) != h for path, h in row["input_hashes"].items()
                ) or ("vision_model" in row and row["vision_model"] != vision_model()):
                    row["status"] = "inputs_changed"
            except OSError:
                row["status"] = "inputs_changed"
    passed = all(r["status"] == "passed" for r in rows)
    skipped = any(r["skipped_checks"] for r in rows)
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
        "unverified": [
            *(
                ["source_dialogue"]
                if any(r.get("source_comparison", {}).get("status") == "unchecked" for r in rows)
                else []
            ),
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
    jev_model="jev-latest",
):
    """Revalidate native artifacts, refresh the manifest graph, then hand off."""
    report = check(
        directory,
        "all",
        source_text,
        timeout,
        jev_model=jev_model,
    )
    if report["status"] != "native_validated":
        raise ValueError("native validation must pass before finalizing production pins")
    manifest_path = directory / "production.json"
    manifest = read(manifest_path)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("production.json artifacts are required")
    dependencies = stage_graph.provenance(manifest.get("mode"))
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
            choices=[*stage_graph.ORDER, "all"] if action == "check" else list(stage_graph.ORDER),
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
            parser.add_argument("--jev-model", default="jev-latest")
    rebind = sub.add_parser(
        "rebind-source",
        help="Re-export a bound source_material from its analysis project and repin only it.",
    )
    rebind.add_argument("directory", type=Path)
    rebind.add_argument("--source-project", type=Path, required=True)
    step = sub.add_parser(
        "next",
        help="Return the next action of an unattended production run (autopilot).",
    )
    step.add_argument("directory", type=Path)
    step.add_argument("--source-text", type=Path)
    step.add_argument(
        "--retry", action="store_true", help="Clear escalations after the cause was fixed."
    )
    step.add_argument(
        "--canvas-synced",
        type=Path,
        metavar="RECEIPT",
        help="Record a successful import_storyboard receipt for the current storyboard.",
    )
    step.add_argument(
        "--text-only",
        action="store_true",
        help="The user wants the text files only: finish with done instead of a canvas import.",
    )
    final = sub.add_parser("finalize")
    final.add_argument("directory", type=Path)
    final.add_argument("--source-text", type=Path)
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
        elif args.action == "next":
            try:
                from . import autopilot
            except ImportError:  # Direct CLI execution from the pipeline directory.
                import autopilot
            if args.canvas_synced:
                result = autopilot.record_canvas_sync(directory, args.canvas_synced.resolve())
            else:
                result = autopilot.next_step(
                    directory,
                    source_text=args.source_text.resolve() if args.source_text else None,
                    retry=args.retry,
                    text_only=args.text_only,
                )
        elif args.action == "check":
            result = check(
                directory,
                args.stage,
                args.source_text.resolve() if args.source_text else None,
                jev_model=args.jev_model,
            )
        else:
            result = finalize(
                directory,
                args.source_text.resolve() if args.source_text else None,
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
