"""Authenticated quality decisions trigger only bounded, traceable local repairs."""

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

from agentnexus.seedance.media_quality_receipts import sign_review

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import autopilot, generation, production, quality_loop


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def media(path, text):
    path.write_bytes(text.encode())
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "media_kind": "video",
    }


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNEXUS_DATA_DIR", str(tmp_path / "runner-state"))
    directory = tmp_path / "topic" / "production"
    directory.mkdir(parents=True)
    for stage in ("outline", "cast", "art", "script"):
        write(directory / f"{stage}.json", {"stage": stage, "version": 1})
    write(
        directory / "storyboard.json",
        {
            "episodes": [
                {
                    "segments": [
                        {"id": "S01", "h3Prompt": "approved first scene"},
                        {"id": "S02", "h3Prompt": "approved continuation", "continuesFrom": "S01"},
                        {"id": "S03", "h3Prompt": "unrelated scene"},
                    ]
                }
            ]
        },
    )
    plan = {
        "schema_version": 1,
        "project_id": "project",
        "authorization": {
            "allowed": True,
            "scope": "The approved film, bounded retries included",
            "models": ["video-model"],
            "max_submissions": 9,
            "max_attempts_per_item": 3,
        },
        "quality": {
            "requirements": [{"id": "story", "text": "Preserve cause and effect"}],
            "max_review_calls": 5,
            "max_rework_rounds": 2,
            "max_design_edits": 2,
        },
        "items": [
            {
                "id": identity,
                "node_id": "node-" + identity,
                "kind": "video",
                "model": "video-model",
                "production_stage": "storyboard",
                "production_pointer": f"/episodes/0/segments/{index}/h3Prompt",
                "depends_on": ["first"] if identity == "second" else [],
            }
            for index, identity in enumerate(("first", "second", "unrelated"))
        ],
        "assembly": ["first", "second", "unrelated"],
    }
    plan_path = write(directory / "generation-plan.json", plan)
    state = {
        "schema_version": 1,
        "plan": plan,
        "plan_path": str(plan_path),
        "plan_sha256": quality_loop.digest(plan),
        "run_id": "run-1",
        "inputs": generation._inputs(directory, None),
        "source_text": None,
        "items": {},
        "submissions": 3,
        "sequence": 0,
        "receipts": {},
        "active": None,
        "io_failures": 0,
        "quality": {"rounds": 0},
    }
    for item in plan["items"]:
        identity = item["id"]
        exported = media(directory.parent / f"{identity}.mp4", "rendered " + identity)
        exported.update(status="exported", job_id="job-" + identity)
        state["items"][identity] = {
            "status": "exported",
            "attempts": 1,
            "job_id": "job-" + identity,
            "request_id": "request-" + identity,
            "export": exported,
            "job": {
                "id": "job-" + identity,
                "projectId": "project",
                "nodeId": item["node_id"],
                "kind": "video",
                "status": "succeeded",
                "outputRefs": ["local://" + exported["sha256"]],
            },
            "history": [{"operation": "submit", "job_id": "job-" + identity}],
            "continuation_verified": "job-first" if identity == "second" else None,
        }
    assembly = media(directory.parent / "assembly.mp4", "first second unrelated")
    assembly["clips"] = [
        {"job_id": state["items"][i]["job_id"], "sha256": state["items"][i]["export"]["sha256"]}
        for i in plan["assembly"]
    ]
    state["assembly"] = assembly
    generation.validate_plan(directory, plan)
    return directory, state


def configure(run, *, authorization=None, quality=None):
    _directory, state = run
    if authorization:
        state["plan"]["authorization"].update(authorization)
    if quality:
        state["plan"]["quality"].update(quality)
    write(Path(state["plan_path"]), state["plan"])
    state["plan_sha256"] = quality_loop.digest(state["plan"])


def review_result(
    run, *, decision="pass", regenerate=(), stage_edits=(), uncertain=(), limitations=()
):
    directory, state = run
    review = {
        "status": "reviewed",
        "plan_sha256": state["plan_sha256"],
        "decision": decision,
        "limitations": list(limitations),
        "report": {
            "issues": [
                {
                    "id": "issue-one",
                    "item_id": "first",
                    "classification": "source_design" if stage_edits else "generation_execution",
                    "repair": {"instruction": "Fix only the observed defect"},
                }
            ]
        },
        "repair_plan": {
            "regenerate_item_ids": list(regenerate),
            "stage_edits": list(stage_edits),
            "review_item_ids": list(uncertain),
        },
    }
    envelope = sign_review(
        {
            "schema_version": 1,
            "session_id": "topic",
            "snapshot_sha256": quality_loop.digest(quality_loop.snapshot(directory, state)),
            "review": review,
        }
    )
    return {"status": "reviewed", "envelope": envelope, "receipt_path": "quality-receipt.json"}


def receive(run, **options):
    result = review_result(run, **options)
    quality_loop.receive(*run, result)
    return result


def design(run, stage="storyboard"):
    receive(run, decision="revise", stage_edits=[{"item_id": "first", "stage": stage}])
    return quality_loop.advance(*run)


def test_unsigned_model_written_pass_is_never_a_quality_decision(run):
    result = review_result(run)
    result["envelope"].pop("signature")
    before = copy.deepcopy(run[1])
    with pytest.raises(ValueError):
        quality_loop.receive(*run, result)
    assert run[1] == before


def test_controller_routes_real_review_tool_then_accepts_only_signed_receipt(run):
    directory, state = run
    write(directory / generation.STATE, state)
    action = generation.next_step(directory)
    assert action["tool"] == "cine_review_media"
    assert action["operation"] == "quality"
    assert action["arguments"] == {"production_dir": str(directory)}
    receipt = write(
        directory / "review-receipt.json",
        {
            "action_id": action["action_id"],
            "result": review_result(run),
        },
    )
    done = generation.next_step(directory, receipt_path=receipt)
    assert done["action"] == "done" and done["acceptance"] == "model_reviewed"
    assert generation.next_step(directory)["acceptance"] == "model_reviewed"


def test_controller_routes_failed_quality_to_local_generation_not_full_reset(run):
    directory, state = run
    write(directory / generation.STATE, state)
    action = generation.next_step(directory)
    receipt = write(
        directory / "review-receipt.json",
        {
            "action_id": action["action_id"],
            "result": review_result(run, decision="revise", regenerate=["first"]),
        },
    )
    redo = generation.next_step(directory, receipt_path=receipt)
    assert redo["operation"] == "preflight"
    assert redo["item_id"] == "first"
    restored = production.read(directory / generation.STATE)
    assert restored["submissions"] == 3
    assert restored["items"]["unrelated"]["export"] == state["items"]["unrelated"]["export"]
    assert restored["items"]["first"]["attempts"] == 1
    assert restored["items"]["second"]["status"] == "pending"


def test_controller_review_unavailable_never_spends_a_new_generation_attempt(run):
    directory, state = run
    write(directory / generation.STATE, state)
    action = generation.next_step(directory)
    receipt = write(
        directory / "review-receipt.json",
        {
            "action_id": action["action_id"],
            "result": {"status": "unavailable", "error_code": "MODEL_OFFLINE"},
        },
    )
    stopped = generation.next_step(directory, receipt_path=receipt)
    assert stopped["reason"] == "quality_review_unavailable"
    assert stopped["submissions_reserved"] == 3
    resumed = generation.next_step(directory, retry=True)
    assert resumed["action_id"] == action["action_id"]
    assert resumed["tool"] == "cine_review_media"


def test_tampering_with_a_signed_disposition_is_rejected(run):
    result = review_result(run, decision="review")
    result["envelope"]["payload"]["review"]["decision"] = "pass"
    with pytest.raises(ValueError):
        quality_loop.receive(*run, result)


@pytest.mark.parametrize(
    "changed", ["job", "attempt", "media", "requirements", "upstream", "assembly"]
)
def test_signed_receipt_cannot_be_replayed_after_its_snapshot_changes(run, changed):
    directory, state = run
    result = review_result(run)
    if changed == "job":
        state["items"]["first"]["job_id"] = "replacement-job"
    elif changed == "attempt":
        state["items"]["first"]["attempts"] += 1
    elif changed == "media":
        exported = state["items"]["first"]["export"]
        exported.update(media(Path(exported["path"]), "replacement media"))
    elif changed == "requirements":
        configure(
            run,
            quality={
                "requirements": [{"id": "story", "text": "Different authorized requirement"}]
            },
        )
    elif changed == "upstream":
        write(directory / "script.json", {"version": 2})
        state["inputs"] = generation._inputs(directory, None)
    else:
        state["assembly"].update(media(Path(state["assembly"]["path"]), "replacement assembly"))
    with pytest.raises(ValueError, match="older or different"):
        quality_loop.receive(directory, state, result)


def test_current_signed_pass_is_returned_as_model_reviewed_delivery(run):
    receive(run)
    result = quality_loop.advance(*run)
    assert result["action"] == "done" and result["acceptance"] == "model_reviewed"
    assert set(result["exports"]) == {"first", "second", "unrelated"}
    assert result["quality_receipt"] == "quality-receipt.json"
    assert result["assembly"] == run[1]["assembly"]
    assert run[1]["submissions"] == 3


def test_local_regeneration_preserves_other_outputs_candidates_and_reserved_counts(run):
    directory, state = run
    before = copy.deepcopy(state)
    receive(run, decision="revise", regenerate=["first"])
    assert quality_loop.advance(directory, state) is None
    assert state["items"]["unrelated"] == before["items"]["unrelated"]
    for identity in ("first", "second"):
        assert state["items"][identity]["status"] == "pending"
        assert state["items"][identity]["attempts"] == 1
        assert "continuation_verified" not in state["items"][identity]
    assert state["submissions"] == 3 and state["quality"]["rounds"] == 1
    assert state["quality"]["versions"][0]["items"] == {
        i: before["items"][i] for i in ("first", "second")
    }
    assert state["quality"]["versions"][0]["assembly"] == before["assembly"]
    assert "assembly" not in state and "envelope" not in state["quality"]
    assert all(Path(row["export"]["path"]).is_file() for row in before["items"].values())
    preflight = generation._advance(directory, state, 1000.0, False)
    assert preflight["operation"] == "preflight" and preflight["item_id"] == "first"
    generation._record(
        directory,
        state,
        {"action_id": preflight["action_id"], "result": {"status": "validated"}},
        1000.0,
    )
    submitted = generation._advance(directory, state, 1000.0, False)
    assert submitted["operation"] == "submit"
    assert state["submissions"] == 4 and state["items"]["first"]["attempts"] == 2
    assert (
        submitted["arguments"]["generation_request_id"] != before["items"]["first"]["request_id"]
    )


@pytest.mark.parametrize("budget", ["global", "per_item", "rounds"])
def test_exhausted_allowance_keeps_completed_outputs_deliverable(run, budget):
    directory, state = run
    if budget == "global":
        configure(run, authorization={"max_submissions": 4})
    elif budget == "per_item":
        configure(run, authorization={"max_attempts_per_item": 1})
    else:
        configure(run, quality={"max_rework_rounds": 0})
    before_items, before_assembly = copy.deepcopy(state["items"]), copy.deepcopy(state["assembly"])
    receive(run, decision="revise", regenerate=["first"])
    step = quality_loop.advance(directory, state)
    assert step["action"] == "escalate"
    assert step["reason"] in {"quality_generation_allowance_exhausted", "quality_rework_exhausted"}
    assert state["items"] == before_items and state["assembly"] == before_assembly
    assert state["submissions"] == 3 and state["quality"]["rounds"] == 0
    assert all(Path(row["export"]["path"]).is_file() for row in state["items"].values())


@pytest.mark.parametrize(
    "options",
    [
        {"decision": "review"},
        {"decision": "revise", "regenerate": ["first"], "uncertain": ["first"]},
        {
            "decision": "revise",
            "regenerate": ["first"],
            "limitations": ["Audio was not fully heard"],
        },
    ],
)
def test_uncertain_or_incomplete_evidence_does_not_trigger_blind_regeneration(run, options):
    before = copy.deepcopy(run[1]["items"])
    receive(run, **options)
    step = quality_loop.advance(*run)
    assert step["reason"] == "quality_requires_review"
    assert run[1]["items"] == before and run[1]["submissions"] == 3


def test_source_design_asks_to_edit_native_stage_before_any_regeneration(run):
    directory, state = run
    before = copy.deepcopy(state["items"])
    step = design(run, stage="script")
    assert step["action"] == "edit" and step["stage"] == "script"
    assert step["quality_repair"] is True
    assert Path(step["file"]) == directory / "script.json"
    assert state["items"] == before and state["submissions"] == 3


def test_certain_design_defects_can_be_fixed_while_overall_rhythm_needs_reinspection(run):
    receive(
        run,
        decision="revise",
        stage_edits=[{"item_id": "first", "stage": "storyboard"}],
        limitations=["some_checks_or_issues_are_uncertain"],
    )
    step = quality_loop.advance(*run)
    assert step["action"] == "edit" and step["quality_repair"]
    assert run[1]["submissions"] == 3
    assert step.get("acceptance") != "model_reviewed"


def test_unrequested_file_change_during_design_repair_is_rejected(run, monkeypatch):
    directory, state = run
    design(run)
    before = copy.deepcopy(state["items"])
    write(directory / "cast.json", {"unrequested": True})
    calls = []
    monkeypatch.setattr(autopilot, "_decide", lambda *_a, **_k: calls.append(True))
    result = quality_loop.design_step(directory, state)
    assert result["reason"] == "quality_unrequested_edit"
    assert str(directory / "cast.json") in result["detail"]
    assert state["items"] == before and not calls


def test_native_revalidation_and_canvas_import_complete_before_local_reset(run, monkeypatch):
    directory, state = run
    design(run)
    before = copy.deepcopy(state["items"])
    board = production.read(directory / "storyboard.json")
    board["episodes"][0]["segments"][0]["h3Prompt"] = "corrected first scene"
    write(directory / "storyboard.json", board)
    decisions = iter(
        [
            {"action": "import_canvas", "arguments": {"action": "import_storyboard"}},
            {"action": "done", "delivery": "canvas", "blocking_items": 0},
        ]
    )
    monkeypatch.setattr(autopilot, "_decide", lambda *_a, **_k: next(decisions))
    assert quality_loop.design_step(directory, state)["action"] == "import_canvas"
    assert state["items"] == before
    assert quality_loop.design_step(directory, state) is None
    assert state["inputs"] == generation._inputs(directory, None)
    assert state["items"]["first"]["status"] == "pending"
    assert state["items"]["second"]["status"] == "pending"
    assert state["items"]["unrelated"] == before["unrelated"]
    assert "pending_design" not in state["quality"]


def test_same_prompt_upstream_repair_refreshes_pins_and_invalidates_all_derived_media(
    run, monkeypatch
):
    directory, state = run
    board_before = (directory / "storyboard.json").read_bytes()
    before_inputs = dict(state["inputs"])
    design(run, stage="script")
    write(directory / "script.json", {"stage": "script", "version": 2, "causality": "repaired"})
    monkeypatch.setattr(
        autopilot,
        "_decide",
        lambda *_a, **_k: {"action": "done", "delivery": "canvas", "blocking_items": 0},
    )
    assert quality_loop.design_step(directory, state) is None
    assert (directory / "storyboard.json").read_bytes() == board_before
    assert state["inputs"] == generation._inputs(directory, None) != before_inputs
    assert all(row["status"] == "pending" for row in state["items"].values())
    assert state["submissions"] == 3


def test_design_repair_edit_limit_is_not_reset_by_native_validation(run, monkeypatch):
    directory, state = run
    configure(run, quality={"max_design_edits": 1})
    design(run, stage="script")
    write(directory / "script.json", {"stage": "script", "version": 2})
    monkeypatch.setattr(
        autopilot,
        "_decide",
        lambda *_a, **_k: {
            "action": "edit",
            "target_stage": "script",
            "file": str(directory / "script.json"),
            "errors": ["Still missing an essential action"],
        },
    )
    step = quality_loop.design_step(directory, state)
    assert step["reason"] == "quality_design_edits_exhausted"
    assert state["quality"]["pending_design"]["edits"] == 1
    assert state["submissions"] == 3


def test_design_repairs_without_edit_authorization_do_not_consume_a_round(run):
    configure(run, quality={"max_design_edits": 0})
    step = design(run, stage="script")
    assert step["reason"] == "quality_design_edits_not_authorized"
    assert run[1]["quality"]["rounds"] == 0
    assert "pending_design" not in run[1]["quality"]


def test_repeated_calls_without_the_requested_design_edit_stop(run):
    directory, state = run
    step = design(run)
    assert step["action"] == "edit"
    for _ in range(10):
        step = quality_loop.design_step(directory, state)
        if step["action"] == "escalate":
            break
    assert step["action"] == "escalate"
    assert state["submissions"] == 3


@pytest.mark.parametrize("stage", ["cast", "script"])
def test_unrequested_edit_while_native_validation_runs_is_not_silently_repinned(
    run, monkeypatch, stage
):
    directory, state = run
    design(run)
    board = production.read(directory / "storyboard.json")
    board["episodes"][0]["segments"][0]["h3Prompt"] = "corrected first scene"
    write(directory / "storyboard.json", board)
    before_inputs = dict(state["inputs"])
    before_items = copy.deepcopy(state["items"])

    def validate_while_another_actor_edits(*_args, **_kwargs):
        write(directory / f"{stage}.json", {"unexpected-concurrent-write": True})
        return {"action": "done", "delivery": "canvas", "blocking_items": 0}

    monkeypatch.setattr(autopilot, "_decide", validate_while_another_actor_edits)
    result = quality_loop.design_step(directory, state)
    assert result is not None and result["action"] == "escalate"
    assert result["reason"] in {"quality_unrequested_edit", "quality_inputs_changed"}
    assert state["inputs"] == before_inputs and state["items"] == before_items


def test_text_only_native_completion_cannot_skip_required_canvas_reimport(run, monkeypatch):
    directory, state = run
    design(run)
    board = production.read(directory / "storyboard.json")
    board["episodes"][0]["segments"][0]["h3Prompt"] = "corrected first scene"
    write(directory / "storyboard.json", board)
    before = copy.deepcopy(state["items"])
    monkeypatch.setattr(
        autopilot,
        "_decide",
        lambda *_a, **_k: {
            "action": "done",
            "delivery": "text",
            "blocking_items": 0,
        },
    )
    result = quality_loop.design_step(directory, state)
    assert result is not None and result["action"] in {"import_canvas", "escalate"}
    assert state["items"] == before and "pending_design" in state["quality"]


def test_native_repair_expanding_to_all_items_rechecks_the_full_allowance(run, monkeypatch):
    directory, state = run
    configure(run, authorization={"max_submissions": 5})
    design(run, stage="script")
    before_items, before_inputs = copy.deepcopy(state["items"]), dict(state["inputs"])
    write(directory / "script.json", {"stage": "script", "version": 2})
    monkeypatch.setattr(
        autopilot,
        "_decide",
        lambda *_a, **_k: {
            "action": "done",
            "delivery": "canvas",
            "blocking_items": 0,
        },
    )
    result = quality_loop.design_step(directory, state)
    assert result["reason"] == "quality_generation_allowance_exhausted"
    assert set(result["detail"]["affected_items"]) == {"first", "second", "unrelated"}
    assert state["items"] == before_items and state["inputs"] == before_inputs
    assert state["submissions"] == 3


def test_returned_native_edit_can_target_next_stage_without_unlocking_other_files(
    run, monkeypatch
):
    directory, state = run
    design(run, stage="script")
    write(directory / "script.json", {"stage": "script", "version": 2})
    monkeypatch.setattr(
        autopilot,
        "_decide",
        lambda *_a, **_k: {
            "action": "edit",
            "target_stage": "storyboard",
            "file": str(directory / "storyboard.json"),
            "errors": ["The changed script needs a corresponding shot"],
        },
    )
    step = quality_loop.design_step(directory, state)
    assert step["action"] == "edit" and step["quality_repair"] is True
    write(directory / "script.json", {"stage": "script", "version": 3})
    result = quality_loop.design_step(directory, state)
    assert result["reason"] == "quality_unrequested_edit"
    assert str(directory / "script.json") in result["detail"]


def test_revision_pass_receipt_is_rejected_against_replacement_jobs_until_reinspection(run):
    directory, state = run
    old_review = review_result(run)
    receive(run, decision="revise", regenerate=["first"])
    assert quality_loop.advance(directory, state) is None
    previous = state["quality"]["versions"][0]
    for identity in ("first", "second"):
        row = copy.deepcopy(previous["items"][identity])
        row.update(attempts=2, job_id="replacement-" + identity, request_id="retry-" + identity)
        row["export"].update(
            media(directory.parent / f"{identity}-replacement.mp4", "new " + identity)
        )
        state["items"][identity] = row
    with pytest.raises(ValueError, match="older or different"):
        quality_loop.receive(directory, state, old_review)
    step = quality_loop.advance(directory, state)
    assert step["operation"] == "quality" and step["tool"] == "cine_review_media"
    receive(run)
    final = quality_loop.advance(directory, state)
    assert final["action"] == "done" and final["acceptance"] == "model_reviewed"
    assert state["quality"]["versions"][0] == previous


def test_dependency_invalidation_reaches_transitive_continuations(run):
    directory, state = run
    state["plan"]["items"][2]["depends_on"] = ["second"]
    configure(run)
    receive(run, decision="revise", regenerate=["first"])
    assert quality_loop.advance(directory, state) is None
    assert all(row["status"] == "pending" for row in state["items"].values())
    assert set(state["quality"]["versions"][0]["items"]) == {"first", "second", "unrelated"}
    assert state["submissions"] == 3


def test_more_edits_after_canvas_import_was_requested_are_not_allowed(run, monkeypatch):
    directory, state = run
    design(run)
    board = production.read(directory / "storyboard.json")
    board["episodes"][0]["segments"][0]["h3Prompt"] = "first approved repair"
    write(directory / "storyboard.json", board)
    monkeypatch.setattr(autopilot, "_decide", lambda *_a, **_k: {"action": "import_canvas"})
    assert quality_loop.design_step(directory, state)["action"] == "import_canvas"
    before = copy.deepcopy(state["items"])
    board["episodes"][0]["segments"][0]["h3Prompt"] = "new unrequested repair"
    write(directory / "storyboard.json", board)
    result = quality_loop.design_step(directory, state)
    assert result["reason"] == "quality_unrequested_edit"
    assert state["items"] == before
