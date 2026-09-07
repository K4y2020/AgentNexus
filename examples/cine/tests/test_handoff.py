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
    deps = dict(handoff.DEPENDENCIES)
    if mode == "original":
        del docs["source_material"]
        del deps["source_material"]
        docs["mapping"][0].update(kind="new", source_shot_ids=[])
    else:
        deps["outline"] = ("source_material",)
        deps["mapping"] += ("source_material",)
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
