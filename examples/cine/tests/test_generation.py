"""Media progress survives interruptions without silently creating another paid job."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import autopilot, generation, production


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.fixture
def run(tmp_path, monkeypatch):
    directory = tmp_path / "topic/production"
    directory.mkdir(parents=True)
    for stage in ("outline", "cast", "art", "script"):
        write(directory / f"{stage}.json", {"stage": stage, "prompt": "reference"})
    write(
        directory / "storyboard.json",
        {
            "episodes": [
                {
                    "segments": [
                        {"id": "E01-01", "h3Prompt": "first scene"},
                        {"id": "E01-02", "h3Prompt": "second scene", "continuesFrom": "E01-01"},
                    ]
                }
            ]
        },
    )
    plan = {
        "schema_version": 1,
        "project_id": "project-one",
        "authorization": {
            "allowed": True,
            "scope": "Generate the selected scene, retries included",
            "models": ["test-video-model"],
            "max_submissions": 3,
            "max_attempts_per_item": 2,
        },
        "items": [
            {
                "id": "first",
                "kind": "video",
                "node_id": "node-first",
                "model": "test-video-model",
                "production_stage": "storyboard",
                "production_pointer": "/episodes/0/segments/0/h3Prompt",
            }
        ],
    }
    path = write(directory / "generation-plan.json", plan)
    clock = [1000.0]
    monkeypatch.setattr(generation.time, "time", lambda: clock[0])
    return directory, path, plan, clock


def start(run):
    directory, path, _, _ = run
    return generation.next_step(directory, plan_path=path)


def receive(run, action, result):
    directory, _, _, _ = run
    receipt = write(
        directory / "receipt.json", {"action_id": action["action_id"], "result": result}
    )
    return generation.next_step(directory, receipt_path=receipt)


def submit(run):
    preflight = start(run)
    assert preflight["operation"] == "preflight"
    return receive(run, preflight, {"status": "validated", "submitted": False})


def job(item="first", status="queued", output=None, identity=None):
    return {
        "id": identity or "job-" + item,
        "projectId": "project-one",
        "nodeId": "node-" + item,
        "kind": "video",
        "status": status,
        "outputRefs": ["local://" + output] if output else [],
    }


def submitted(action, value):
    return {
        "status": "submitted",
        "generation_request_id": action["arguments"]["generation_request_id"],
        "job": value,
        "poll_after_seconds": 20,
    }


def media(run, name="clip.mp4"):
    path = run[0].parent / name
    path.write_bytes(b"synthetic exported media " + name.encode())
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def finish_item(run, action, item="first", name="clip.mp4"):
    path, digest = media(run, name)
    export = receive(run, action, submitted(action, job(item, "succeeded", digest)))
    assert export["operation"] == "export"
    result = {
        "status": "exported",
        "job_id": "job-" + item,
        "media_kind": "video",
        "path": str(path),
        "sha256": digest,
    }
    return receive(run, export, result)


@pytest.mark.parametrize(
    "change",
    [
        {"allowed": False},
        {"max_submissions": 0},
        {"max_attempts_per_item": True},
        {"models": []},
    ],
)
def test_authorization_and_limits_required_before_any_action(run, change):
    directory, path, plan, _ = run
    plan["authorization"].update(change)
    write(path, plan)
    with pytest.raises(ValueError):
        start(run)
    assert not (directory / generation.STATE).exists()


def test_restart_keeps_the_same_submission_and_reserves_allowance_once(run):
    action = submit(run)
    assert action["operation"] == "submit"
    assert action["submissions_reserved"] == 1
    assert generation.next_step(run[0]) == action
    assert generation.next_step(run[0]) == action


def test_unknown_submission_reuses_request_instead_of_creating_an_attempt(run):
    action = submit(run)
    waiting = receive(
        run, action, {"status": "unknown", "error_code": "CINE_GENERATION_OUTCOME_UNKNOWN"}
    )
    assert waiting["action"] == "generation_wait"
    run[3][0] += 21
    assert generation.next_step(run[0]) == action
    step = receive(run, action, submitted(action, job()))
    assert step["action"] == "generation_wait"
    assert step["submissions_reserved"] == 1
    run[3][0] += 21
    step = generation.next_step(run[0])
    assert step["operation"] == "poll" and step["arguments"]["job_id"] == "job-first"


def test_poll_interval_and_receipt_replay_survive_restart(run):
    action = submit(run)
    queued = submitted(action, job())
    waiting = receive(run, action, queued)
    assert waiting["retry_after_seconds"] == 20
    assert receive(run, action, queued) == waiting
    run[3][0] += 21
    poll = generation.next_step(run[0])
    path, digest = media(run)
    export = receive(
        run, poll, {"status": "completed", "job": job(status="succeeded", output=digest)}
    )
    assert export["arguments"] == {"action": "export_video", "job_id": "job-first"}
    review = receive(
        run,
        export,
        {
            "status": "exported",
            "job_id": "job-first",
            "media_kind": "video",
            "path": str(path),
            "sha256": digest,
        },
    )
    assert review["action"] == "review_media"
    assert review["acceptance"] == "pending"
    assert "audio" in review["unverified"]
    assert generation.next_step(run[0]) == review


@pytest.mark.parametrize(
    "field,value",
    [
        ("projectId", "other-project"),
        ("nodeId", "other-node"),
        ("kind", "image"),
        ("status", "maybe_done"),
    ],
)
def test_foreign_or_unrecognized_job_is_not_accepted(run, field, value):
    action = submit(run)
    wrong = job()
    wrong[field] = value
    with pytest.raises(ValueError):
        receive(run, action, submitted(action, wrong))
    assert generation.next_step(run[0]) == action


def test_old_receipt_cannot_overwrite_newer_job_state(run):
    action = submit(run)
    receive(run, action, submitted(action, job()))
    with pytest.raises(ValueError, match="cannot be replaced"):
        receive(run, action, submitted(action, job(status="succeeded")))


def test_terminal_failure_retries_with_new_id_within_both_limits(run):
    first = submit(run)
    preflight = receive(run, first, submitted(first, job(status="failed")))
    second = receive(run, preflight, {"status": "validated"})
    assert second["submissions_reserved"] == 2
    assert (
        second["arguments"]["generation_request_id"] != first["arguments"]["generation_request_id"]
    )
    failed = receive(run, second, submitted(second, job(status="failed", identity="second-job")))
    assert failed["reason"] == "generation_attempts_exhausted"
    assert generation.next_step(run[0], retry=True)["reason"] == "generation_attempts_exhausted"


def test_global_limit_does_not_reset_when_retried(run):
    _, path, plan, _ = run
    plan["authorization"]["max_submissions"] = 1
    write(path, plan)
    action = submit(run)
    blocked = receive(run, action, submitted(action, job(status="failed")))
    assert blocked["reason"] == "generation_submission_limit"
    assert generation.next_step(run[0], retry=True)["reason"] == "generation_submission_limit"


def test_cancelled_job_is_not_automatically_restarted(run):
    action = submit(run)
    result = receive(run, action, submitted(action, job(status="cancelled")))
    assert result["reason"] == "generation_cancelled"
    assert generation.next_step(run[0], retry=True)["reason"] == "generation_cancelled"


def test_timeout_preserves_existing_job_when_resumed(run):
    action = submit(run)
    receive(run, action, submitted(action, job()))
    run[3][0] += 3601
    assert generation.next_step(run[0])["reason"] == "generation_wait_timeout"
    resumed = generation.next_step(run[0], retry=True)
    assert resumed["operation"] == "poll"
    assert resumed["arguments"]["job_id"] == "job-first"
    assert resumed["submissions_reserved"] == 1


@pytest.mark.parametrize(
    "filename", ["outline.json", "cast.json", "art.json", "script.json", "storyboard.json"]
)
def test_upstream_changes_stop_a_live_run(run, filename):
    submit(run)
    write(run[0] / filename, {"changed": True})
    result = generation.next_step(run[0])
    assert result["reason"] == "generation_inputs_changed"
    assert result["detail"] == [str(run[0] / filename)]


def test_plan_changes_cannot_reset_budget(run):
    submit(run)
    run[2]["authorization"]["max_submissions"] = 100
    write(run[1], run[2])
    assert generation.next_step(run[0])["reason"] == "generation_plan_changed"
    assert start(run)["reason"] == "generation_plan_changed"


@pytest.mark.parametrize("corrupt", ["{", "null", "{}", '{"schema_version": 1}'])
def test_corrupt_progress_is_not_treated_as_a_new_run(run, corrupt):
    submit(run)
    (run[0] / generation.STATE).write_text(corrupt, encoding="utf-8")
    with pytest.raises(ValueError):
        start(run)


def test_export_requires_current_job_bytes_and_topic_path(run, tmp_path):
    action = submit(run)
    path, digest = media(run)
    export = receive(run, action, submitted(action, job(status="succeeded", output=digest)))
    wrong = tmp_path / "outside.mp4"
    wrong.write_bytes(path.read_bytes())
    result = {
        "status": "exported",
        "job_id": "job-first",
        "media_kind": "video",
        "path": str(wrong),
        "sha256": digest,
    }
    with pytest.raises(ValueError, match="inside this Topic"):
        receive(run, export, result)
    result["path"] = str(path)
    result["sha256"] = "a" * 64
    with pytest.raises(ValueError, match="bytes"):
        receive(run, export, result)
    result["sha256"] = digest
    receive(run, export, result)
    path.write_bytes(b"replaced")
    with pytest.raises(ValueError, match="bytes"):
        generation.next_step(run[0])


def test_export_cannot_substitute_an_unrelated_file_with_a_valid_hash(run):
    action = submit(run)
    path, digest = media(run)
    export = receive(run, action, submitted(action, job(status="succeeded", output="f" * 64)))
    with pytest.raises(ValueError, match="exact job"):
        receive(
            run,
            export,
            {
                "status": "exported",
                "job_id": "job-first",
                "media_kind": "video",
                "path": str(path),
                "sha256": digest,
            },
        )


def add_second(run):
    _, path, plan, _ = run
    plan["items"].append(
        {
            **plan["items"][0],
            "id": "second",
            "node_id": "node-second",
            "production_pointer": "/episodes/0/segments/1/h3Prompt",
            "depends_on": ["first"],
        }
    )
    write(path, plan)


def continuation_result(run, *, old=False):
    state = production.read(run[0] / generation.STATE)
    previous = state["items"]["first"]
    return {
        "status": "completed",
        "seedance_project_id": "project-one",
        "nodes": [
            {
                "id": "tail-frame",
                "type": "image",
                "data": {
                    "continuationSourceNodeId": "node-first",
                    "continuationTargetNodeId": "node-second",
                    "continuationSourceJobId": "old-job" if old else previous["job_id"],
                    "continuationSourceOutputRef": "local://" + previous["export"]["sha256"],
                    "activeOutputRef": "local://" + "d" * 64,
                },
            },
            {
                "id": "node-second",
                "type": "video_prompt",
                "references": [{"id": "node:tail-frame"}],
            },
        ],
    }


def test_continuation_waits_for_predecessor_export_then_preflights_again(run):
    add_second(run)
    first = submit(run)
    queued = receive(run, first, submitted(first, job()))
    assert queued["action"] == "generation_wait"
    run[3][0] += 21
    poll = generation.next_step(run[0])
    path, digest = media(run)
    export = receive(
        run, poll, {"status": "completed", "job": job(status="succeeded", output=digest)}
    )
    assert export["item_id"] == "first"
    continuation = receive(
        run,
        export,
        {
            "status": "exported",
            "job_id": "job-first",
            "media_kind": "video",
            "path": str(path),
            "sha256": digest,
        },
    )
    assert continuation["operation"] == "continuation"
    preflight = receive(run, continuation, continuation_result(run))
    assert preflight["item_id"] == "second" and preflight["operation"] == "preflight"
    blocked = receive(
        run, preflight, {"status": "rejected", "error_code": "PROMPT_FORMAT_INVALID"}
    )
    assert blocked["reason"] == "generation_preflight_failed"
    assert blocked["submissions_reserved"] == 1


def test_cannot_omit_a_planned_continuation_dependency(run):
    add_second(run)
    run[2]["items"][1]["depends_on"] = []
    write(run[1], run[2])
    with pytest.raises(ValueError, match="predecessor"):
        start(run)


def test_plan_rejects_dependency_cycles(run):
    add_second(run)
    run[2]["items"][0]["depends_on"] = ["second"]
    write(run[1], run[2])
    with pytest.raises(ValueError, match="acyclic"):
        start(run)


def test_assembly_is_requested_only_after_all_selected_exports(run):
    add_second(run)
    run[2]["assembly"] = ["first", "second"]
    write(run[1], run[2])
    first = submit(run)
    continuation = finish_item(run, first)
    preflight = receive(run, continuation, continuation_result(run))
    second = receive(run, preflight, {"status": "validated"})
    final = finish_item(run, second, item="second", name="second.mp4")
    assert final["action"] == "assemble_media"
    assert final["items"] == ["first", "second"]


def test_old_tail_frame_cannot_start_the_continued_segment(run):
    add_second(run)
    first = submit(run)
    continuation = finish_item(run, first)
    blocked = receive(run, continuation, continuation_result(run, old=True))
    assert blocked["reason"] == "generation_continuation_unverified"
    assert blocked["submissions_reserved"] == 1


def test_new_reference_image_cannot_silently_approve_dependent_video(run):
    directory, path, plan, _ = run
    plan["items"].insert(
        0,
        {
            "id": "image",
            "node_id": "node-image",
            "kind": "image",
            "model": "test-video-model",
            "production_stage": "cast",
            "production_pointer": "/prompt",
        },
    )
    plan["items"][1]["depends_on"] = ["image"]
    write(path, plan)
    action = submit(run)
    image_path, digest = media(run, "reference.png")
    image_job = {**job("image", "succeeded", digest), "kind": "image"}
    export = receive(run, action, submitted(action, image_job))
    review = receive(
        run,
        export,
        {
            "status": "exported",
            "job_id": "job-image",
            "media_kind": "image",
            "path": str(image_path),
            "sha256": digest,
        },
    )
    assert review["action"] == "review_media"
    assert review["pending_items"] == ["first"]
    assert review["submissions_reserved"] == 1
    assert generation.next_step(directory) == review


@pytest.mark.parametrize("failure", ["failed", "cancelled"])
def test_exhausted_or_cancelled_item_still_drains_other_paid_jobs(run, failure):
    directory, path, plan, clock = run
    document = production.read(directory / "storyboard.json")
    document["episodes"][0]["segments"][1].pop("continuesFrom")
    write(directory / "storyboard.json", document)
    add_second(run)
    plan["items"][1]["depends_on"] = []
    plan["authorization"]["max_submissions"] = 2
    write(path, plan)
    first = submit(run)
    preflight = receive(run, first, submitted(first, job()))
    second = receive(run, preflight, {"status": "validated"})
    receive(run, second, submitted(second, job("second")))
    clock[0] += 21
    poll = generation.next_step(directory)
    next_poll = receive(run, poll, {"status": "completed", "job": job(status=failure)})
    assert next_poll["operation"] == "poll"
    assert next_poll["arguments"]["job_id"] == "job-second"
    clip_path, digest = media(run, "second.mp4")
    export = receive(
        run, next_poll, {"status": "completed", "job": job("second", "succeeded", digest)}
    )
    blocked = receive(
        run,
        export,
        {
            "status": "exported",
            "job_id": "job-second",
            "media_kind": "video",
            "path": str(clip_path),
            "sha256": digest,
        },
    )
    assert blocked["action"] == "escalate"
    assert blocked["exports"]["second"]["job_id"] == "job-second"
    assert blocked["submissions_reserved"] == 2


def test_next_connects_canvas_delivery_to_media_and_resumes_without_draft_edits(run, monkeypatch):
    directory, plan_path, _, _ = run
    calls = []

    def passed(directory, stage, *_args, **_kwargs):
        calls.append(stage)
        path = directory / f"{stage}.json"
        return {"status": "passed", "input_hashes": {str(path): production.digest(path)}}

    monkeypatch.setattr(production, "check_stage", passed)
    assert autopilot.next_step(directory, generation_plan=plan_path)["action"] == "import_canvas"
    receipt = write(directory / "canvas.json", {"status": "synced"})
    action = autopilot.record_canvas_sync(directory, receipt)
    assert action["operation"] == "preflight"
    assert autopilot.next_step(directory) == action
    assert len(calls) == 5
    with pytest.raises(ValueError, match="text-only"):
        autopilot.next_step(directory, text_only=True)


def native_checks(monkeypatch, *, failing_stage=None):
    checked = []

    def check(directory, stage, *_args, **_kwargs):
        checked.append(stage)
        path = directory / f"{stage}.json"
        return {
            "status": "failed" if stage == failing_stage else "passed",
            "stderr": "Native stage still needs repair" if stage == failing_stage else "",
            "input_hashes": {str(path): production.digest(path)},
        }

    monkeypatch.setattr(production, "check_stage", check)
    return checked


def test_existing_nodes_plan_starts_preflight_after_all_native_checks_without_import(
    run, monkeypatch
):
    directory, plan_path, plan, _ = run
    plan["canvas_strategy"] = "existing_nodes"
    plan["items"][0]["node_id"] = "independent-revision-node"
    write(plan_path, plan)
    checked = native_checks(monkeypatch)
    action = autopilot.next_step(directory, generation_plan=plan_path)
    assert checked == ["outline", "cast", "art", "script", "storyboard"]
    assert action["action"] == "generation_tool" and action["operation"] == "preflight"
    assert action["arguments"]["action"] == "validate_generation"
    assert action["arguments"]["node_id"] == "independent-revision-node"
    assert action["arguments"]["project_id"] == plan["project_id"]
    assert action["submissions_reserved"] == 0
    draft = production.read(directory / autopilot.STATE)
    assert "canvas_synced" not in draft and "delivery" not in draft
    assert all(draft["stages"][stage]["passed_inputs"] for stage in checked)
    assert autopilot.next_step(directory) == action
    assert len(checked) == 5


def test_existing_node_preflight_rejection_stops_without_reserving_paid_generation(
    run, monkeypatch
):
    directory, plan_path, plan, _ = run
    plan["canvas_strategy"] = "existing_nodes"
    write(plan_path, plan)
    native_checks(monkeypatch)
    action = autopilot.next_step(directory, generation_plan=plan_path)
    receipt = write(
        directory / "provider-preflight.json",
        {
            "action_id": action["action_id"],
            "result": {
                "status": "rejected",
                "error_code": "CINE_GENERATION_NODE_BINDING_MISMATCH",
            },
        },
    )
    result = autopilot.next_step(directory, generation_receipt=receipt)
    assert result["reason"] == "generation_preflight_failed"
    assert result["submissions_reserved"] == 0
    state = production.read(directory / generation.STATE)
    assert state["items"]["first"]["attempts"] == 0
    assert "canvas_synced" not in production.read(directory / autopilot.STATE)


@pytest.mark.parametrize("stage", ["outline", "cast", "art", "script", "storyboard"])
def test_existing_nodes_never_skips_a_failed_native_stage(run, monkeypatch, stage):
    directory, plan_path, plan, _ = run
    plan["canvas_strategy"] = "existing_nodes"
    write(plan_path, plan)
    checked = native_checks(monkeypatch, failing_stage=stage)
    result = autopilot.next_step(directory, generation_plan=plan_path)
    assert result["action"] == "edit" and result["target_stage"] == stage
    assert checked[-1] == stage
    assert not (directory / generation.STATE).exists()


def test_existing_nodes_does_not_skip_blocking_native_review_queue(run, monkeypatch):
    directory, plan_path, plan, _ = run
    plan["canvas_strategy"] = "existing_nodes"
    write(plan_path, plan)
    native_checks(monkeypatch)
    write(
        directory / autopilot.QUEUE,
        {
            "items": [
                {
                    "stage": "storyboard",
                    "source": "canvas",
                    "severity": "blocking",
                    "reason": "prompt_references_unaligned",
                }
            ]
        },
    )
    result = autopilot.next_step(directory, generation_plan=plan_path)
    assert result["reason"] == "generation_blocked_by_review"
    assert not (directory / generation.STATE).exists()
    assert "canvas_synced" not in production.read(directory / autopilot.STATE)


@pytest.mark.parametrize("strategy", [None, "import_storyboard"])
def test_default_canvas_strategy_still_requires_storyboard_import(run, monkeypatch, strategy):
    directory, plan_path, plan, _ = run
    if strategy is not None:
        plan["canvas_strategy"] = strategy
    write(plan_path, plan)
    native_checks(monkeypatch)
    result = autopilot.next_step(directory, generation_plan=plan_path)
    assert result["action"] == "import_canvas"
    assert result["arguments"]["action"] == "import_storyboard"
    assert not (directory / generation.STATE).exists()


@pytest.mark.parametrize("strategy", ["skip", "existing_node", None, True, {}])
def test_unknown_or_invalid_canvas_strategy_is_rejected(run, strategy):
    directory, plan_path, plan, _ = run
    plan["canvas_strategy"] = strategy
    write(plan_path, plan)
    with pytest.raises(ValueError, match="canvas_strategy"):
        generation.validate_plan(directory, plan)
    assert not (directory / generation.STATE).exists()


def test_existing_nodes_explicit_plan_recovers_prior_text_only_choice(run, monkeypatch):
    directory, plan_path, plan, _ = run
    plan["canvas_strategy"] = "existing_nodes"
    write(plan_path, plan)
    native_checks(monkeypatch)
    assert autopilot.next_step(directory, text_only=True)["delivery"] == "text"
    action = autopilot.next_step(directory, generation_plan=plan_path)
    assert action["operation"] == "preflight"
    saved = production.read(directory / autopilot.STATE)
    assert "delivery" not in saved and "canvas_synced" not in saved
    assert saved["generation_plan"] == str(plan_path.resolve())
