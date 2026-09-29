import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

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
            "next_stage": Answer(type="choice", choice="cast", confidence=0.9),
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
    assert result["scheduler"]["next_action"] == "advance_to_cast"
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


def routed_gate(tmp_path, stage, *, decision, next_stage, scope="artifact", passing=True):
    """Run a stage gate whose quality answers all pass (or all fail) with the given route."""
    path = tmp_path / "artifact.json"
    outline(path)
    value = 0.9 if passing else 0.1
    answers = {
        name: Answer(type="score", score=2.0 if passing else 0.0, confidence=0.8)
        if kind == "score"
        else Answer(type="noul", noul=value)
        for name, (kind, _) in jev_gates.STAGE_GATES[stage]["questions"].items()
    }
    answers.update(
        route_decision=Answer(type="choice", choice=decision, confidence=0.9),
        next_stage=Answer(type="choice", choice=next_stage, confidence=0.9),
        repair_scope=Answer(type="choice", choice=scope, confidence=0.9),
        retry_allowed=Answer(type="noul", noul=0.9),
    )
    return jev_gates.run_stage_gate(
        stage,
        {stage: path},
        api_key="test-key",
        client_factory=lambda _key: Client(Response(answers)),
    )["scheduler"]


def test_route_graph_matches_production_dependencies():
    assert jev_gates.UPSTREAM_STAGES == {stage: set(inputs) for stage, inputs in production.INPUTS.items()}
    choices = set(jev_gates.SUPERVISOR_QUESTIONS["next_stage"][1])
    for stage, targets in jev_gates.NEXT_STAGES.items():
        assert targets <= choices
        assert stage in choices
        # The next stage may not be one the current stage depends on.
        assert not targets & jev_gates.UPSTREAM_STAGES[stage]


def test_passed_cast_advances_to_art_not_script(tmp_path):
    """Art reads the cast and must exist before the script, so cast cannot hand off to script."""
    scheduler = routed_gate(tmp_path, "cast", decision="advance", next_stage="script")
    assert scheduler["next_action"] == "advance_to_art"
    assert "invalid_next_stage:script" in scheduler["reasons"]


def test_artifact_repair_targets_the_current_stage(tmp_path):
    scheduler = routed_gate(tmp_path, "script", decision="repair", next_stage="canvas", passing=False)
    assert scheduler["next_action"] == "repair_artifact"
    assert scheduler["next_stage"] == "script"
    assert "invalid_repair_stage:canvas" in scheduler["reasons"]


def test_failed_gate_that_proposed_advancing_repairs_in_place(tmp_path):
    scheduler = routed_gate(tmp_path, "script", decision="advance", next_stage="storyboard", passing=False)
    assert scheduler["decision"] == "repair"
    assert scheduler["next_stage"] == "script"


def test_upstream_repair_keeps_a_real_dependency(tmp_path):
    scheduler = routed_gate(
        tmp_path, "script", decision="repair", next_stage="art", scope="upstream", passing=False
    )
    assert scheduler["next_action"] == "repair_upstream"
    assert scheduler["next_stage"] == "art"
    assert scheduler["reasons"] == []


@pytest.mark.parametrize("proposed", ["storyboard", "canvas", "script"])
def test_upstream_repair_rejects_non_dependencies(tmp_path, proposed):
    scheduler = routed_gate(
        tmp_path, "script", decision="repair", next_stage=proposed, scope="upstream", passing=False
    )
    assert scheduler["next_stage"] == "stop"
    assert f"invalid_repair_stage:{proposed}" in scheduler["reasons"]


def test_upstream_prompts_become_presence_markers(tmp_path):
    """Upstream sheets keep only their presence; the stage's own prompts stay intact."""
    cast = tmp_path / "cast.json"
    cast.write_text(
        json.dumps(
            {
                "characters": [
                    {"id": "C01", "name": "甲", "image": {"sheet": "full body sheet " * 50, "prompt": ""}},
                    {"id": "C02", "name": "乙", "image": {"sheet": ""}},
                ]
            }
        ),
        encoding="utf-8",
    )
    art = tmp_path / "art.json"
    art.write_text(json.dumps({"scenes": [{"id": "S01", "image": {"prompt": "rainy alley"}}]}), encoding="utf-8")
    state, _ = jev_gates.build_state("art", {"art": art, "cast": cast})
    characters = state["artifacts"]["cast"]["characters"]
    assert characters[0]["image"]["sheet"].startswith("[generation prompt present:")
    assert characters[0]["image"]["prompt"] == ""
    assert characters[1]["image"]["sheet"] == ""
    assert state["artifacts"]["art"]["scenes"][0]["image"]["prompt"] == "rainy alley"
    assert state["compaction"]["upstream_prompts_projected"] == ["cast"]
    assert "upstream_note" in state


def test_state_stays_within_one_shared_budget(tmp_path):
    """Oversized inputs are cut to one budget, spending it on the stage's own artifact first."""
    script = tmp_path / "script.json"
    flow = [{"speaker": "C01", "line": f"第{i}句台词，说清楚为什么。"} for i in range(60)]
    script.write_text(json.dumps({"episodes": [{"scenes": [{"flow": flow}]}]}, ensure_ascii=False), encoding="utf-8")
    cast = tmp_path / "cast.json"
    personas = [{"id": f"C{i:02d}", "persona": {"motivation": "动机" * 1400}} for i in range(40)]
    cast.write_text(json.dumps({"characters": personas}, ensure_ascii=False), encoding="utf-8")
    source = tmp_path / "source.txt"
    source.write_text("[甲] 原文台词。\n" * 4000, encoding="utf-8")

    state, _ = jev_gates.build_state("script", {"script": script, "cast": cast}, source_text=source)
    compaction = state["compaction"]
    assert compaction["estimated_tokens"] <= jev_gates.STATE_TOKEN_BUDGET * 1.05
    assert "script" not in compaction["truncated"]
    assert {"cast", "source_material"} <= set(compaction["truncated"])
    assert json.dumps(state["artifacts"]["script"], ensure_ascii=False).count("台词") == 60
    # The transcript is weighted above upstream documents and is cut, not dropped.
    assert len(state["source_material"]) > 5000
    assert "truncation_note" in state
    assert jev_gates._estimate_tokens(json.dumps(state, ensure_ascii=False)) < 30_000


def test_storyboard_sees_asset_identities_not_characterization(tmp_path):
    cast = tmp_path / "cast.json"
    cast.write_text(
        json.dumps({"characters": [{"id": "C01", "name": "甲", "persona": {"motivation": "长篇人物动机"},
                                    "states": [{"id": "wet"}], "image": {"sheet": "sheet"}}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    storyboard = tmp_path / "storyboard.json"
    storyboard.write_text(json.dumps({"episodes": []}), encoding="utf-8")
    state, _ = jev_gates.build_state("storyboard", {"storyboard": storyboard, "cast": cast})
    character = state["artifacts"]["cast"]["characters"][0]
    assert "persona" not in character
    assert character["states"] == [{"id": "wet"}]
    assert character["image"]["sheet"].startswith("[generation prompt present:")
    assert "persona" in state["compaction"]["upstream_fields_dropped"]["cast"]
    # The script stage still judges dialogue against characterization.
    script = tmp_path / "script.json"
    script.write_text(json.dumps({"episodes": []}), encoding="utf-8")
    state, _ = jev_gates.build_state("script", {"script": script, "cast": cast})
    assert "persona" in state["artifacts"]["cast"]["characters"][0]


def test_structure_adds_to_the_token_estimate():
    flat = {"text": "x" * 3000}
    nested = {"items": [{"a": {"b": "x" * 3}} for _ in range(300)]}
    assert jev_gates._estimate_value_tokens(nested) > jev_gates._estimate_value_tokens(flat)


def test_budget_keeps_small_upstream_whole_beside_a_large_artifact():
    """A large storyboard must not crowd out the cast and art it is checked against."""
    allowances = jev_gates._share_budget(
        {"storyboard": 50_000, "cast": 3_000, "art": 2_000},
        {"storyboard": 2, "cast": 1, "art": 1},
        26_000,
    )
    assert allowances["cast"] == 3_000 and allowances["art"] == 2_000
    assert allowances["storyboard"] == 21_000


def test_token_overflow_retries_once_with_a_smaller_state(tmp_path):
    path = tmp_path / "outline.json"
    outline(path)
    calls = []

    class Overflow(Client):
        def system_one(self, **kwargs):
            calls.append(kwargs["state"]["compaction"]["token_budget"])
            if len(calls) == 1:
                raise RuntimeError('400 {"detail":{"error_type":"max_tokens_exceeded"}}')
            return self.response

    result = jev_gates.run_stage_gate(
        "outline",
        {"outline": path},
        api_key="test-key",
        client_factory=lambda _key: Overflow(response()),
    )
    assert result["status"] == "passed"
    assert calls == [jev_gates.STATE_TOKEN_BUDGET, int(jev_gates.STATE_TOKEN_BUDGET * jev_gates.RETRY_BUDGET_FRACTION)]
    assert result["state_compaction"]["retried_after"] == "max_tokens_exceeded"


def test_receipt_records_state_compaction(monkeypatch, tmp_path):
    path = tmp_path / "outline.json"
    outline(path)
    monkeypatch.setattr(jev_gates, "_find_api_key", lambda: "")
    result = jev_gates.run_stage_gate("outline", {"outline": path})
    assert result["state_compaction"]["token_budget"] == jev_gates.STATE_TOKEN_BUDGET
    assert result["state_compaction"]["truncated"] == []


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


@pytest.mark.skipif(not shutil.which("node"), reason="native CLI requires node")
def test_advisory_jev_is_reported_without_blocking(monkeypatch, tmp_path):
    source = next((production.SKILLS / "cine-outline/examples").glob("*-outline.json"))
    shutil.copyfile(source, tmp_path / "outline.json")
    monkeypatch.setattr(jev_gates, "_find_api_key", lambda: "")
    result = production.check(tmp_path, "outline", advise_jev=True)
    assert result["status"] == "native_validated"
    assert result["jev_mode"] == "advisory"
    assert result["jev_status"] == "advisory_findings"
    assert result["stages"][0]["status"] == "passed"
    assert result["stages"][0]["jev"]["status"] == "unavailable"


def test_stage_gate_retries_transport_failure(monkeypatch, tmp_path):
    path = tmp_path / "outline.json"
    outline(path)
    attempts = []

    class FlakyClient(Client):
        def system_one(self, **_kwargs):
            if len(attempts) == 1:
                raise ConnectionError("temporary transport failure")
            return self.response

    def factory(_key, **kwargs):
        attempts.append(kwargs)
        return FlakyClient(response())

    monkeypatch.setattr(
        jev_gates,
        "_transport_module",
        lambda: SimpleNamespace(
            open_http_client=lambda: None,
            is_transport_error=lambda exc: isinstance(exc, ConnectionError),
        ),
    )
    result = jev_gates.run_stage_gate(
        "outline", {"outline": path}, api_key="test-key", client_factory=factory
    )
    assert result["status"] == "passed"
    assert len(attempts) == 2


def test_compact_enforces_total_character_budget():
    large = {"groups": [{"items": [{"text": "x" * 1000} for _ in range(30)]} for _ in range(30)]}
    compacted = json.dumps(jev_gates._compact(large), ensure_ascii=False)
    assert len(compacted) <= 200_000
    assert '"_compaction_truncated": true' in compacted
