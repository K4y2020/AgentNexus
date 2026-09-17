"""Read-only freshness and shot-mapping checks; not a production approval."""

import argparse
import hashlib
import json
import re
from pathlib import Path

DEPENDENCIES = {
    "source_material": (),
    "outline": (),
    "cast": ("outline",),
    "art": ("outline",),
    "script": ("outline", "cast", "art"),
    "storyboard": ("script", "cast", "art"),
    "mapping": ("script", "storyboard"),
}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def integer(value):
    return type(value) is int and value > 0


def unique(rows, key):
    require(isinstance(rows, list), "ROWS_REQUIRED")
    result = {}
    for row in rows:
        require(isinstance(row, dict), "ROW_INVALID")
        value = row.get(key)
        require(isinstance(value, (str, int)) and not isinstance(value, bool), "ID_INVALID")
        require(bool(value) and value not in result, "ID_MISSING_OR_DUPLICATE")
        result[value] = row
    return result


def source_material(project):
    """Copy current ledger/review fields without promoting unreviewed evidence."""
    root = Path(project).resolve(strict=True)
    input_hashes = {}

    def read(relative):
        file = (root / relative).resolve(strict=True)
        require(file.is_relative_to(root), "SOURCE_PATH_ESCAPE")
        raw = file.read_bytes()
        input_hashes[relative] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    revision = read("project.json").get("current_revision")
    require(
        isinstance(revision, str) and re.fullmatch(r"[A-Za-z0-9_-]+", revision),
        "COMMITTED_REVISION_REQUIRED",
    )
    source_id = read("source.json").get("source_id")
    require(isinstance(source_id, str) and bool(source_id), "SOURCE_ID_REQUIRED")
    shots = unique(read(f"revisions/{revision}/source_shots.json"), "shot_id")
    review_path = root / "reviews" / f"{revision}.json"
    reviews = unique(read(f"reviews/{revision}.json"), "shot_id") if review_path.exists() else {}
    require(set(reviews).issubset(shots), "REVIEW_SHOT_UNKNOWN")
    output = []
    for shot_id, shot in shots.items():
        require(
            shot.get("source_id") == source_id and shot.get("revision_id") == revision,
            "SOURCE_REVISION_MISMATCH",
        )
        review = reviews.get(shot_id)
        row = {
            "shot_id": shot_id,
            "source_interval": shot.get("interval"),
            "review_status": "unverified",
            "observations": [],
            "image_receipt_ids": [],
            "unknowns": ["identity, motion and audio require separate verification"],
        }
        if review:
            require(
                review.get("source_id") == source_id and review.get("revision_id") == revision,
                "REVIEW_REVISION_MISMATCH",
            )
            require(
                review.get("status") in ("model_reviewed", "disputed", "unreviewed", "unverified"),
                "REVIEW_STATUS_INVALID",
            )
            for field in ("observations", "image_receipt_ids"):
                values = review.get(field, [])
                require(
                    isinstance(values, list)
                    and all(isinstance(v, str) and v.strip() for v in values),
                    "REVIEW_FIELD_INVALID",
                )
                row[field] = values
            require(
                review["status"] != "model_reviewed"
                or (row["observations"] and row["image_receipt_ids"]),
                "SOURCE_REVIEW_INCOMPLETE",
            )
            if review["status"] in ("unreviewed", "unverified"):
                require(
                    not row["observations"] and not row["image_receipt_ids"],
                    "UNREVIEWED_EVIDENCE_CONFLICT",
                )
                row["review_status"] = "unverified"
            else:
                row["review_status"] = review["status"]
        output.append(row)
    result = {
        "status": "exported_unverified",
        "can_claim_reviewed": False,
        "source_id": source_id,
        "revision_id": revision,
        "shots": output,
        "receipt_authenticity": "not_checked_use_runtime_gate",
    }
    plan_relative = f"revisions/{revision}/story_plan.json"
    if (root / plan_relative).exists():
        plan = read(plan_relative)
        require(isinstance(plan, dict), "STORY_PLAN_INVALID")
        require(
            plan.get("source_id") == source_id and plan.get("revision_id") == revision,
            "STORY_REVISION_MISMATCH",
        )
        draft_relative = f"story/{revision}.json"
        result["adaptation_story_status"] = "missing"
        if (root / draft_relative).exists():
            draft = read(draft_relative)
            require(isinstance(draft, dict), "STORY_DRAFT_INVALID")
            require(
                draft.get("source_id") == source_id and draft.get("revision_id") == revision,
                "STORY_REVISION_MISMATCH",
            )
            result["adaptation_story"] = draft
            result["adaptation_story_status"] = "exported_unverified"
        result["adaptation_readiness"] = "not_checked_use_cine_verify_report"
    result["source_input_hashes"] = input_hashes
    return result


def validate(path, stage=None):
    path = Path(path).resolve(strict=True)
    root = path.parent
    manifest = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(manifest, dict), "MANIFEST_REQUIRED")
    require(
        type(manifest.get("schema_version")) is int and manifest["schema_version"] == 1,
        "SCHEMA_VERSION_UNSUPPORTED",
    )
    mode = manifest.get("mode")
    require(mode in ("original", "faithful", "adaptation"), "PRODUCTION_MODE_REQUIRED")
    artifacts = manifest.get("artifacts")
    require(isinstance(artifacts, dict), "ARTIFACTS_REQUIRED")
    deps = dict(DEPENDENCIES)
    if mode == "original":
        del deps["source_material"]
    else:
        deps["outline"] = ("source_material",)
        deps["mapping"] += ("source_material",)
    if stage is None:
        require(set(artifacts) == set(deps), "ARTIFACT_SET_MISMATCH")
    else:
        require(stage in ("outline", "cast", "art", "script"), "HANDOFF_STAGE_INVALID")
        required = set()

        def include(name):
            if name in required:
                return
            required.add(name)
            for upstream in deps[name]:
                include(upstream)

        include(stage)
        require(
            required.issubset(artifacts) and set(artifacts).issubset(deps), "ARTIFACT_SET_MISMATCH"
        )
        deps = {name: inputs for name, inputs in deps.items() if name in required}
    documents, hashes, paths = {}, {}, set()
    for name, inputs in deps.items():
        entry = artifacts[name]
        require(isinstance(entry, dict), f"ARTIFACT_INVALID:{name}")
        relative = entry.get("path")
        require(isinstance(relative, str) and bool(relative), f"PATH_REQUIRED:{name}")
        require(not Path(relative).is_absolute(), f"PATH_NOT_RELATIVE:{name}")
        file = (root / relative).resolve(strict=True)
        require(file.is_relative_to(root) and file.is_file(), f"PATH_ESCAPE:{name}")
        require(file not in paths and file != path, f"ARTIFACT_PATH_DUPLICATE:{name}")
        paths.add(file)
        raw = file.read_bytes()
        hashes[name] = hashlib.sha256(raw).hexdigest()
        require(entry.get("sha256") == hashes[name], f"ARTIFACT_CHANGED:{name}")
        documents[name] = json.loads(raw)
        pins = entry.get("inputs")
        require(isinstance(pins, dict) and set(pins) == set(inputs), f"INPUT_PINS_REQUIRED:{name}")
    for name, inputs in deps.items():
        for upstream in inputs:
            require(
                artifacts[name]["inputs"][upstream] == hashes[upstream],
                f"UPSTREAM_STALE:{name}:{upstream}",
            )

    source_shots = {}
    if mode != "original":
        material = documents["source_material"]
        require(isinstance(material, dict), "SOURCE_MATERIAL_REQUIRED")
        for key in ("source_id", "revision_id"):
            require(
                isinstance(material.get(key), str) and bool(material[key].strip()),
                f"SOURCE_IDENTITY_REQUIRED:{key}",
            )
        if "adaptation_story" in material:
            story = material["adaptation_story"]
            require(isinstance(story, dict), "STORY_DRAFT_INVALID")
            require(
                all(story.get(key) == material[key] for key in ("source_id", "revision_id")),
                "STORY_REVISION_MISMATCH",
            )
        source_shots = unique(material.get("shots"), "shot_id")
        for shot in source_shots.values():
            require(
                shot.get("review_status") in ("model_reviewed", "unverified", "disputed"),
                "SOURCE_REVIEW_STATUS_INVALID",
            )
            for key in ("observations", "image_receipt_ids", "unknowns"):
                values = shot.get(key)
                require(
                    isinstance(values, list)
                    and all(isinstance(v, str) and v.strip() for v in values),
                    f"SOURCE_FIELD_INVALID:{key}",
                )
            if shot["review_status"] == "model_reviewed":
                require(
                    shot["observations"] and shot["image_receipt_ids"], "SOURCE_REVIEW_INCOMPLETE"
                )

    if stage is not None:
        return {
            "status": "stage_inputs_validated",
            "stage": stage,
            "mode": mode,
            "checked_artifacts": list(deps),
            "checks": ["file_hashes", "upstream_pins"],
            "not_verified": [
                "source_readiness",
                "source_receipt_authenticity",
                "native_schemas",
                "semantic_quality",
                "downstream_artifacts",
                "generation_authorization",
            ],
        }

    script = documents["script"]
    board = documents["storyboard"]
    require(isinstance(script, dict) and isinstance(board, dict), "SCRIPT_STORYBOARD_REQUIRED")
    episodes = unique(script.get("episodes"), "ep")
    board_episodes = unique(board.get("episodes"), "ep")
    require(episodes and set(episodes) == set(board_episodes), "EPISODE_SCOPE_MISMATCH")
    cuts, covered = {}, set()
    expected_beats = set()
    for ep, episode in episodes.items():
        require(integer(ep), "EPISODE_ID_INVALID")
        scenes = episode.get("scenes")
        require(isinstance(scenes, list) and scenes, "SCENES_REQUIRED")
        for scene_index, scene in enumerate(scenes, 1):
            require(
                isinstance(scene, dict) and isinstance(scene.get("flow"), list) and scene["flow"],
                "SCENE_FLOW_REQUIRED",
            )
            expected_beats.update((ep, scene_index, i) for i in range(1, len(scene["flow"]) + 1))
        segments = unique(board_episodes[ep].get("segments"), "id")
        require(segments, "SEGMENTS_REQUIRED")
        for segment_id, segment in segments.items():
            scene_index = segment.get("sceneIndex")
            require(integer(scene_index) and scene_index <= len(scenes), "SCENE_REFERENCE_INVALID")
            rows = segment.get("cuts")
            require(isinstance(rows, list) and rows, "CUTS_REQUIRED")
            for index, cut in enumerate(rows, 1):
                require(isinstance(cut, dict), "CUT_INVALID")
                beats = cut.get("beats")
                require(
                    isinstance(beats, list)
                    and len(beats) == 2
                    and all(integer(b) for b in beats)
                    and beats[0] <= beats[1] <= len(scenes[scene_index - 1]["flow"]),
                    "BEAT_RANGE_INVALID",
                )
                claimed = {(ep, scene_index, b) for b in range(beats[0], beats[1] + 1)}
                require(not claimed.intersection(covered), "BEAT_COVERAGE_DUPLICATE")
                covered.update(claimed)
                cuts[(ep, segment_id, index)] = (scene_index, beats)
    require(covered == expected_beats, "BEAT_COVERAGE_INCOMPLETE")
    mappings = documents["mapping"]
    source_reference_warnings = []
    require(isinstance(mappings, list), "MAPPING_REQUIRED")
    mapped = set()
    for row in mappings:
        require(isinstance(row, dict), "MAPPING_ROW_INVALID")
        require(
            integer(row.get("ep"))
            and integer(row.get("cut"))
            and isinstance(row.get("segment_id"), str),
            "MAPPING_TARGET_INVALID",
        )
        key = (row["ep"], row["segment_id"], row["cut"])
        require(key in cuts and key not in mapped, "MAPPING_TARGET_MISSING_OR_DUPLICATE")
        require(
            isinstance(row.get("beats"), list)
            and all(integer(b) for b in row["beats"])
            and integer(row.get("scene_index"))
            and (row["scene_index"], row["beats"]) == cuts[key],
            "MAPPING_BEATS_MISMATCH",
        )
        mapped.add(key)
        kind = row.get("kind")
        require(kind in ("retained", "adapted", "new"), "MAPPING_KIND_INVALID")
        if mode == "faithful":
            require(kind == "retained", "FAITHFUL_CREATIVE_CHANGE")
        ids = row.get("source_shot_ids")
        require(
            isinstance(ids, list)
            and all(isinstance(s, str) for s in ids)
            and len(ids) == len(set(ids)),
            "SOURCE_REFERENCES_INVALID",
        )
        require(
            (kind == "new" and not ids) or (kind != "new" and bool(ids)),
            "SOURCE_REFERENCES_REQUIRED",
        )
        for source_id in ids:
            require(source_id in source_shots, "SOURCE_REFERENCE_UNKNOWN")
            if source_shots[source_id]["review_status"] != "model_reviewed":
                require(kind != "retained", "SOURCE_UNREVIEWED")
                source_reference_warnings.append(
                    {"source_shot_id": source_id, "status": "unverified_inspiration"}
                )
        for field in ("narrative_function", "creative_change"):
            if field == "creative_change" and kind == "retained":
                continue
            require(
                isinstance(row.get(field), str) and bool(row[field].strip()),
                f"MAPPING_FIELD_REQUIRED:{field}",
            )
    require(mapped == set(cuts), "MAPPING_COVERAGE_INCOMPLETE")
    return {
        "status": "handoff_validated",
        "mode": mode,
        "cut_count": len(cuts),
        "source_reference_warnings": source_reference_warnings,
        "checks": ["file_hashes", "upstream_pins", "beat_coverage", "source_mapping"],
        "not_verified": [
            "native_schemas",
            "source_receipt_authenticity",
            "semantic_quality",
            "remote_assets",
            "provider_compatibility",
            "generation_authorization",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, nargs="?")
    parser.add_argument("--stage", choices=["outline", "cast", "art", "script"])
    parser.add_argument(
        "--source-project", type=Path, help="Print editorial source material, read-only"
    )
    args = parser.parse_args(argv)
    if bool(args.manifest) == bool(args.source_project):
        parser.error("provide a manifest OR --source-project")
    if args.stage and args.source_project:
        parser.error("--stage applies only to a production manifest")
    try:
        result = (
            source_material(args.source_project)
            if args.source_project
            else validate(args.manifest, args.stage)
        )
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
