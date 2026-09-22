import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import jev_gates, production


class Answer:
    def __init__(self, **values):
        self.__dict__.update(values)

    def model_dump(self, mode="json"):
        return dict(self.__dict__)


class Response:
    model = "jev-test-1"

    def __init__(self, answers):
        self.answers = answers


class Client:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def system_one(self, **_kwargs):
        return self.response


def outline(path):
    path.write_text(
        json.dumps(
            {
                "source": "test",
                "adaptation": {"core": "a choice creates a consequence"},
                "characters": [{"id": "C01", "role": "protagonist"}],
                "scenes": [{"id": "S01", "intent": "discover the cost"}],
                "beats": [{"id": "B01", "action": "the door opens"}],
                "episodes": [{"ep": 1, "hook": "the door opens by itself", "synopsis": "one cause"}],
            }
        ),
        encoding="utf-8",
    )


def response(hook=0.9, causal=0.9, score=2.0, confidence=0.8):
    return Response(
        {
            "hook_visible": Answer(type="noul", noul=hook),
            "causal_progression": Answer(type="noul", noul=causal),
            "story_readiness": Answer(
                type="score", score=score, confidence=confidence, probabilities={"0": 0, "1": 0, "2": 1}
            ),
            "route_decision": Answer(type="choice", choice="advance", confidence=0.9),
            "next_stage": Answer(type="choice", choice="assets", confidence=0.9),
            "repair_scope": Answer(type="choice", choice="artifact", confidence=0.9),
            "retry_allowed": Answer(type="noul", noul=0.1),
        }
    )


def test_stage_gate_passes_and_records_actual_model(tmp_path):
    path = tmp_path / "outline.json"
    outline(path)
    result = jev_gates.run_stage_gate(
        "outline",
        {"outline": path},
        api_key="test-key",
        client_factory=lambda _key: Client(response()),
    )

    assert result["status"] == "passed"
    assert result["model"] == "jev-test-1"
    assert result["input_sha256"]["outline"]
    assert result["scheduler"]["next_action"] == "advance_to_assets"
    receipt = jev_gates.write_receipt(tmp_path, result)
    assert receipt.is_file()
    assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == "passed"


def test_stage_gate_blocks_negative_semantic_result(tmp_path):
    path = tmp_path / "outline.json"
    outline(path)
    result = jev_gates.run_stage_gate(
        "outline",
        {"outline": path},
        api_key="test-key",
        client_factory=lambda _key: Client(response(hook=0.2)),
    )

    assert result["status"] == "failed"
    assert any("hook_visible" in item for item in result["failures"])
    assert result["scheduler"]["decision"] == "repair"


def test_require_jev_without_credentials_is_a_real_block(monkeypatch, tmp_path):
    path = tmp_path / "outline.json"
    outline(path)
    monkeypatch.setattr(jev_gates, "_find_api_key", lambda: "")
    result = jev_gates.run_stage_gate("outline", {"outline": path})
    assert result["status"] == "unavailable"


def test_compact_preserves_dialogue_at_artifact_depth():
    """Dialogue sits deep in the document; truncating it makes causality unjudgeable.

    A depth cutoff short enough to reach the beat text silently deletes the
    lines a causality or fidelity judgment depends on, so the gate would pass
    everything. The floor below is the depth of a dialogue line in a real
    script artifact.
    """
    script = {
        "episodes": [
            {
                "ep": 1,
                "scenes": [
                    {
                        "sceneId": "S01",
                        "characters": ["C02", "C07"],
                        "flow": [
                            {"speaker": "C06", "line": "老夫夜观天象，看大姐你是命中带苦。"},
                            {"speaker": "C07", "line": "老婆，我升官了！"},
                            {"speaker": "C02", "line": "宝贝懂了吧？男人靠不住啊。"},
                        ],
                    }
                ],
            }
        ]
    }
    compacted = json.dumps(jev_gates._compact(script), ensure_ascii=False)
    for line in ("升官", "男人靠不住", "命中带苦"):
        assert line in compacted, f"{line} was truncated out of the JEV state"


def test_compact_bounds_pathological_input():
    """An unbounded document must not produce an unbounded request."""
    deep: dict = {}
    cursor = deep
    for _ in range(2000):
        cursor["n"] = {}
        cursor = cursor["n"]
    cursor["line"] = "deep"
    huge = {"deep": deep, "wide": list(range(20_000)), "long": "x" * 500_000}
    compacted = json.dumps(jev_gates._compact(huge), ensure_ascii=False)
    assert len(compacted) < 400_000


def test_source_gates_absent_without_source(tmp_path):
    """Without a source the model has nothing to compare against, so the
    source-fidelity questions must not be asked at all."""
    path = tmp_path / "outline.json"
    outline(path)
    questions = jev_gates._question_objects("script", include_source=False)
    assert "speaker_attribution_faithful" not in questions
    rules = jev_gates.gate_rules("script", include_source=False)
    assert "source_order_fidelity" not in rules

    questions = jev_gates._question_objects("script", include_source=True)
    assert "speaker_attribution_faithful" in questions
    assert "source_baseline_usable" in questions


def test_build_state_carries_source_material(tmp_path):
    path = tmp_path / "outline.json"
    outline(path)
    source = tmp_path / "source.txt"
    source.write_text("[00:00] 算命先生：老夫夜观天象。\n", encoding="utf-8")
    state, hashes = jev_gates.build_state("script", {"outline": path}, source_text=source)
    assert "老夫夜观天象" in state["source_material"]
    assert hashes["source_material"]


def test_unusable_baseline_blocks_instead_of_scoring_the_artifact(tmp_path):
    """A baseline with merged speaker labels must not yield a fidelity verdict.

    Scoring a script against a corrupt baseline marks it unfaithful for not
    reproducing the corruption. The gate reports `baseline_unusable` instead.
    """
    path = tmp_path / "outline.json"
    outline(path)
    source = tmp_path / "source.txt"
    source.write_text("[女儿] 男人靠不住啊。妈，你算命的钱还是爸爸出的呢！我升官了！\n", encoding="utf-8")

    answers = {
        "dialogue_naturalness": Answer(type="score", score=2.0, confidence=0.8),
        "causal_continuity": Answer(type="noul", noul=0.2),
        "visual_actionability": Answer(type="noul", noul=0.8),
        "source_baseline_usable": Answer(type="noul", noul=0.07),
        "speaker_attribution_faithful": Answer(type="noul", noul=0.2),
        "reaction_order_faithful": Answer(type="noul", noul=0.2),
        "setup_payoff_preserved": Answer(type="noul", noul=0.2),
        "source_order_fidelity": Answer(type="score", score=0.4, confidence=0.8),
        "route_decision": Answer(type="choice", choice="advance", confidence=0.9),
        "next_stage": Answer(type="choice", choice="storyboard", confidence=0.9),
        "repair_scope": Answer(type="choice", choice="artifact", confidence=0.9),
        "retry_allowed": Answer(type="noul", noul=0.1),
    }
    result = jev_gates.run_stage_gate(
        "script",
        {"outline": path},
        api_key="test-key",
        client_factory=lambda _key: Client(Response(answers)),
        source_text=source,
    )
    assert result["status"] == "baseline_unusable"
    assert result["source_compared"] is True
    assert set(result["evaluations"]) == {"source_baseline_usable"}
    assert result["scheduler"]["repair_scope"] == "upstream"
    assert result["scheduler"]["next_stage"] == "stop"


@pytest.mark.skipif(not shutil.which("node"), reason="native CLI requires node")
def test_production_report_records_jev_requirement(monkeypatch, tmp_path):
    source = next((production.SKILLS / "cine-outline/examples").glob("*-outline.json"))
    shutil.copyfile(source, tmp_path / "outline.json")
    monkeypatch.setattr(jev_gates, "_find_api_key", lambda: "")
    result = production.check(tmp_path, "outline", require_jev=True)
    assert result["status"] == "failed"
    assert result["jev_required"] is True
    assert result["stages"][0]["status"] == "jev_unavailable"
