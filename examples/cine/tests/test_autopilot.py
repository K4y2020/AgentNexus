"""The unattended production controller decides the next step from files alone."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import autopilot, production

STAGES = ("outline", "cast", "art", "script", "storyboard")


@pytest.fixture
def directory(tmp_path):
    root = tmp_path / "production"
    root.mkdir()
    for stage in STAGES:
        (root / f"{stage}.json").write_text(json.dumps({"stage": stage}), encoding="utf-8")
    return root


class Checks:
    """Stand-in for check_stage: every stage passes unless told otherwise."""

    def __init__(self, monkeypatch):
        self.results: dict[str, dict] = {}
        self.calls: list[str] = []
        monkeypatch.setattr(production, "check_stage", self)

    def __call__(self, directory, stage, _source_text=None, **_kwargs):
        self.calls.append(stage)
        path = directory / f"{stage}.json"
        row = {
            "stage": stage,
            "status": "passed",
            "input_hashes": {str(path): production.fingerprint(path)},
        }
        row.update(self.results.get(stage, {}))
        return row


def edit(directory, stage, n):
    (directory / f"{stage}.json").write_text(
        json.dumps({"stage": stage, "edit": n}), encoding="utf-8"
    )


def queue(directory):
    return json.loads((directory / "review-queue.json").read_text(encoding="utf-8"))["items"]


def test_missing_outline_asks_for_intake(tmp_path):
    assert autopilot.next_step(tmp_path)["action"] == "intake"


def test_a_failing_stage_gets_a_bounded_edit_budget(monkeypatch, directory):
    checks = Checks(monkeypatch)
    checks.results["script"] = {"status": "failed", "stderr": "台词顺序被调换了"}

    steps = []
    for n in range(autopilot.MAX_EDITS + 1):
        steps.append(autopilot.next_step(directory))
        edit(directory, "script", n)

    assert [s["action"] for s in steps[:-1]] == ["edit"] * autopilot.MAX_EDITS
    assert [s["mode"] for s in steps[:2]] == ["write", "repair"]
    assert steps[0]["skill"] == "cine-script" and "台词顺序被调换了" in steps[0]["errors"]
    assert steps[-1]["action"] == "escalate" and steps[-1]["reason"] == "repair_exhausted"
    assert [i["severity"] for i in queue(directory)] == ["blocking"]


def test_a_long_error_list_is_shown_from_its_head_and_saved_whole(monkeypatch, directory):
    checks = Checks(monkeypatch)
    lines = [
        f"  E01-{n:02d}.detailed_description.<Subject 4>: 首次使用不在正文里" + "。" * 200
        for n in range(1, 41)
    ]
    stdout = "✗ 40 处违规：\n\n" + "\n".join(lines)
    checks.results["storyboard"] = {"status": "failed", "exit_code": 1, "stdout": stdout}

    step = autopilot.next_step(directory)

    log = directory / ".cine-validation" / "errors" / "storyboard.txt"
    assert step["action"] == "edit" and step["errors"][0].startswith("✗ 40 处违规")
    assert str(log.resolve()) in step["errors"][-1]
    assert sum(len(e) for e in step["errors"][:-1]) <= autopilot.ERROR_EXCERPT
    saved = log.read_text(encoding="utf-8")
    assert all(line.strip() in saved for line in lines)

    checks.results["storyboard"] = {"status": "failed", "exit_code": 1, "stdout": "✗ 1 处违规"}
    edit(directory, "storyboard", 1)
    assert autopilot.next_step(directory)["errors"] == ["✗ 1 处违规"]
    assert not log.exists()


def test_an_unedited_file_stops_after_repeated_calls(monkeypatch, directory):
    checks = Checks(monkeypatch)
    checks.results["cast"] = {"status": "failed"}
    steps = [autopilot.next_step(directory) for _ in range(autopilot.MAX_UNCHANGED + 1)]
    assert steps[-1]["reason"] == "not_progressing"


def test_a_person_editing_the_file_reopens_an_escalated_stage(monkeypatch, directory):
    checks = Checks(monkeypatch)
    checks.results["cast"] = {"status": "failed"}
    for n in range(autopilot.MAX_EDITS + 1):
        result = autopilot.next_step(directory)
        edit(directory, "cast", n)
    # The last edit above happened after escalation, i.e. by a person.
    assert result["action"] == "escalate"
    checks.results.pop("cast")
    assert autopilot.next_step(directory)["action"] == "import_canvas"
    assert queue(directory) == []


def test_passed_stages_are_not_rechecked_until_an_input_changes(monkeypatch, directory):
    checks = Checks(monkeypatch)
    autopilot.next_step(directory)
    assert checks.calls == list(STAGES)
    autopilot.next_step(directory)
    assert checks.calls == list(STAGES)
    edit(directory, "art", 1)
    autopilot.next_step(directory)
    assert checks.calls[len(STAGES) :] == ["art"]


def test_missing_sheets_are_repaired_in_cast(monkeypatch, directory):
    checks = Checks(monkeypatch)
    checks.results["storyboard"] = {
        "status": "assets_missing",
        "asset_anchors": {
            "status": "missing",
            "problems": ["Character C01 (A) has no image.sheet in cast.json."],
        },
    }
    step = autopilot.next_step(directory)
    assert (step["stage"], step["target_stage"], step["skill"]) == (
        "storyboard",
        "cast",
        "cine-characters",
    )
    assert step["file"].endswith("cast.json")


def test_technical_failures_retry_once_then_wait_for_an_explicit_retry(monkeypatch, directory):
    checks = Checks(monkeypatch)
    checks.results["script"] = {
        "status": "attribution_error",
        "stderr": "TYPESAFE_API_KEY missing",
    }
    step = autopilot.next_step(directory)
    assert step["reason"] == "technical_attribution_error"
    assert checks.calls.count("script") == 2
    assert autopilot.next_step(directory)["action"] == "escalate"
    checks.results.pop("script")
    assert autopilot.next_step(directory, retry=True)["action"] == "import_canvas"


def test_unsettled_speakers_are_queued_without_blocking(monkeypatch, directory):
    checks = Checks(monkeypatch)
    checks.results["script"] = {
        # The script was compared with the attributed transcript.
        "command": ["node", "cine-script.mjs", "validate", "--source", "attributed.json"],
    }
    (directory / "source-transcript-attributed.json").write_text(
        json.dumps({"attributions": [{"text": "我来提示了！", "needs_review": True}]}),
        encoding="utf-8",
    )
    step = autopilot.next_step(directory)
    assert step["action"] == "import_canvas"
    reasons = {(i["reason"], i["severity"]) for i in queue(directory)}
    assert reasons == {("speaker_unsettled", "review")}


def test_canvas_sync_is_recorded_per_storyboard_version(monkeypatch, directory, tmp_path):
    Checks(monkeypatch)
    step = autopilot.next_step(directory)
    assert step["arguments"]["action"] == "import_storyboard"
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"status": "imported"}), encoding="utf-8")
    assert autopilot.record_canvas_sync(directory, receipt)["action"] == "done"
    edit(directory, "storyboard", 1)
    assert autopilot.next_step(directory)["action"] == "import_canvas"


def test_canvas_delivery_returns_existing_files_and_receipts_after_restart(
    monkeypatch, directory, tmp_path
):
    checks = Checks(monkeypatch)
    checks.results["storyboard"] = {"stdout": "✓ 分镜全部通过校验\n"}
    assert autopilot.next_step(directory)["action"] == "import_canvas"
    canvas_receipt = {"status": "imported", "generation_submitted": False}
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(canvas_receipt), encoding="utf-8")

    done = autopilot.record_canvas_sync(directory, receipt)
    assert (done["action"], done["delivery"]) == ("done", "canvas")
    assert done["files"] == {stage: str(directory / f"{stage}.json") for stage in STAGES}
    assert all(Path(path).is_file() for path in done["files"].values())
    assert not (directory / "production.json").exists()
    assert done["receipts"]["storyboard"] == "✓ 分镜全部通过校验"
    assert done["canvas_receipt"] == canvas_receipt
    assert autopilot.next_step(directory) == done


def test_done_carries_each_review_item_as_queued(monkeypatch, directory):
    checks = Checks(monkeypatch)
    review = {"reason": "dialogue_location_unknown", "message": "台词地点无法判断"}
    checks.results["script"] = {"locations": {"status": "passed", "review": [review]}}

    done = autopilot.next_step(directory, text_only=True)

    assert done["action"] == "done"
    assert done["review"] == queue(directory)
    assert done["review"][0]["detail"]["message"] == "台词地点无法判断"


def test_cached_runs_retire_old_stage_reviews_but_preserve_other_findings(monkeypatch, directory):
    checks = Checks(monkeypatch)
    autopilot.next_step(directory, text_only=True)
    retired = {"stage": "script", "source": "jev", "severity": "review", "reason": "jev_failed"}
    active = {
        "stage": "storyboard",
        "source": "pov",
        "severity": "review",
        "reason": "pov_unchecked",
        "detail": {"error": "service unavailable"},
    }
    (directory / autopilot.QUEUE).write_text(
        json.dumps({"items": [retired, active]}), encoding="utf-8"
    )

    done = autopilot.next_step(directory)

    assert checks.calls == list(STAGES)
    assert done["review"] == [active]
    saved = json.loads((directory / autopilot.QUEUE).read_text(encoding="utf-8"))
    assert saved["items"] == [active]
    assert saved["retired_items"] == [retired]
    assert autopilot.next_step(directory) == done
    assert json.loads((directory / autopilot.QUEUE).read_text())["retired_items"] == [retired]


def test_unchecked_source_comparison_is_reported_and_cleared_after_recheck(monkeypatch, directory):
    checks = Checks(monkeypatch)
    comparison = {
        "status": "unchecked",
        "reason": "plain_text_requires_attributed_transcript",
        "scope": "source_dialogue",
        "message": "原稿对账未检查，不能声称台词忠实。",
    }
    checks.results["script"] = {"source_comparison": comparison}

    done = autopilot.next_step(directory, text_only=True)

    assert done["review"] == [
        {
            "stage": "script",
            "source": "source_comparison",
            "severity": "review",
            "reason": "source_comparison_unchecked",
            "detail": comparison,
        }
    ]
    checks.results["script"] = {"source_comparison": {"status": "checked"}}
    edit(directory, "script", 1)
    assert autopilot.next_step(directory)["review"] == []


def test_selecting_or_removing_source_text_reopens_cached_checks(monkeypatch, directory):
    checks = Checks(monkeypatch)
    autopilot.next_step(directory)
    first = directory / "first.txt"
    second = directory / "second.txt"
    first.write_text("原稿", encoding="utf-8")
    second.write_text("原稿", encoding="utf-8")

    for selected in (first, second, None):
        before = len(checks.calls)
        autopilot.next_step(directory, source_text=selected)
        assert checks.calls[before:] == ["cast", "script"]


def test_changed_validator_wrapper_reopens_cached_stages(monkeypatch, directory, tmp_path):
    wrapper = tmp_path / "production.py"
    wrapper.write_text("old check implementation", encoding="utf-8")
    monkeypatch.setattr(production, "__file__", str(wrapper))
    checks = Checks(monkeypatch)
    autopilot.next_step(directory)
    wrapper.write_text("new check implementation", encoding="utf-8")

    autopilot.next_step(directory)

    assert checks.calls == list(STAGES) * 2


def test_source_location_reviews_are_queued_and_cleared_on_recheck(monkeypatch, directory):
    checks = Checks(monkeypatch)
    checks.results["outline"] = {
        "locations": {
            "status": "unavailable",
            "review": [
                {
                    "reason": "source_locations_missing",
                    "message": "场景表缺失",
                }
            ],
        }
    }
    checks.results["script"] = {
        "locations": {
            "status": "passed",
            "review": [
                {
                    "reason": "dialogue_location_unknown",
                    "message": "台词地点无法判断",
                }
            ],
        }
    }
    assert autopilot.next_step(directory)["review_items"] == 2
    assert {item["source"] for item in queue(directory)} == {"locations"}
    checks.results.clear()
    edit(directory, "outline", 1)
    edit(directory, "script", 1)
    assert autopilot.next_step(directory)["review_items"] == 0


def test_new_source_locations_reopen_passed_outline_and_script(monkeypatch, directory):
    checks = Checks(monkeypatch)
    autopilot.next_step(directory)
    (directory / "source-material.json").write_text('{"source_locations": {}}', encoding="utf-8")
    autopilot.next_step(directory)
    assert checks.calls[len(STAGES) :] == ["outline", "script"]


def test_legacy_cached_stage_without_location_inputs_is_rechecked(monkeypatch, directory):
    checks = Checks(monkeypatch)
    autopilot.next_step(directory)
    path = directory / autopilot.STATE
    state = json.loads(path.read_text(encoding="utf-8"))
    state["stages"]["outline"]["passed_inputs"].pop(str(directory / "source-material.json"))
    path.write_text(json.dumps(state), encoding="utf-8")
    autopilot.next_step(directory)
    assert checks.calls[len(STAGES) :] == ["outline"]


def test_cards_whose_prompt_ignores_their_references_block_generation(
    monkeypatch, directory, tmp_path
):
    """A card imported with its references untied to the prompt is queued as blocking.

    The prompt called the character sheets shot keyframes and never named the
    scene; generating it as it is would spend credits on the wrong picture.
    """
    Checks(monkeypatch)
    autopilot.next_step(directory)
    receipt = tmp_path / "receipt.json"
    review = [{"segmentId": "E01-01", "issues": ["The prompt defines no <Subject N>"]}]
    receipt.write_text(
        json.dumps({"status": "synced", "receipt": {"subject_alignment_review": review}}),
        encoding="utf-8",
    )
    step = autopilot.record_canvas_sync(directory, receipt)
    assert step["action"] == "done"
    assert step["blocking_items"] == 1
    [item] = queue(directory)
    assert (item["stage"], item["source"], item["severity"]) == (
        "storyboard",
        "canvas",
        "blocking",
    )
    assert item["detail"]["segmentId"] == "E01-01"

    # A clean re-import clears it.
    receipt.write_text(
        json.dumps({"status": "synced", "receipt": {"subject_alignment_review": []}}),
        encoding="utf-8",
    )
    assert autopilot.record_canvas_sync(directory, receipt)["blocking_items"] == 0
    assert queue(directory) == []


def test_seed_creates_a_missing_stage_before_checking(monkeypatch, directory):
    checks = Checks(monkeypatch)
    (directory / "art.json").unlink()
    seeded = []

    def seed(d, stage):
        seeded.append(stage)
        (d / f"{stage}.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(production, "seed", seed)
    checks.results["art"] = {"status": "failed"}
    step = autopilot.next_step(directory)
    assert seeded == ["art"]
    assert (step["action"], step["mode"], step["stage"]) == ("edit", "write", "art")


def test_edit_history_survives_the_stage_passing(monkeypatch, directory):
    checks = Checks(monkeypatch)
    checks.results["cast"] = {"status": "failed"}
    autopilot.next_step(directory)
    edit(directory, "cast", 1)
    autopilot.next_step(directory)
    edit(directory, "cast", 2)
    checks.results.pop("cast")
    autopilot.next_step(directory)
    state = json.loads((directory / autopilot.STATE).read_text(encoding="utf-8"))
    history = state["stages"]["cast"]["history"]
    assert [h["status"] for h in history] == ["failed", "failed", "passed"]
    assert state["stages"]["cast"]["failed_versions"] == []


def test_a_free_adaptation_raises_no_speaker_items(monkeypatch, directory):
    """Without a source comparison, unsettled source speakers are not this script's concern."""
    checks = Checks(monkeypatch)
    checks.results["script"] = {"command": ["node", "cine-script.mjs", "validate"]}
    (directory / "source-transcript-attributed.json").write_text(
        json.dumps({"attributions": [{"text": "x", "needs_review": True}]}), encoding="utf-8"
    )
    autopilot.next_step(directory)
    assert queue(directory) == []


def test_performance_differences_are_queued_and_a_contradiction_is_repaired(
    monkeypatch, directory
):
    checks = Checks(monkeypatch)
    item = {"line": "去把三条船都拴牢了", "message": "表演可能和原片不一致"}
    checks.results["script"] = {"performance": {"status": "passed", "review": [item]}}
    assert autopilot.next_step(directory)["action"] == "import_canvas"
    assert [(i["reason"], i["detail"]) for i in queue(directory)] == [
        ("performance_differs", item)
    ]

    edit(directory, "script", 1)
    blocking = ["S01「娃记住了吧」表演和原片相反"]
    checks.results["script"] = {
        "status": "failed",
        "errors": blocking,
        "performance": {"status": "failed", "blocking": blocking},
    }
    step = autopilot.next_step(directory)
    assert (step["action"], step["stage"], step["errors"]) == ("edit", "script", blocking)


@pytest.mark.parametrize("synced", [False, True])
def test_used_unsettled_speaker_stops_faithful_completion(monkeypatch, directory, synced):
    Checks(monkeypatch)
    (directory / "outline.json").write_text(
        json.dumps({"params": {"adaptMode": "忠实"}}), encoding="utf-8"
    )
    (directory / "script.json").write_text(
        json.dumps(
            {
                "episodes": [
                    {"scenes": [{"sceneId": "S01", "flow": [{"speaker": "C01", "line": "请坐"}]}]}
                ]
            }
        ),
        encoding="utf-8",
    )
    source = {
        "attributions": [
            {
                "id": "a",
                "text": "请坐",
                "start": 1,
                "end": 2,
                "speaker": "unclear",
                "speaker_needs_review": True,
                "evidence": {
                    "a": {"speaker": "unclear"},
                    "b": {},
                    "present_people": [],
                    "constraints": {"status": "review"},
                },
                "review_reasons": ["画面不明确"],
            }
        ]
    }
    (directory / "source-transcript-attributed.json").write_text(
        json.dumps(source), encoding="utf-8"
    )
    if synced:
        run = autopilot._Run(directory)
        run.state["canvas_synced"] = {"sha": production.digest(directory / "storyboard.json")}
        run.save()
    result = autopilot.next_step(directory)
    assert result["action"] == "human_review_speakers"
    assert result["lines"][0]["evidence"] == source["attributions"][0]["evidence"]
    assert autopilot.next_step(directory)["action"] == "human_review_speakers"
    source["attributions"][0].update(speaker="C01", speaker_needs_review=False)
    (directory / "source-transcript-attributed.json").write_text(
        json.dumps(source), encoding="utf-8"
    )
    assert autopilot.next_step(directory)["action"] == ("done" if synced else "import_canvas")
    assert not [i for i in queue(directory) if i["source"] == "speakers"]


def test_unused_unsettled_speaker_does_not_stop_completion(monkeypatch, directory):
    Checks(monkeypatch)
    (directory / "outline.json").write_text(
        json.dumps({"params": {"adaptMode": "忠实"}}), encoding="utf-8"
    )
    (directory / "source-transcript-attributed.json").write_text(
        json.dumps(
            {
                "attributions": [
                    {"text": "请坐", "speaker": "unclear", "speaker_needs_review": True}
                ]
            }
        ),
        encoding="utf-8",
    )
    assert autopilot.next_step(directory)["action"] == "import_canvas"


@pytest.mark.parametrize("synced", [False, True])
def test_used_confirmed_extra_does_not_stop_faithful_completion(monkeypatch, directory, synced):
    Checks(monkeypatch)
    (directory / "outline.json").write_text(
        json.dumps({"params": {"adaptMode": "忠实"}}), encoding="utf-8"
    )
    (directory / "script.json").write_text(
        json.dumps(
            {
                "episodes": [
                    {
                        "scenes": [
                            {
                                "sceneId": "S01",
                                "characters": [],
                                "flow": [{"speaker": "extra", "line": "请坐"}],
                            }
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (directory / "source-transcript-attributed.json").write_text(
        json.dumps(
            {
                "attributions": [
                    {
                        "id": "a",
                        "text": "请坐",
                        "start": 1,
                        "end": 2,
                        "speaker": "extra",
                        "speaker_needs_review": False,
                        "needs_review": False,
                        "delivery": "spoken",
                        "delivery_needs_review": False,
                        "evidence": {
                            "a": {"speaker": "P1", "status": "extra"},
                            "b": {},
                            "present_people": ["P1"],
                            "constraints": {"status": "passed"},
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    if synced:
        run = autopilot._Run(directory)
        run.state["canvas_synced"] = {"sha": production.digest(directory / "storyboard.json")}
        run.save()
    assert autopilot.next_step(directory)["action"] == ("done" if synced else "import_canvas")
    assert not [i for i in queue(directory) if i["source"] == "speakers"]


def test_confirmed_extra_cannot_hide_an_unsettled_response_in_a_merged_beat(
    monkeypatch, directory
):
    Checks(monkeypatch)
    (directory / "outline.json").write_text(
        json.dumps({"params": {"adaptMode": "忠实"}}), encoding="utf-8"
    )
    (directory / "script.json").write_text(
        json.dumps(
            {
                "episodes": [
                    {
                        "scenes": [
                            {
                                "sceneId": "S01",
                                "flow": [{"speaker": "extra", "line": "请稍等一下，马上就来"}],
                            }
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (directory / "source-transcript-attributed.json").write_text(
        json.dumps(
            {
                "attributions": [
                    {
                        "id": "a",
                        "text": "请稍等一下",
                        "speaker": "extra",
                        "speaker_needs_review": False,
                    },
                    {
                        "id": "b",
                        "text": "马上就来",
                        "speaker": "unclear",
                        "speaker_needs_review": True,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    run = autopilot._Run(directory)
    run.state["canvas_synced"] = {"sha": production.digest(directory / "storyboard.json")}
    run.save()
    result = autopilot.next_step(directory)
    assert result["action"] == "human_review_speakers"
    assert [r["source_id"] for r in result["lines"]] == ["b"]


def test_legacy_passed_script_receipt_reopens_for_people_evidence(monkeypatch, directory):
    checks = Checks(monkeypatch)
    autopilot.next_step(directory)
    run = autopilot._Run(directory)
    run.stage("script")["passed_inputs"] = {
        str(directory / "script.json"): production.fingerprint(directory / "script.json")
    }
    run.save()
    checks.calls.clear()
    autopilot.next_step(directory)
    assert checks.calls == ["script"]


def test_native_source_review_findings_reach_the_review_queue(monkeypatch, directory):
    checks = Checks(monkeypatch)
    checks.results["script"] = {
        "source_review": {"bad": {"review": ["S01 说话人不符，原片 1.0–2.0s"]}}
    }
    assert autopilot.next_step(directory)["action"] == "import_canvas"
    assert any(i["reason"] == "source_speaker_review" for i in queue(directory))


def test_changed_vision_model_reopens_script_without_rechecking_other_stages(
    monkeypatch, directory
):
    current = ["vision-a"]
    monkeypatch.setattr(production, "vision_model", lambda: current[0])
    checks = Checks(monkeypatch)
    autopilot.next_step(directory)
    checks.calls.clear()
    autopilot.next_step(directory)
    assert checks.calls == []
    current[0] = "vision-b"
    autopilot.next_step(directory)
    assert checks.calls == ["script"]


def test_a_text_only_run_ends_with_the_files_and_validator_receipts(monkeypatch, directory):
    checks = Checks(monkeypatch)
    checks.results["storyboard"] = {"stdout": "warming up\n✓ 1 集 / 5 段全部通过校验\n"}
    step = autopilot.next_step(directory)
    assert step["action"] == "import_canvas" and "--text-only" in step["text_only"]

    step = autopilot.next_step(directory, text_only=True)
    assert (step["action"], step["delivery"]) == ("done", "text")
    assert step["receipts"]["storyboard"] == "✓ 1 集 / 5 段全部通过校验"
    assert step["files"]["script"].endswith("script.json")
    assert "finalize" in step["detail"]
    # The choice is remembered, so a later plain call still ends there.
    assert autopilot.next_step(directory)["delivery"] == "text"


def test_upstream_edits_during_a_storyboard_repair_are_undone(monkeypatch, directory):
    checks = Checks(monkeypatch)
    checks.results["storyboard"] = {"status": "failed", "stderr": "一个镜头两个人说话"}
    step = autopilot.next_step(directory)
    assert step["action"] == "edit" and step["target_stage"] == "storyboard"
    assert {"outline.json", "cast.json", "art.json", "script.json"} <= set(step["frozen"])
    original = (directory / "script.json").read_bytes()

    edit(directory, "script", 1)  # the repair rewrites dialogue upstream
    (directory / "person-map.json").write_text("{}", encoding="utf-8")
    edit(directory, "storyboard", 1)
    step = autopilot.next_step(directory)

    assert (directory / "script.json").read_bytes() == original
    assert not (directory / "person-map.json").exists()
    assert step["action"] == "edit" and "script.json" in step["errors"][0]
    reverted = {item["file"]: item["edited_copy"] for item in step["reverted_upstream"]}
    kept = json.loads((directory / reverted["script.json"]).read_text(encoding="utf-8"))
    assert kept == {"stage": "script", "edit": 1}
    # The restored script still matches its pass, so it is not checked again.
    assert checks.calls.count("script") == 1
    items = [i for i in queue(directory) if i["reason"] == "upstream_edit_reverted"]
    assert {i["detail"]["file"] for i in items} == {"script.json", "person-map.json"}
    assert {i["severity"] for i in items} == {"review"}


def test_a_repair_aimed_at_an_upstream_stage_may_change_it(monkeypatch, directory):
    checks = Checks(monkeypatch)
    checks.results["storyboard"] = {
        "status": "assets_missing",
        "asset_anchors": {"status": "missing", "problems": ["Character C01 has no sheet."]},
    }
    # Later stages list the cast among their inputs; it stays editable all the same.
    inputs = {str(directory / f"{s}.json"): None for s in ("cast", "art")}
    checks.results["art"] = {"input_hashes": inputs}
    step = autopilot.next_step(directory)
    assert step["target_stage"] == "cast" and "cast.json" not in step["frozen"]
    assert "art.json" in step["frozen"]
    edit(directory, "cast", 1)
    step = autopilot.next_step(directory)
    assert "reverted_upstream" not in step
    assert json.loads((directory / "cast.json").read_text(encoding="utf-8"))["edit"] == 1


def test_a_person_may_change_upstream_stages_after_an_escalation(monkeypatch, directory):
    checks = Checks(monkeypatch)
    checks.results["storyboard"] = {"status": "failed"}
    for n in range(autopilot.MAX_EDITS + 1):
        result = autopilot.next_step(directory)
        edit(directory, "storyboard", n)
    assert result["action"] == "escalate"
    edit(directory, "script", 9)
    step = autopilot.next_step(directory)
    assert "reverted_upstream" not in step
    assert json.loads((directory / "script.json").read_text(encoding="utf-8"))["edit"] == 9


def test_an_unfinished_film_analysis_sends_the_run_back_to_intake(monkeypatch, directory):
    Checks(monkeypatch)
    story = {
        "source_id": "src",
        "revision_id": "r1",
        "characters": [],
        "summary": {"premise": "", "conflict": "", "turning_points": [], "ending": ""},
        "sections": [{"batch_id": "B001", "events": []}],
    }
    material = {
        "source_id": "src",
        "revision_id": "r1",
        "shots": [],
        "source_locations": {"status": "partial", "unread_shots": [3, 4]},
        "adaptation_story": story,
        "adaptation_story_batches": [{"batch_id": "B001"}],
    }
    path = directory / "source-material.json"
    path.write_text(json.dumps(material), encoding="utf-8")
    step = autopilot.next_step(directory)
    assert step["action"] == "intake"
    assert "shots_unread:2" in step["gaps"] and "summary_premise_missing" in step["gaps"]
    assert "story draft" in step["detail"]

    story.update(
        forms=[],
        characters=["船夫"],
        summary={"premise": "渡口", "conflict": "皮箱", "turning_points": [], "ending": "开船"},
        sections=[{"batch_id": "B001", "events": ["开船"], "connection": "完"}],
    )
    material["source_locations"] = {"status": "read", "unread_shots": []}
    path.write_text(json.dumps(material), encoding="utf-8")
    assert autopilot.next_step(directory)["action"] == "import_canvas"
