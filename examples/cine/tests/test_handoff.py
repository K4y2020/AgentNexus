import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import handoff


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path, mode="adaptation"):
    docs = {
        "source_material": {
            "source_id": "src",
            "revision_id": "r1",
            "shots": [
                {
                    "shot_id": "S1",
                    "review_status": "model_reviewed",
                    "observations": ["door"],
                    "image_receipt_ids": ["test-receipt-not-provider-proof"],
                    "unknowns": ["identity"],
                }
            ],
        },
        "outline": {},
        "cast": {},
        "art": {},
        "script": {"episodes": [{"ep": 1, "scenes": [{"flow": [{"action": "wait"}]}]}]},
        "storyboard": {
            "episodes": [
                {
                    "ep": 1,
                    "segments": [
                        {
                            "id": "E01-01",
                            "sceneIndex": 1,
                            "cuts": [{"beats": [1, 1], "seconds": 12}],
                        }
                    ],
                }
            ]
        },
        "mapping": [
            {
                "ep": 1,
                "segment_id": "E01-01",
                "cut": 1,
                "scene_index": 1,
                "beats": [1, 1],
                "kind": "adapted",
                "source_shot_ids": ["S1"],
                "narrative_function": "anticipation",
                "creative_change": "airlock",
            }
        ],
    }
    deps = handoff.stage_graph.provenance(mode)
    if mode == "original":
        del docs["source_material"]
        docs["mapping"][0].update(kind="new", source_shot_ids=[])
    if mode == "faithful":
        docs["mapping"][0]["kind"] = "retained"
    hashes = {name: write(tmp_path / f"{name}.json", doc) for name, doc in docs.items()}
    manifest = {
        "schema_version": 1,
        "mode": mode,
        "artifacts": {
            name: {
                "path": f"{name}.json",
                "sha256": hashes[name],
                "inputs": {dep: hashes[dep] for dep in deps[name]},
            }
            for name in docs
        },
    }
    path = tmp_path / "production.json"
    write(path, manifest)
    return path, manifest, docs


def repin(path, manifest, docs):
    for name, doc in docs.items():
        manifest["artifacts"][name]["sha256"] = write(path.parent / f"{name}.json", doc)
    for entry in manifest["artifacts"].values():
        entry["inputs"] = {k: manifest["artifacts"][k]["sha256"] for k in entry["inputs"]}
    write(path, manifest)


@pytest.mark.parametrize("mode", ["original", "faithful", "adaptation"])
def test_valid_handoff_is_not_production_pass(tmp_path, mode):
    path, _, _ = fixture(tmp_path, mode)
    result = handoff.validate(path)
    assert result["status"] == "handoff_validated"
    assert result["cut_count"] == 1
    assert "semantic_quality" in result["not_verified"]
    assert "source_receipt_authenticity" in result["not_verified"]


def test_file_changed_without_refresh(tmp_path):
    path, _, _ = fixture(tmp_path)
    write(tmp_path / "outline.json", {"changed": True})
    with pytest.raises(ValueError, match="ARTIFACT_CHANGED:outline"):
        handoff.validate(path)


def test_refreshing_upstream_hash_does_not_refresh_descendants(tmp_path):
    path, manifest, _ = fixture(tmp_path)
    manifest["artifacts"]["outline"]["sha256"] = write(
        tmp_path / "outline.json", {"changed": True}
    )
    write(path, manifest)
    with pytest.raises(ValueError, match="UPSTREAM_STALE:cast:outline"):
        handoff.validate(path)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda d: d["mapping"].clear(), "MAPPING_COVERAGE_INCOMPLETE"),
        (
            lambda d: d["mapping"].append(d["mapping"][0].copy()),
            "MAPPING_TARGET_MISSING_OR_DUPLICATE",
        ),
        (lambda d: d["mapping"][0].update(beats=[1, 2]), "MAPPING_BEATS_MISMATCH"),
        (lambda d: d["mapping"][0].update(source_shot_ids=["absent"]), "SOURCE_REFERENCE_UNKNOWN"),
        (lambda d: d["mapping"][0].update(narrative_function=""), "MAPPING_FIELD_REQUIRED"),
        (lambda d: d["mapping"][0].update(kind="new"), "SOURCE_REFERENCES_REQUIRED"),
        (
            lambda d: (
                d["source_material"]["shots"][0].update(review_status="unverified"),
                d["mapping"][0].update(kind="retained"),
            ),
            "SOURCE_UNREVIEWED",
        ),
        (
            lambda d: d["source_material"]["shots"][0].update(image_receipt_ids=[]),
            "SOURCE_REVIEW_INCOMPLETE",
        ),
        (
            lambda d: d["script"]["episodes"][0]["scenes"][0]["flow"].append({"action": "leave"}),
            "BEAT_COVERAGE_INCOMPLETE",
        ),
        (
            lambda d: d["storyboard"]["episodes"][0]["segments"][0]["cuts"].append(
                {"beats": [1, 1]}
            ),
            "BEAT_COVERAGE_DUPLICATE",
        ),
        (
            lambda d: d["storyboard"]["episodes"][0]["segments"][0].update(sceneIndex=True),
            "SCENE_REFERENCE_INVALID",
        ),
    ],
)
def test_invalid_mapping_fails_closed(tmp_path, mutation, code):
    path, manifest, docs = fixture(tmp_path)
    mutation(docs)
    repin(path, manifest, docs)
    with pytest.raises(ValueError, match=code):
        handoff.validate(path)


def test_faithful_cannot_relabel_new_shot(tmp_path):
    path, manifest, docs = fixture(tmp_path, "faithful")
    docs["mapping"][0].update(kind="new", source_shot_ids=[])
    repin(path, manifest, docs)
    with pytest.raises(ValueError, match="FAITHFUL_CREATIVE_CHANGE"):
        handoff.validate(path)


@pytest.mark.parametrize("value", [None, [], {"mode": "analysis"}, {"schema_version": True}])
def test_malformed_manifest_cli_blocks(tmp_path, value, capsys):
    path = tmp_path / "production.json"
    write(path, value)
    assert handoff.main([str(path)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"


def test_missing_upstream_not_optional(tmp_path):
    path, manifest, _ = fixture(tmp_path)
    del manifest["artifacts"]["script"]["inputs"]["art"]
    write(path, manifest)
    with pytest.raises(ValueError, match="INPUT_PINS_REQUIRED:script"):
        handoff.validate(path)


def test_path_escape(tmp_path):
    root = tmp_path / "production"
    root.mkdir()
    path, manifest, _ = fixture(root)
    write(tmp_path / "outside.json", {})
    manifest["artifacts"]["outline"]["path"] = "../outside.json"
    write(path, manifest)
    with pytest.raises(ValueError, match="PATH_ESCAPE"):
        handoff.validate(path)


def test_adapted_inspiration_does_not_require_every_source_shot_reviewed(tmp_path):
    path, manifest, docs = fixture(tmp_path, "adaptation")
    docs["source_material"]["shots"][0]["review_status"] = "unverified"
    repin(path, manifest, docs)
    result = handoff.validate(path)
    assert result["status"] == "handoff_validated"
    assert result["source_reference_warnings"] == [
        {"source_shot_id": "S1", "status": "unverified_inspiration"}
    ]


def source_fixture(tmp_path):
    (tmp_path / "revisions/r1").mkdir(parents=True)
    (tmp_path / "reviews").mkdir()
    write(tmp_path / "project.json", {"current_revision": "r1"})
    write(tmp_path / "source.json", {"source_id": "src"})
    write(
        tmp_path / "revisions/r1/source_shots.json",
        [
            {"shot_id": "s1", "source_id": "src", "revision_id": "r1", "interval": {"in_pts": 10}},
            {"shot_id": "s2", "source_id": "src", "revision_id": "r1", "interval": {"in_pts": 20}},
        ],
    )
    write(
        tmp_path / "revisions/r1/shot_notes.json",
        {
            "schema_version": 3,
            "source_id": "src",
            "revision_id": "r1",
            "status": "read",
            "unread_shots": [],
            "locations": [],
            "shots": [{"shot_id": "s1", "place": "?"}, {"shot_id": "s2", "place": "?"}],
        },
    )


def test_material_adapter_never_promotes_missing_reviews(tmp_path):
    source_fixture(tmp_path)
    before = (tmp_path / "revisions/r1/source_shots.json").read_bytes()
    result = handoff.source_material(tmp_path)
    assert [s["review_status"] for s in result["shots"]] == ["unverified", "unverified"]
    assert all(not s["observations"] for s in result["shots"])
    assert (tmp_path / "revisions/r1/source_shots.json").read_bytes() == before
    write(
        tmp_path / "reviews/r1.json",
        [
            {
                "shot_id": "s1",
                "source_id": "src",
                "revision_id": "r1",
                "status": "model_reviewed",
                "observations": ["door"],
                "image_receipt_ids": ["r"],
            }
        ],
    )
    result = handoff.source_material(tmp_path)
    assert result["shots"][0]["observations"] == ["door"]
    assert result["shots"][1]["review_status"] == "unverified"
    assert result["receipt_authenticity"] == "not_checked_use_runtime_gate"


def test_material_adapter_rejects_stale_review(tmp_path):
    source_fixture(tmp_path)
    write(
        tmp_path / "reviews/r1.json", [{"shot_id": "s1", "source_id": "src", "revision_id": "old"}]
    )
    with pytest.raises(ValueError, match="REVIEW_REVISION_MISMATCH"):
        handoff.source_material(tmp_path)


@pytest.mark.parametrize("status", ["unreviewed", "unverified"])
def test_empty_unreviewed_rows_are_compatible_not_promoted(tmp_path, status):
    source_fixture(tmp_path)
    review = {
        "shot_id": "s1",
        "source_id": "src",
        "revision_id": "r1",
        "status": status,
        "observations": [],
        "image_receipt_ids": [],
    }
    write(tmp_path / "reviews/r1.json", [review])
    before = (tmp_path / "reviews/r1.json").read_bytes()
    assert handoff.source_material(tmp_path)["shots"][0]["review_status"] == "unverified"
    assert (tmp_path / "reviews/r1.json").read_bytes() == before
    review["observations"] = ["unverified interpretation"]
    write(tmp_path / "reviews/r1.json", [review])
    with pytest.raises(ValueError, match="UNREVIEWED_EVIDENCE_CONFLICT"):
        handoff.source_material(tmp_path)


def test_material_exports_the_film_fingerprint_for_transcript_checks(tmp_path):
    source_fixture(tmp_path)
    fingerprint = {"size_bytes": 10, "sha256_head": "a" * 64, "sha256_tail": "b" * 64}
    write(tmp_path / "source.json", {"source_id": "src", **fingerprint, "duration_pts": 1})
    assert handoff.source_material(tmp_path)["source_fingerprint"] == fingerprint


def test_material_exports_the_source_visual_style_for_this_revision_only(tmp_path):
    source_fixture(tmp_path)
    assert "visual_style" not in handoff.source_material(tmp_path)
    reading = {
        "source_id": "src",
        "revision_id": "r1",
        "status": "read",
        "style": "photoreal",
        "evidence": "真实皮肤与布料",
        "model": "vision",
    }
    write(tmp_path / "revisions/r1/visual_style.json", reading)
    material = handoff.source_material(tmp_path)
    assert material["visual_style"] == {
        "style": "photoreal",
        "evidence": "真实皮肤与布料",
        "model": "vision",
    }
    assert "revisions/r1/visual_style.json" in material["source_input_hashes"]
    write(tmp_path / "revisions/r1/visual_style.json", {**reading, "revision_id": "r0"})
    assert "visual_style" not in handoff.source_material(tmp_path)
    write(tmp_path / "revisions/r1/visual_style.json", {**reading, "status": "unavailable"})
    assert "visual_style" not in handoff.source_material(tmp_path)


def test_material_without_a_complete_fingerprint_exports_none(tmp_path):
    source_fixture(tmp_path)
    write(tmp_path / "source.json", {"source_id": "src", "size_bytes": 10})
    assert "source_fingerprint" not in handoff.source_material(tmp_path)


def test_material_exports_locations_dialogue_indices_and_tracks_inputs(tmp_path):
    source_fixture(tmp_path)
    fingerprint = {"size_bytes": 10, "sha256_head": "a" * 64, "sha256_tail": "b" * 64}
    write(tmp_path / "source.json", {"source_id": "src", **fingerprint})
    notes = {
        "schema_version": 1,
        "source_id": "src",
        "revision_id": "r1",
        "status": "read",
        "model": "vision",
        "errors": [],
        "locations": [
            {"id": "L1", "name": "码头", "spans": [{"start": 0, "end": 17.6}], "seconds": 17.6},
            {"id": "L2", "name": "仓库", "spans": [{"start": 17.6, "end": 21.3}], "seconds": 3.7},
        ],
        "shots": [{"shot_id": "s1", "place": "L1"}, {"shot_id": "s2", "place": "L2"}],
    }
    path = tmp_path / "revisions/r1/shot_notes.json"
    write(path, notes)
    before = path.read_bytes()
    (tmp_path / "inputs").mkdir()
    transcript = tmp_path / "inputs/source-transcript.json"
    write(
        transcript,
        {
            "source_fingerprint": fingerprint,
            "segments": [
                {"text": "预告", "start": 0, "end": 1, "teaser": True},
                {"text": "开船", "start": 1, "end": 2},
                {"text": "你怎么又来了", "start": 17.6, "end": 19.5},
                {"text": "已经离开", "start": 21.3, "end": 22},
            ],
        },
    )
    exported = handoff.source_material(tmp_path)
    locations = exported["source_locations"]["locations"]
    assert [loc["line_numbers"] for loc in locations] == [[2], [3]]
    assert locations[1]["lines"][0]["text"] == "你怎么又来了"
    assert exported["source_locations"]["transcript_status"] == "matched"
    assert "revisions/r1/shot_notes.json" in exported["source_input_hashes"]
    assert str(transcript) in exported["source_input_hashes"]
    assert path.read_bytes() == before
    write(
        transcript,
        {
            "source_fingerprint": {**fingerprint, "size_bytes": 11},
            "segments": [{"start": 18, "text": "另一部片"}],
        },
    )
    exported = handoff.source_material(tmp_path)
    assert exported["source_locations"]["transcript_status"] == "mismatch"
    assert all(loc["line_numbers"] == [] for loc in exported["source_locations"]["locations"])


def test_material_exports_cross_cut_dialogue_to_substantial_locations(tmp_path):
    source_fixture(tmp_path)
    (tmp_path / "inputs").mkdir()
    fingerprint = {"size_bytes": 10, "sha256_head": "a" * 64, "sha256_tail": "b" * 64}
    write(tmp_path / "source.json", {"source_id": "src", **fingerprint})
    write(
        tmp_path / "revisions/r1/shot_notes.json",
        {
            "schema_version": 1,
            "source_id": "src",
            "revision_id": "r1",
            "status": "read",
            "locations": [
                {"id": "L1", "name": "停车场", "spans": [{"start": 0, "end": 3}], "seconds": 3},
                {"id": "L2", "name": "步道", "spans": [{"start": 3, "end": 8}], "seconds": 5},
            ],
            "shots": [{"shot_id": "s1", "place": "L1"}, {"shot_id": "s2", "place": "L2"}],
        },
    )
    write(
        tmp_path / "inputs/source-transcript.json",
        {
            "source_fingerprint": fingerprint,
            "segments": [
                {"text": "主体在步道", "start": 2.9, "end": 7.8},
                {"text": "横跨两个地点", "start": 2, "end": 4},
                {"text": "预告", "start": 2, "end": 4, "teaser": True},
                {"text": "缺少结束时间", "start": 1},
            ],
        },
    )
    locations = handoff.source_material(tmp_path)["source_locations"]["locations"]
    assert [loc["line_numbers"] for loc in locations] == [[2], [1, 2]]
    assert locations[0]["lines"][0]["end"] == 4
    assert locations[1]["lines"][0]["start"] == 2.9
    assert locations[1]["lines"][0]["end"] == 7.8


def test_material_rejects_stale_shot_notes(tmp_path):
    source_fixture(tmp_path)
    write(
        tmp_path / "revisions/r1/shot_notes.json",
        {
            "schema_version": 1,
            "source_id": "src",
            "revision_id": "old",
        },
    )
    with pytest.raises(ValueError, match="SHOT_NOTES_REVISION_MISMATCH"):
        handoff.source_material(tmp_path)


def test_material_refuses_shots_nobody_read(tmp_path):
    source_fixture(tmp_path)
    notes_path = tmp_path / "revisions/r1/shot_notes.json"
    notes = json.loads(notes_path.read_text(encoding="utf-8"))
    write(
        notes_path,
        {**notes, "status": "partial", "unread_shots": [1], "errors": ["shots 1-1: bad place"]},
    )
    with pytest.raises(ValueError, match=r"SHOT_NOTES_INCOMPLETE: 1 of 2 shots.*bad place"):
        handoff.source_material(tmp_path)
    notes_path.unlink()
    with pytest.raises(ValueError, match=r"SHOT_NOTES_INCOMPLETE: .*does not exist"):
        handoff.source_material(tmp_path)
    # A maintenance re-export of a legacy production may still read what exists.
    assert "source_locations" not in handoff.source_material(tmp_path, require_complete=False)


def test_material_refuses_an_unwritten_story_draft(tmp_path):
    source_fixture(tmp_path)
    plan = {
        "source_id": "src",
        "revision_id": "r1",
        "batches": [{"batch_id": "B001"}, {"batch_id": "B002"}],
    }
    write(tmp_path / "revisions/r1/story_plan.json", plan)
    with pytest.raises(ValueError, match=r"STORY_DRAFT_INCOMPLETE: .*does not exist"):
        handoff.source_material(tmp_path)
    skeleton = {
        "source_id": "src",
        "revision_id": "r1",
        "characters": [],
        "summary": {"premise": "", "conflict": "", "turning_points": [], "ending": ""},
        "sections": [{"batch_id": "B001", "events": []}, {"batch_id": "B002", "events": []}],
    }
    (tmp_path / "story").mkdir()
    write(tmp_path / "story/r1.json", skeleton)
    with pytest.raises(ValueError, match="STORY_DRAFT_INCOMPLETE") as failure:
        handoff.source_material(tmp_path)
    assert "summary_premise_missing" in str(failure.value)
    assert "section_incomplete:B002" in str(failure.value)
    written = {
        **skeleton,
        "forms": [],
        "characters": ["船夫"],
        "summary": {
            "premise": "雾天渡口。",
            "conflict": "有人想碰皮箱。",
            "turning_points": ["皮箱被拍住。"],
            "ending": "船驶进雾里。",
        },
        "sections": [
            {"batch_id": batch, "events": ["船夫撑船。"], "connection": "继续"}
            for batch in ("B001", "B002")
        ],
    }
    write(tmp_path / "story/r1.json", written)
    assert handoff.source_material(tmp_path)["adaptation_story_status"] == "exported_unverified"
