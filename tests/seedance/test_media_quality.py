"""Offline runner reviews bind real local media, V3 jobs and signed cached verdicts."""

import copy
import hashlib
import importlib
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from agentnexus.seedance import media_quality
from agentnexus.seedance.media_quality_contract import CRITERIA
from agentnexus.seedance.media_quality_provider import MediaReviewUnavailable, probe_video
from agentnexus.seedance.media_quality_receipts import MediaQualityReceiptError, verify_review
from agentnexus.seedance.skill_modules import pipeline_module

SKILLS = Path(__file__).parents[2] / "examples/cine/skills"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def video(path, color="red", duration=0.4):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and FFprobe are needed for offline real-media runner tests")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=64x48:r=10:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ac",
            "1",
            "-t",
            str(duration),
            "-n",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return path


def model_report(request):
    observations = []
    checks = []
    identities = []
    for item in request["items"]:
        identity = item["item_id"]
        identities.append(identity)
        start = item["timeline_start_seconds"] if request["assembly"] else 0.0
        observations.append(
            {
                "id": identity,
                "media_receipt_id": "rendered-media",
                "item_id": identity,
                "cut_id": None,
                "start_seconds": start,
                "end_seconds": start + item["duration_seconds"],
                "observation": "实际测试视频显示一块稳定色面并播放连续测试音。",
            }
        )
        checks.append(
            {
                "item_id": identity,
                "checks": [
                    {
                        "criterion": criterion,
                        "decision": "pass",
                        "confidence": 0.95,
                        "evidence_ids": [identity],
                        "rationale": "测试替身返回固定判断，仅验证回执路径。",
                    }
                    for criterion in CRITERIA
                ],
            }
        )
    return {
        "schema_version": 1,
        "decision": "pass",
        "summary": "离线评审替身，非电影质量结论。",
        "observations": observations,
        "item_checks": checks,
        "requirement_checks": [
            {
                "requirement_id": row["id"],
                "decision": "pass",
                "confidence": 0.95,
                "evidence_ids": identities,
                "rationale": "在真实输入上传步骤外使用评审替身。",
            }
            for row in request["requirements"]
        ],
        "film_checks": [
            {
                "criterion": criterion,
                "decision": "pass",
                "confidence": 0.95,
                "item_ids": identities,
                "evidence_ids": identities,
                "rationale": "顺序检查整部测试片段，未调用真实付费评审。",
            }
            for criterion in ("story_causality", "rhythm")
        ],
        "issues": [],
    }


@pytest.fixture
def review_run(tmp_path, monkeypatch, request):
    count = getattr(request, "param", 1)
    topic = tmp_path / "topic"
    directory = topic / "production"
    directory.mkdir(parents=True)
    monkeypatch.setenv("AGENTNEXUS_DATA_DIR", str(tmp_path / "runtime-private"))
    monkeypatch.delenv("AGENTNEXUS_MEDIA_REVIEW_MODEL", raising=False)
    prod = pipeline_module(SKILLS, "production")
    generation = pipeline_module(SKILLS, "generation")
    assembly_module = importlib.import_module(generation.__package__ + ".media_assembly")
    for stage in ("outline", "cast", "art", "script"):
        write(directory / f"{stage}.json", {"stage": stage, "intent": "approved source design"})
    write(
        directory / "storyboard.json",
        {
            "episodes": [
                {
                    "segments": [
                        {"id": f"segment-{index}", "h3Prompt": f"approved prompt {index}"}
                        for index in range(count)
                    ]
                }
            ]
        },
    )
    items, records, jobs = [], {}, {}
    for index in range(count):
        identity = f"item-{index}"
        path = video(topic / "exports" / f"{identity}.mp4", "red" if index == 0 else "blue")
        sha = digest(path)
        job_id = f"job-{index}"
        items.append(
            {
                "id": identity,
                "kind": "video",
                "node_id": f"node-{index}",
                "model": "authorized-generation-model",
                "production_stage": "storyboard",
                "production_pointer": f"/episodes/0/segments/{index}/h3Prompt",
            }
        )
        exported = {"job_id": job_id, "path": str(path), "sha256": sha, "media_kind": "video"}
        records[identity] = {
            "status": "exported",
            "attempts": 1,
            "job_id": job_id,
            "request_id": f"request-{index}",
            "export": exported,
        }
        jobs[job_id] = {
            "id": job_id,
            "status": "succeeded",
            "kind": "video",
            "nodeId": f"node-{index}",
            "projectId": "project",
            "outputRefs": ["local://" + sha],
        }
    plan = {
        "schema_version": 1,
        "project_id": "project",
        "authorization": {
            "allowed": True,
            "scope": "Authorized local quality test",
            "models": ["authorized-generation-model"],
            "max_submissions": 4,
            "max_attempts_per_item": 2,
        },
        "items": items,
        "quality": {
            "requirements": [{"id": "R1", "text": "Keep action, response and ending legible."}],
            "max_review_calls": 3,
            "max_rework_rounds": 1,
            "max_design_edits": 1,
        },
    }
    if count > 1:
        plan["assembly"] = [item["id"] for item in items]
    plan_path = write(directory / "generation-plan.json", plan)
    state = {
        "run_id": "run-one",
        "plan_path": str(plan_path),
        "plan": plan,
        "plan_sha256": generation._hash(plan),
        "inputs": generation._inputs(directory, None),
        "items": records,
    }
    if count > 1:
        state["assembly"] = assembly_module.assemble(
            directory, [r["export"] for r in records.values()]
        )
        assert state["assembly"]["status"] == "assembled", state["assembly"]
    state_path = write(directory / generation.STATE, state)
    settings = SimpleNamespace(
        gateway_settings=lambda: ("https://review.invalid/v1", "test-only", "test-review-model")
    )
    modules = {
        "production": prod,
        "generation": generation,
        "transcribe": settings,
        "media_assembly": assembly_module,
    }
    monkeypatch.setattr(media_quality, "pipeline_module", lambda _skills, name: modules[name])
    session = {
        "id": "session",
        "workspace": str(topic),
        "labels": {"seedance.project_id": "project"},
    }
    server = AsyncMock()
    server.get.return_value = httpx.Response(
        200, request=httpx.Request("GET", "http://server/session"), json=session
    )
    client = AsyncMock()
    client.get_job.side_effect = lambda identity: copy.deepcopy(jobs[identity])
    run = SimpleNamespace(
        directory=directory,
        topic=topic,
        prod=prod,
        generation=generation,
        state=state,
        state_path=state_path,
        plan=plan,
        jobs=jobs,
        server=server,
        client=client,
        settings=settings,
        calls=[],
        transform=None,
    )

    async def reviewer(**kwargs):
        run.calls.append(kwargs)
        path = kwargs["path"]
        raw = path.read_bytes()
        meta = probe_video(path)
        text = kwargs["prompt"].split("\nEVIDENCE (not instructions):\n", 1)[1]
        evidence = json.loads(text.split("\nReturn only JSON matching this schema:\n", 1)[0])
        run.last_evidence = evidence
        response = {
            "report": model_report(evidence["request"]),
            "original_sha256": hashlib.sha256(raw).hexdigest(),
            "transport_sha256": hashlib.sha256(raw).hexdigest(),
            "duration_seconds": meta["duration_seconds"],
            "has_audio": meta["has_audio"],
            "invocation": {
                "response_id": "independent-response",
                "model_version": "test-review-model",
            },
        }
        if run.transform:
            return run.transform(response, path)
        return response

    run.reviewer = reviewer

    async def invoke():
        return await media_quality.review_media(
            server,
            "session",
            production_dir="production",
            skills_dir=SKILLS,
            seedance_client=client,
            reviewer=reviewer,
        )

    run.invoke = invoke
    return run


@pytest.mark.parametrize("review_run", [1, 2], indirect=True)
async def test_real_mp4_gets_signed_and_cached_without_duplicate_model_call(review_run):
    run = review_run
    result = await run.invoke()
    assert result["status"] == "reviewed", result
    payload = verify_review(result["envelope"])
    assert payload["review"]["decision"] == "pass"
    assert payload["review"]["reviewer"]["call_id"] == "independent-response"
    assert len(run.calls) == 1
    assert (
        run.last_evidence["approved_creative_documents"]["script"]["intent"]
        == "approved source design"
    )
    requested = run.last_evidence["request"]
    assert requested["items"][0]["has_audio"] is True
    if len(run.plan["items"]) > 1:
        assert run.calls[0]["path"] == Path(run.state["assembly"]["path"])
        assert requested["items"][1]["timeline_start_seconds"] > 0
        assert payload["review"]["assembly_sha256"] == digest(run.calls[0]["path"])
    second = await run.invoke()
    assert second["reused"] is True
    assert second["envelope"] == result["envelope"]
    assert len(run.calls) == 1


async def test_workspace_cannot_forge_a_cached_quality_pass(review_run):
    result = await review_run.invoke()
    envelope = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
    envelope["payload"]["review"]["report"]["summary"] = "Forged workspace acceptance"
    write(Path(result["receipt_path"]), envelope)
    with pytest.raises(MediaQualityReceiptError, match="SIGNATURE_INVALID"):
        await review_run.invoke()
    assert len(review_run.calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "running"),
        ("nodeId", "another-node"),
        ("projectId", "another-project"),
        ("outputRefs", ["local://" + "0" * 64]),
        ("id", "a-different-job"),
    ],
)
async def test_job_identity_must_match_actual_v3_output_before_review(review_run, field, value):
    review_run.jobs["job-0"][field] = value
    with pytest.raises(ValueError, match="job binding"):
        await review_run.invoke()
    assert review_run.calls == []


@pytest.mark.parametrize("review_run", [2], indirect=True)
async def test_reordered_assembly_receipt_is_rejected_before_review(review_run):
    review_run.state["assembly"]["clips"].reverse()
    write(review_run.state_path, review_run.state)
    with pytest.raises(ValueError, match="older or reordered"):
        await review_run.invoke()
    assert review_run.calls == []


@pytest.mark.parametrize("review_run", [2], indirect=True)
async def test_unrelated_same_duration_assembly_cannot_borrow_real_clip_receipts(review_run):
    run = review_run
    forged = video(run.topic / "exports" / "unrelated.mp4", "green", duration=0.8)
    run.state["assembly"].update(path=str(forged), sha256=digest(forged))
    write(run.state_path, run.state)
    with pytest.raises(ValueError, match=r"assembly|assembled|Assembly"):
        await run.invoke()
    assert run.calls == []


async def test_no_config_returns_unavailable_without_call_or_signed_receipt(review_run):
    review_run.settings.gateway_settings = lambda: ("", "", "")
    result = await review_run.invoke()
    assert result["status"] == "unavailable"
    assert "envelope" not in result
    assert review_run.calls == []


async def test_model_unavailable_consumes_bounded_call_and_does_not_pass(review_run):
    def fail(_response, _path):
        raise MediaReviewUnavailable("MODEL_UNAVAILABLE", "reviewer offline")

    review_run.transform = fail
    for _ in range(3):
        result = await review_run.invoke()
        assert result["status"] == "unavailable"
        assert result["acceptance"] == "pending"
        assert "envelope" not in result
    result = await review_run.invoke()
    assert result["error_code"] == "CINE_MEDIA_REVIEW_LIMIT"
    assert len(review_run.calls) == 3


async def test_partial_report_does_not_create_a_passing_receipt(review_run):
    def incomplete(response, _path):
        response["report"]["film_checks"] = []
        return response

    review_run.transform = incomplete
    result = await review_run.invoke()
    assert result["status"] == "unavailable"
    assert result["error_code"] == "CINE_MEDIA_REVIEW_INVALID"
    assert "envelope" not in result


async def test_unheard_soundtrack_cannot_be_promoted_to_pass(review_run):
    review_run.transform = lambda response, _path: {**response, "has_audio": False}
    result = await review_run.invoke()
    assert result["status"] == "reviewed"
    assert verify_review(result["envelope"])["review"]["decision"] == "review"


@pytest.mark.parametrize("changed", ["media", "script", "state"])
async def test_inputs_changed_during_review_prevent_signed_acceptance(review_run, changed):
    run = review_run

    def mutate(response, path):
        if changed == "media":
            path.write_bytes(b"changed while review was running")
        elif changed == "script":
            write(run.directory / "script.json", {"changed": True})
        else:
            run.state["items"]["item-0"]["attempts"] = 2
            write(run.state_path, run.state)
        return response

    run.transform = mutate
    with pytest.raises(ValueError, match=r"changed|CHANGED"):
        await run.invoke()
    caches = list((run.directory / ".cine-validation/media-quality").glob("*.json"))
    assert all(path.name == "usage.json" for path in caches)


@pytest.mark.parametrize("field,value", [("original_sha256", "0" * 64), ("duration_seconds", 0.1)])
async def test_reviewer_must_attest_to_the_bound_video_bytes_and_duration(
    review_run, field, value
):
    review_run.transform = lambda response, _path: {**response, field: value}
    with pytest.raises(ValueError, match="Reviewer input"):
        await review_run.invoke()


async def test_changed_media_invalidates_previously_signed_cache(review_run):
    await review_run.invoke()
    Path(review_run.state["items"]["item-0"]["export"]["path"]).write_bytes(b"replacement")
    with pytest.raises(ValueError, match="media changed"):
        await review_run.invoke()
    assert len(review_run.calls) == 1


async def test_state_cannot_weaken_the_pinned_user_requirements(review_run):
    run = review_run
    run.state["plan"]["quality"]["requirements"] = [{"id": "R1", "text": "Accept anything."}]
    write(run.state_path, run.state)
    with pytest.raises(ValueError, match=r"plan|Plan|PLAN"):
        await run.invoke()
    assert run.calls == []


async def test_deleting_a_workspace_ledger_cannot_reset_paid_review_allowance(review_run):
    def fail(_response, _path):
        raise MediaReviewUnavailable("MODEL_UNAVAILABLE", "reviewer offline")

    review_run.transform = fail
    for _ in range(3):
        assert (await review_run.invoke())["status"] == "unavailable"
    (review_run.directory / ".cine-validation/media-quality/usage.json").unlink(missing_ok=True)
    result = await review_run.invoke()
    assert result["error_code"] == "CINE_MEDIA_REVIEW_LIMIT"
    assert len(review_run.calls) == 3


async def test_cached_review_rechecks_media_after_awaiting_v3_job(review_run):
    run = review_run
    await run.invoke()

    async def changed_job_read(identity):
        Path(run.state["items"]["item-0"]["export"]["path"]).write_bytes(
            b"changed during job lookup"
        )
        return copy.deepcopy(run.jobs[identity])

    run.client.get_job.side_effect = changed_job_read
    with pytest.raises(ValueError, match=r"changed|CHANGED"):
        await run.invoke()
    assert len(run.calls) == 1


async def test_gpt_adjudication_preserves_observer_identity_and_reserves_two_calls(
    review_run, monkeypatch
):
    run = review_run
    monkeypatch.setenv("AGENTNEXUS_MEDIA_REVIEW_MODEL", "gpt-6.1-sol")
    monkeypatch.setenv("AGENTNEXUS_MEDIA_REVIEW_MODE", "observer_assisted")
    monkeypatch.setenv("AGENTNEXUS_MEDIA_OBSERVER_MODEL", "test-av-observer")

    def assisted(response, _path):
        for observation in response["report"]["observations"]:
            observation["media_receipt_id"] = "observer-av"
        duration = response["duration_seconds"]
        media = {
            "receipt_id": "observer-av",
            "item_id": None,
            "sha256": response["original_sha256"],
            "transport_sha256": response["transport_sha256"],
            "start_seconds": 0.0,
            "end_seconds": duration,
            "modalities": ["video", "audio"],
        }
        response.update(
            input_mode="observer_assisted",
            reviewer_modalities=["text", "image"],
            reviewer_media=[
                {
                    **media,
                    "receipt_id": "gpt-frame-0001",
                    "end_seconds": 0.001,
                    "modalities": ["image"],
                }
            ],
            observer_invocation={
                "call_id": "av-call",
                "model": "test-av-observer",
                "media": [media],
            },
            invocation={"response_id": "gpt-call", "model_version": "gpt-6.1-sol"},
        )
        return response

    run.transform = assisted
    result = await run.invoke()
    assert result["status"] == "reviewed"
    review = verify_review(result["envelope"])["review"]
    assert review["reviewer"]["model"] == "gpt-6.1-sol"
    assert review["evidence_observer"]["model"] == "test-av-observer"
    assert review["review_method"] == "observer_assisted"
    assert review["direct_av_review"] is False
    assert review["media"][0]["modalities"] == ["image"]
    assert result["review_calls"] == 2
    assert run.calls[0]["settings"][2] == "gpt-6.1-sol"
    assert run.calls[0]["observer_settings"][2] == "test-av-observer"
    assert (await run.invoke())["reused"] is True
    assert len(run.calls) == 1


async def test_assisted_review_does_not_start_with_allowance_for_only_one_call(
    review_run, monkeypatch
):
    run = review_run
    monkeypatch.setenv("AGENTNEXUS_MEDIA_REVIEW_MODEL", "gpt-6.1-sol")
    monkeypatch.setenv("AGENTNEXUS_MEDIA_REVIEW_MODE", "observer_assisted")
    run.plan["quality"]["max_review_calls"] = 1
    write(Path(run.state["plan_path"]), run.plan)
    run.state["plan_sha256"] = run.generation._hash(run.plan)
    write(run.state_path, run.state)
    result = await run.invoke()
    assert result["error_code"] == "CINE_MEDIA_REVIEW_LIMIT"
    assert run.calls == []


def controller_action(run, operation="quality"):
    run.state.setdefault("schema_version", 1)
    run.state.setdefault("sequence", 0)
    run.state.setdefault("receipts", {})
    run.state.setdefault(
        "submissions", sum(row["attempts"] for row in run.state["items"].values())
    )
    run.state.setdefault("io_failures", 0)
    run.state.setdefault("quality", {"rounds": 0})
    for row in run.state["items"].values():
        row["job"] = copy.deepcopy(run.jobs[row["job_id"]])
    action = run.generation._action(
        run.state, {"id": "__film__"}, operation, {"production_dir": str(run.directory)}
    )
    write(run.state_path, run.state)
    return action


async def test_review_saves_a_controller_receipt_that_is_consumed_without_copying(review_run):
    run = review_run
    action = controller_action(run)
    result = await run.invoke()
    path = Path(result["controller_receipt_path"])
    wrapper = json.loads(path.read_text(encoding="utf-8"))
    assert wrapper == {
        "action_id": action["action_id"],
        "result": {
            key: result[key] for key in ("status", "envelope", "receipt_path", "report_path")
        },
    }
    assert path.name.endswith(".receipt.json")
    assert path.parent == Path(result["receipt_path"]).parent
    assert action["action_id"] not in path.name
    assert "--generation-receipt" in result["after"] and str(path) in result["after"]
    assert "Do not copy" in result["after"]
    state = run.prod.read(run.state_path)
    run.generation._record(run.directory, state, wrapper, 1000.0)
    assert state["active"] is None
    step = run.generation._advance(run.directory, state, 1000.0, False)
    assert step["action"] == "done" and step["acceptance"] == "model_reviewed"
    assert len(run.calls) == 1
    repeated = await run.invoke()
    assert repeated["controller_receipt_path"] == str(path)
    assert repeated["reused"] is True and len(run.calls) == 1
    assert run.prod.read(path) == wrapper


async def test_controller_receipt_preserves_quality_redo_budget_on_consumption(review_run):
    run = review_run
    run.plan["authorization"]["max_submissions"] = 1
    write(Path(run.state["plan_path"]), run.plan)
    run.state["plan_sha256"] = run.generation._hash(run.plan)
    run.jobs["job-0"]["input"] = {"prompt": "Keep the character's face steady"}
    controller_action(run)

    def failed_render(response, _path):
        report = response["report"]
        report["decision"] = "revise"
        report["item_checks"][0]["checks"][1]["decision"] = "revise"
        report["issues"] = [
            {
                "id": "face-instability",
                "item_id": "item-0",
                "cut_id": None,
                "criterion": "performance",
                "classification": "generation_execution",
                "severity": "major",
                "confidence": 0.95,
                "requirement_ids": ["R1"],
                "evidence_ids": ["item-0"],
                "expected": "A steady face in the rendered shot",
                "source_quote": "Keep the character's face steady",
                "repair": {
                    "action": "regenerate_same_prompt",
                    "stage": None,
                    "instruction": "Retry this exact approved prompt once within budget",
                    "success_criteria": "The rendered face remains steady",
                },
            }
        ]
        return response

    run.transform = failed_render
    result = await run.invoke()
    assert result["status"] == "reviewed", result
    assert verify_review(result["envelope"])["review"]["decision"] == "revise"
    state = run.prod.read(run.state_path)
    before = copy.deepcopy(state["items"])
    run.generation._record(
        run.directory, state, run.prod.read(Path(result["controller_receipt_path"])), 1000.0
    )
    step = run.generation._advance(run.directory, state, 1000.0, False)
    assert step["reason"] == "quality_generation_allowance_exhausted"
    assert state["items"] == before and state["submissions"] == 1
    assert len(run.calls) == 1
    run.client.submit_command.assert_not_called()


async def test_cached_review_wraps_a_new_matching_action_without_new_model_work(review_run):
    run = review_run
    first_action = controller_action(run)
    first = await run.invoke()
    first_wrapper = run.prod.read(Path(first["controller_receipt_path"]))
    second_action = controller_action(run)
    assert second_action["action_id"] != first_action["action_id"]
    second = await run.invoke()
    assert second["reused"] is True and second["envelope"] == first["envelope"]
    assert second["controller_receipt_path"] != first["controller_receipt_path"]
    second_wrapper = run.prod.read(Path(second["controller_receipt_path"]))
    assert second_wrapper["action_id"] == second_action["action_id"]
    assert first_wrapper["result"] == second_wrapper["result"]
    assert len(run.calls) == 1
    run.generation._record(run.directory, run.state, second_wrapper, 1000.0)
    assert run.state["active"] is None


@pytest.mark.parametrize("active", [None, {"operation": "poll", "action_id": "not-a-review"}])
async def test_manual_or_nonquality_review_does_not_invent_a_controller_action(review_run, active):
    run = review_run
    run.state["active"] = active
    write(run.state_path, run.state)
    result = await run.invoke()
    assert "controller_receipt_path" not in result and "after" not in result
    assert list((run.directory / ".cine-validation/media-quality").glob("*.receipt.json")) == []
    cached = await run.invoke()
    assert "controller_receipt_path" not in cached
    assert len(run.calls) == 1


@pytest.mark.parametrize("updated", [None, "new-quality", "poll"])
async def test_review_handoff_uses_latest_action_after_the_model_finishes(review_run, updated):
    run = review_run
    initial = controller_action(run)

    def change_action(response, _path):
        if updated is None:
            run.state["active"] = None
            write(run.state_path, run.state)
        else:
            controller_action(run, "quality" if updated == "new-quality" else updated)
        return response

    run.transform = change_action
    result = await run.invoke()
    if updated == "new-quality":
        wrapper = run.prod.read(Path(result["controller_receipt_path"]))
        assert wrapper["action_id"] == run.state["active"]["action_id"]
        assert wrapper["action_id"] != initial["action_id"]
        run.generation._record(run.directory, run.state, wrapper, 1000.0)
    else:
        assert "controller_receipt_path" not in result
        assert (
            list((run.directory / ".cine-validation/media-quality").glob("*.receipt.json")) == []
        )
    assert len(run.calls) == 1


async def test_changed_inputs_never_get_a_controller_wrapper(review_run):
    run = review_run
    controller_action(run)

    def edit_source(response, _path):
        write(run.directory / "script.json", {"changed": "during review"})
        return response

    run.transform = edit_source
    with pytest.raises(ValueError, match=r"changed|CHANGED"):
        await run.invoke()
    assert list((run.directory / ".cine-validation/media-quality").glob("*.receipt.json")) == []
    assert len(run.calls) == 1


def _source_design_issue(response, _path):
    report = response["report"]
    report["decision"] = "revise"
    next(
        check
        for check in report["item_checks"][0]["checks"]
        if check["criterion"] == "performance"
    )["decision"] = "revise"
    report["issues"] = [
        {
            "id": "baseline-I01",
            "item_id": "item-0",
            "cut_id": None,
            "criterion": "performance",
            "classification": "source_design",
            "severity": "major",
            "confidence": 0.95,
            "requirement_ids": ["R1"],
            "evidence_ids": ["item-0"],
            "expected": "A visible response before the ending",
            "repair": {
                "action": "edit_stage",
                "stage": "storyboard",
                "instruction": "Add the missing visible response to the source design",
                "success_criteria": "The new render visibly includes the response",
            },
        }
    ]
    return response


def _resolved_baseline_issue(response, _path):
    response["report"]["regression_checks"] = [
        {
            "issue_id": "baseline-I01",
            "decision": "resolved",
            "confidence": 0.95,
            "evidence_ids": ["item-0"],
            "rationale": "The replacement visibly includes the response",
        }
    ]
    return response


@pytest.fixture
async def revision_run(review_run):
    run = review_run
    run.transform = _source_design_issue
    prior = await run.invoke()
    assert prior["status"] == "reviewed", prior
    source = run.directory
    old_state = copy.deepcopy(run.state)
    run.baseline_receipt = Path(prior["receipt_path"])
    run.baseline_envelope = copy.deepcopy(prior["envelope"])
    run.baseline_report = copy.deepcopy(verify_review(prior["envelope"])["review"]["report"])
    run.baseline_source = source
    run.baseline_state_path = run.state_path
    directory = run.topic / "production-revision-01"
    directory.mkdir()
    for name in ("outline", "cast", "art", "script", "storyboard"):
        shutil.copyfile(source / f"{name}.json", directory / f"{name}.json")
    board = run.prod.read(directory / "storyboard.json")
    board["episodes"][0]["segments"][0]["h3Prompt"] = "Newly authorized revised scene"
    write(directory / "storyboard.json", board)
    plan = copy.deepcopy(run.plan)
    plan["authorization"].update(scope="One new authorized revised clip", max_submissions=1)
    plan["quality"]["previous_review"] = {
        "production_dir": source.relative_to(run.topic).as_posix(),
        "receipt_path": run.baseline_receipt.relative_to(run.topic).as_posix(),
        "receipt_sha256": digest(run.baseline_receipt),
    }
    path = video(run.topic / "exports/item-0-revision.mp4", "blue")
    job = {**run.jobs["job-0"], "id": "job-revision", "outputRefs": ["local://" + digest(path)]}
    run.jobs["job-revision"] = job
    state = copy.deepcopy(old_state)
    state.update(
        run_id="run-revision",
        plan=plan,
        plan_path=str(write(directory / "generation-plan.json", plan)),
        plan_sha256=run.generation._hash(plan),
        inputs=run.generation._inputs(directory, None),
    )
    state.pop("quality", None)
    state["items"]["item-0"].update(
        job_id="job-revision",
        request_id="request-revision",
        export={
            "job_id": "job-revision",
            "path": str(path),
            "sha256": digest(path),
            "media_kind": "video",
        },
    )
    run.state, run.plan, run.directory = state, plan, directory
    run.state_path = write(directory / run.generation.STATE, state)
    run.transform = _resolved_baseline_issue

    async def invoke():
        return await media_quality.review_media(
            run.server,
            "session",
            production_dir="production-revision-01",
            skills_dir=SKILLS,
            seedance_client=run.client,
            reviewer=run.reviewer,
        )

    run.invoke = invoke
    return run


def save_revision_plan(run):
    write(Path(run.state["plan_path"]), run.plan)
    run.state["plan_sha256"] = run.generation._hash(run.plan)
    write(run.state_path, run.state)


async def test_cross_revision_signed_baseline_requires_real_regression_checks(revision_run):
    run = revision_run
    original = run.baseline_receipt.read_bytes()
    result = await run.invoke()
    assert result["status"] == "reviewed", result
    payload = verify_review(result["envelope"])
    assert run.last_evidence["previous_review"] == run.baseline_report
    assert payload["review"]["plan_sha256"] == run.state["plan_sha256"]
    assert payload["baseline_review"]["plan_sha256"] != run.state["plan_sha256"]
    assert payload["baseline_review"]["receipt_sha256"] == digest(run.baseline_receipt)
    assert (
        payload["baseline_review"]["snapshot_sha256"]
        == run.baseline_envelope["payload"]["snapshot_sha256"]
    )
    assert payload["review"]["report"]["regression_checks"][0]["decision"] == "resolved"
    assert run.baseline_receipt.read_bytes() == original
    cached = await run.invoke()
    assert cached["reused"] is True and cached["envelope"] == result["envelope"]
    assert len(run.calls) == 2


async def test_cross_revision_cannot_omit_old_issue_regression(revision_run):
    run = revision_run
    run.transform = None
    result = await run.invoke()
    assert (
        result["status"] == "unavailable" and result["error_code"] == "CINE_MEDIA_REVIEW_INVALID"
    )
    assert "regression checks" in result["error"]
    assert "envelope" not in result


@pytest.mark.parametrize(
    "change", ["forged", "session", "project", "source", "media", "receipt_hash"]
)
async def test_cross_revision_baseline_tampering_is_rejected_before_model(revision_run, change):
    from agentnexus.seedance.media_quality_receipts import sign_review

    run = revision_run
    if change in {"forged", "session"}:
        prior = run.prod.read(run.baseline_receipt)
        if change == "forged":
            prior["payload"]["review"]["report"]["summary"] = "Forged old evidence"
        else:
            prior["payload"]["session_id"] = "another-topic-session"
            prior = sign_review(prior["payload"])
        write(run.baseline_receipt, prior)
        run.plan["quality"]["previous_review"]["receipt_sha256"] = digest(run.baseline_receipt)
        save_revision_plan(run)
    elif change == "project":
        old = run.prod.read(run.baseline_state_path)
        old["plan"]["project_id"] = "another-project"
        write(run.baseline_state_path, old)
    elif change == "source":
        write(run.baseline_source / "script.json", {"modified": True})
    elif change == "media":
        old = run.prod.read(run.baseline_state_path)
        Path(old["items"]["item-0"]["export"]["path"]).write_bytes(b"replaced old evidence")
    else:
        run.plan["quality"]["previous_review"]["receipt_sha256"] = "0" * 64
        save_revision_plan(run)
    with pytest.raises(ValueError):
        await run.invoke()
    assert len(run.calls) == 1


@pytest.mark.parametrize("change", ["weaken_text", "remove_requirement", "remove_issue_item"])
async def test_cross_revision_cannot_weaken_requirements_or_drop_defective_items(
    revision_run, change
):
    run = revision_run
    if change == "weaken_text":
        run.plan["quality"]["requirements"][0]["text"] = "Accept anything"
    elif change == "remove_requirement":
        run.plan["quality"]["requirements"] = [
            {"id": "replacement", "text": "Different requirement"}
        ]
    else:
        run.plan["items"][0]["id"] = "different-item"
        run.state["items"]["different-item"] = run.state["items"].pop("item-0")
    save_revision_plan(run)
    with pytest.raises(ValueError, match=r"BASELINE_(REQUIREMENTS_CHANGED|ITEMS_MISSING)"):
        await run.invoke()
    assert len(run.calls) == 1


async def test_cross_revision_may_add_stricter_requirements(revision_run):
    run = revision_run
    run.plan["quality"]["requirements"].append(
        {"id": "R2", "text": "The ending visibly closes the door"}
    )
    save_revision_plan(run)
    result = await run.invoke()
    assert result["status"] == "reviewed", result
    assert {
        r["requirement_id"]
        for r in verify_review(result["envelope"])["review"]["report"]["requirement_checks"]
    } == {"R1", "R2"}


async def test_cross_topic_baseline_path_is_not_accepted(revision_run):
    run = revision_run
    foreign = run.topic.parent / "another-topic/production"
    foreign.mkdir(parents=True)
    receipt = foreign / "review.json"
    shutil.copyfile(run.baseline_receipt, receipt)
    run.plan["quality"]["previous_review"].update(
        production_dir=str(foreign), receipt_path=str(receipt)
    )
    save_revision_plan(run)
    with pytest.raises(ValueError, match="PATH_ESCAPE"):
        await run.invoke()
    assert len(run.calls) == 1


async def test_cached_revision_revalidates_its_old_source_baseline(revision_run):
    run = revision_run
    assert (await run.invoke())["status"] == "reviewed"
    write(run.baseline_source / "script.json", {"modified": "after new receipt"})
    with pytest.raises(ValueError, match="changed"):
        await run.invoke()
    assert len(run.calls) == 2


async def test_old_source_change_while_new_review_runs_prevents_signature(revision_run):
    run = revision_run

    def changed(response, path):
        _resolved_baseline_issue(response, path)
        write(run.baseline_source / "script.json", {"modified": "during new review"})
        return response

    run.transform = changed
    with pytest.raises(ValueError, match="changed"):
        await run.invoke()
    caches = list((run.directory / ".cine-validation/media-quality").glob("*.json"))
    assert all(path.name == "usage.json" for path in caches)


async def test_cross_revision_reference_does_not_relax_existing_history_binding(revision_run):
    run = revision_run
    run.state["quality"] = {"history": [{"envelope": run.baseline_envelope}]}
    write(run.state_path, run.state)
    with pytest.raises(ValueError, match="another production"):
        await run.invoke()
    assert len(run.calls) == 1


async def test_measured_cuts_reach_the_reviewer_signed_review_and_report(review_run, monkeypatch):
    from agentnexus.seedance import media_quality_provider

    measured = {
        "method": "pyscenedetect_content",
        "threshold": 27.0,
        "status": "measured",
        "cut_seconds": [0.2],
    }
    monkeypatch.setattr(media_quality_provider, "measure_cuts", lambda _path: dict(measured))
    result = await review_run.invoke()
    assert result["status"] == "reviewed", result
    item = review_run.last_evidence["request"]["items"][0]
    assert item["cut_detection"] == measured
    assert [cut["cut_id"] for cut in item["cuts"]] == ["shot-1", "shot-2"]
    assert item["cuts"][1]["start_seconds"] == 0.2
    payload = verify_review(result["envelope"])
    assert payload["review"]["cut_detection"] == {"item-0": measured}
    report = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "item-0：0.20 秒" in report


def _two_shot_video(path, seconds=2.0):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is needed to render a real hard cut")
    pytest.importorskip("scenedetect")
    path.parent.mkdir(parents=True, exist_ok=True)
    graph = (
        f"color=c=red:s=96x64:r=24:d={seconds}[a];"
        f"testsrc2=s=96x64:r=24:d={seconds}[b];[a][b]concat=n=2:v=1:a=0"
    )
    subprocess.run(
        [
            *(ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-filter_complex", graph),
            *("-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-n", str(path)),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return path


def test_measure_cuts_finds_a_real_hard_cut_and_none_in_a_continuous_take(tmp_path):
    from agentnexus.seedance.media_quality_provider import measure_cuts

    cut = measure_cuts(_two_shot_video(tmp_path / "cut.mp4"))
    assert cut["status"] == "measured" and cut["threshold"] == 27.0
    assert len(cut["cut_seconds"]) == 1 and abs(cut["cut_seconds"][0] - 2.0) < 0.1
    take = measure_cuts(video(tmp_path / "take.mp4", duration=3.0))
    assert take == {**take, "status": "measured", "cut_seconds": []}


def test_missing_detector_is_reported_as_unmeasured(tmp_path, monkeypatch):
    import sys

    from agentnexus.seedance.media_quality_provider import measure_cuts

    monkeypatch.setitem(sys.modules, "scenedetect", None)
    result = measure_cuts(tmp_path / "absent.mp4")
    assert result["status"] == "unavailable" and result["cut_seconds"] == []
