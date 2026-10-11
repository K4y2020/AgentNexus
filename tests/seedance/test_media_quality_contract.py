"""Offline validation of real-media bindings and actionable local quality decisions."""

import copy

import pytest

from agentnexus.seedance.media_quality_contract import (
    CRITERIA,
    QualityContractError,
    measured_shots,
    quality_response_schema,
    validate_quality_report,
)


@pytest.fixture
def review_case():
    request = {
        "plan_sha256": "a" * 64,
        "requirements": [{"id": "R1", "text": "敲门之后屋内回应，来访者听到后才转身。"}],
        "items": [
            {
                "item_id": "first",
                "job_id": "job-first",
                "sha256": "b" * 64,
                "input_sha256": "c" * 64,
                "duration_seconds": 4.0,
                "timeline_start_seconds": 0.0,
                "has_audio": True,
                "prompt_text": "来访者敲门，屋内随后回应。",
                "cuts": [{"cut_id": "cut-1", "start_seconds": 0.0, "end_seconds": 4.0}],
            },
            {
                "item_id": "second",
                "job_id": "job-second",
                "sha256": "d" * 64,
                "input_sha256": "e" * 64,
                "duration_seconds": 6.0,
                "timeline_start_seconds": 4.0,
                "has_audio": True,
                "prompt_text": "镜头：听完屋内回应后才转身，保留可见的聆听与决定。",
                "cuts": [{"cut_id": "cut-2", "start_seconds": 0.0, "end_seconds": 6.0}],
            },
        ],
        "assembly": {
            "sha256": "f" * 64,
            "duration_seconds": 10.0,
            "item_ids": ["first", "second"],
        },
    }
    invocation = {
        "call_id": "real-independent-call",
        "model": "configured-review-model",
        "independent_context": True,
        "media": [
            {
                "receipt_id": "assembly-input",
                "item_id": None,
                "sha256": "f" * 64,
                "start_seconds": 0.0,
                "end_seconds": 10.0,
                "modalities": ["video", "audio"],
            }
        ],
    }
    report = {
        "schema_version": 1,
        "decision": "pass",
        "summary": "敲门、回应与离场构成可读的行动。",
        "observations": [
            {
                "id": "o1",
                "media_receipt_id": "assembly-input",
                "item_id": "first",
                "cut_id": "cut-1",
                "start_seconds": 0.0,
                "end_seconds": 4.0,
                "observation": "来访者敲门，收回手停下，屋内声音随后出现。",
            },
            {
                "id": "o2",
                "media_receipt_id": "assembly-input",
                "item_id": "second",
                "cut_id": "cut-2",
                "start_seconds": 4.0,
                "end_seconds": 10.0,
                "observation": "来访者听完回应才垂下手、转身离开，脚步声跟随动作。",
            },
            {
                "id": "whole",
                "media_receipt_id": "assembly-input",
                "item_id": None,
                "cut_id": None,
                "start_seconds": 2.0,
                "end_seconds": 8.0,
                "observation": "门内的回应跨越剪辑，后段人物听完后才离开，停顿保留了决定的过程。",
            },
        ],
        "requirement_checks": [
            {
                "requirement_id": "R1",
                "decision": "pass",
                "confidence": 0.93,
                "evidence_ids": ["o1", "o2", "whole"],
                "rationale": "回应先于离场，动作和声音顺序清楚。",
            }
        ],
        "item_checks": [
            {
                "item_id": identity,
                "checks": [
                    {
                        "criterion": criterion,
                        "decision": "pass",
                        "confidence": 0.92,
                        "evidence_ids": [observation],
                        "rationale": "人物行动、反应及声音在该段中可辨。",
                    }
                    for criterion in CRITERIA
                ],
            }
            for identity, observation in [("first", "o1"), ("second", "o2")]
        ],
        "film_checks": [
            {
                "criterion": criterion,
                "decision": "pass",
                "confidence": 0.94,
                "item_ids": ["first", "second"],
                "evidence_ids": ["o1", "o2", "whole"],
                "rationale": "前段的敲门引出回应，跨段停顿与离场形成完整因果和节奏。",
            }
            for criterion in ("story_causality", "rhythm")
        ],
        "issues": [],
    }
    return request, invocation, report


def validate(case, *, previous_report=None):
    request, invocation, report = case
    return validate_quality_report(
        report, request=request, invocation=invocation, previous_report=previous_report
    )


def issue(
    *,
    classification="generation_execution",
    action="regenerate_same_prompt",
    stage=None,
    confidence=0.93,
):
    return {
        "id": "acting-1",
        "item_id": "second",
        "cut_id": "cut-2",
        "criterion": "performance",
        "classification": classification,
        "severity": "major",
        "confidence": confidence,
        "requirement_ids": ["R1"],
        "evidence_ids": ["o2"],
        "expected": "听完屋内回应后才转身，保留可见的聆听与决定。",
        "source_quote": "听完屋内回应后才转身",
        "repair": {
            "action": action,
            "stage": stage,
            "instruction": "修复第二段的过早转身。",
            "success_criteria": "回应结束先于转身，人物反应可读。",
        },
    }


def test_complete_video_audio_and_whole_film_evidence_can_pass(review_case):
    result = validate(review_case)
    assert result["decision"] == "pass"
    assert result["coverage"] == {
        "first": {"video": True, "audio": True},
        "second": {"video": True, "audio": True},
    }
    assert result["assembly_sha256"] == "f" * 64
    assert result["items"][1]["job_id"] == "job-second"
    assert result["reviewer"]["call_id"] == "real-independent-call"
    assert result["repair_plan"]["regenerate_item_ids"] == []


def test_schema_is_strict_at_every_object_boundary():
    schema = quality_response_schema()

    def inspect(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value["additionalProperties"] is False
                optional = {
                    "QualityReport": {"regression_checks"},
                    "Issue": {"source_quote"},
                }.get(value.get("title"), set())
                assert set(value["required"]) == set(value["properties"]) - optional
            for child in value.values():
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)

    inspect(schema)


def test_film_schema_only_requests_the_two_valid_whole_film_criteria():
    field = quality_response_schema()["$defs"]["FilmCheck"]["properties"]["criterion"]
    assert set(field["enum"]) == {"story_causality", "rhythm"}


@pytest.mark.parametrize("criterion", ["performance", "continuity", "audiovisual"])
def test_film_check_rejects_dimensions_belonging_to_item_checks(review_case, criterion):
    review_case[2]["film_checks"][0]["criterion"] = criterion
    with pytest.raises(QualityContractError):
        validate(review_case)


@pytest.mark.parametrize("quote", [None, "", "   ", "来访者敲门", "提示词没有要求的关门动作"])
def test_same_prompt_retry_requires_exact_source_quote_from_that_item(review_case, quote):
    problem = issue(confidence=1.0)
    problem["source_quote"] = quote
    review_case[2]["issues"] = [problem]
    result = validate(review_case)
    assert result["decision"] == "review"
    assert result["repair_plan"]["regenerate_item_ids"] == []
    assert result["repair_plan"]["review_item_ids"] == ["second"]
    assert result["report"]["issues"][0]["repair"]["action"] == "review"
    assert result["report"]["issues"][0]["classification"] is None
    assert "issue:acting-1:source_execution_unproven" in result["limitations"]


def test_execution_retry_without_runner_supplied_prompt_stays_under_review(review_case):
    review_case[0]["items"][1].pop("prompt_text")
    review_case[2]["issues"] = [issue(confidence=1.0)]
    result = validate(review_case)
    assert result["decision"] == "review"
    assert result["repair_plan"]["regenerate_item_ids"] == []


def test_missing_quote_defaults_to_unknown_instead_of_enabling_retry(review_case):
    problem = issue()
    problem.pop("source_quote")
    review_case[2]["issues"] = [problem]
    assert validate(review_case)["report"]["issues"][0]["repair"]["action"] == "review"


def test_source_design_can_request_a_stage_edit_without_a_source_quote(review_case):
    problem = issue(classification="source_design", action="edit_stage", stage="script")
    problem.pop("source_quote")
    review_case[2]["issues"] = [problem]
    result = validate(review_case)
    assert result["decision"] == "revise"
    assert result["repair_plan"]["stage_edits"][0]["stage"] == "script"
    assert result["repair_plan"]["regenerate_item_ids"] == []


@pytest.mark.parametrize("modalities", [["image"], ["video"], ["audio"]])
def test_frames_or_missing_sound_cannot_pass_as_full_media(review_case, modalities):
    review_case[1]["media"][0]["modalities"] = modalities
    result = validate(review_case)
    assert result["decision"] == "review"
    assert any("coverage_incomplete" in value for value in result["limitations"])


def test_low_confidence_is_review_even_when_model_says_pass(review_case):
    review_case[2]["item_checks"][0]["checks"][1]["confidence"] = 0.6
    assert validate(review_case)["decision"] == "review"


def test_source_design_requires_stage_edit_and_never_regeneration(review_case):
    review_case[2]["issues"] = [
        issue(classification="source_design", action="edit_stage", stage="script")
    ]
    result = validate(review_case)
    assert result["decision"] == "revise"
    assert result["repair_plan"]["regenerate_item_ids"] == []
    assert result["repair_plan"]["stage_edits"] == [
        {"item_id": "second", "stage": "script", "issue_id": "acting-1"}
    ]
    review_case[2]["issues"][0]["repair"] = issue()["repair"]
    with pytest.raises(QualityContractError, match="source design"):
        validate(review_case)


def test_execution_defect_targets_only_failed_item(review_case):
    review_case[2]["issues"] = [issue()]
    result = validate(review_case)
    assert result["decision"] == "revise"
    assert result["repair_plan"]["regenerate_item_ids"] == ["second"]
    assert result["report"]["issues"][0]["cut_id"] == "cut-2"


def test_uncertain_defect_cannot_trigger_same_prompt_retry(review_case):
    review_case[2]["issues"] = [issue(confidence=0.4)]
    with pytest.raises(QualityContractError, match="uncertain defect"):
        validate(review_case)
    review_case[2]["issues"] = [issue(classification=None, action="review", confidence=0.4)]
    result = validate(review_case)
    assert result["decision"] == "review"
    assert result["repair_plan"]["review_item_ids"] == ["second"]


def test_design_issue_takes_priority_over_same_prompt_retry_for_same_item(review_case):
    problem = issue(classification="source_design", action="edit_stage", stage="storyboard")
    problem["id"] = "design-1"
    review_case[2]["issues"] = [issue(), problem]
    result = validate(review_case)
    assert result["repair_plan"]["regenerate_item_ids"] == []
    assert result["repair_plan"]["stage_edits"][0]["stage"] == "storyboard"


def test_execution_fix_can_join_a_design_edit_already_required_for_that_item(review_case):
    design = issue(classification="source_design", action="edit_stage", stage="storyboard")
    design["id"] = "design-1"
    execution = issue(action="edit_stage", stage="storyboard")
    review_case[2]["issues"] = [execution, design]
    result = validate(review_case)
    assert result["decision"] == "revise"
    assert result["repair_plan"]["regenerate_item_ids"] == []
    assert all(row["item_id"] == "second" for row in result["repair_plan"]["stage_edits"])


def test_execution_fix_cannot_invent_an_unrelated_design_edit(review_case):
    review_case[2]["issues"] = [issue(action="edit_stage", stage="script")]
    with pytest.raises(QualityContractError, match="execution repair"):
        validate(review_case)


def test_wrong_hash_is_rejected_and_new_input_hash_changes_review_binding(review_case):
    original = validate(review_case)
    review_case[0]["items"][1]["input_sha256"] = "1" * 64
    assert validate(review_case)["request_sha256"] != original["request_sha256"]
    review_case[1]["media"][0]["sha256"] = "2" * 64
    with pytest.raises(QualityContractError, match="media hash mismatch"):
        validate(review_case)


@pytest.mark.parametrize("change", ["outside_item", "wrong_cut", "wrong_receipt", "unknown_item"])
def test_timestamp_and_identity_claims_must_match_real_media(review_case, change):
    observation = review_case[2]["observations"][1]
    if change == "outside_item":
        observation.update(start_seconds=2.0)
    elif change == "wrong_cut":
        observation["cut_id"] = "cut-1"
    elif change == "wrong_receipt":
        observation["media_receipt_id"] = "model-invented-receipt"
    else:
        observation["item_id"] = "model-invented-item"
    with pytest.raises(QualityContractError):
        validate(review_case)


def test_all_user_requirements_and_whole_film_checks_are_required(review_case):
    original = copy.deepcopy(review_case)
    review_case[2]["requirement_checks"] = []
    with pytest.raises(QualityContractError, match="requirement coverage"):
        validate(review_case)
    original[2]["film_checks"] = []
    with pytest.raises(QualityContractError, match="whole-film"):
        validate(original)


def test_isolated_clip_reviews_do_not_replace_assembled_story_and_rhythm(review_case):
    request, invocation, report = review_case
    invocation["media"] = [
        {
            "receipt_id": item["item_id"],
            "item_id": item["item_id"],
            "sha256": item["sha256"],
            "start_seconds": 0.0,
            "end_seconds": item["duration_seconds"],
            "modalities": ["video", "audio"],
        }
        for item in request["items"]
    ]
    report["observations"] = report["observations"][:2]
    for row, item in zip(report["observations"], request["items"], strict=True):
        row["media_receipt_id"] = item["item_id"]
        row["start_seconds"] -= item["timeline_start_seconds"]
        row["end_seconds"] -= item["timeline_start_seconds"]
    report["requirement_checks"][0]["evidence_ids"] = ["o1", "o2"]
    for check in report["film_checks"]:
        check["evidence_ids"] = ["o1", "o2"]
    with pytest.raises(QualityContractError, match="assembled-video evidence"):
        validate(review_case)


def test_model_cannot_add_authority_fields_or_review_itself(review_case):
    original = copy.deepcopy(review_case)
    review_case[2]["verified"] = True
    with pytest.raises(QualityContractError):
        validate(review_case)
    original[1]["independent_context"] = False
    with pytest.raises(QualityContractError):
        validate(original)


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), 1.1, True])
def test_malformed_confidence_never_turns_into_pass(review_case, confidence):
    review_case[2]["item_checks"][0]["checks"][0]["confidence"] = confidence
    with pytest.raises(QualityContractError):
        validate(review_case)


def test_revise_requires_an_actual_local_repair_issue(review_case):
    review_case[2]["item_checks"][1]["checks"][1]["decision"] = "revise"
    with pytest.raises(QualityContractError, match="repair issue"):
        validate(review_case)


def test_unknown_checks_can_be_returned_without_invented_observations(review_case):
    check = review_case[2]["item_checks"][1]["checks"][1]
    check.update(
        decision="review", confidence=0.4, evidence_ids=[], rationale="没有足够证据判断反应。"
    )
    assert validate(review_case)["decision"] == "review"


def previous_with_issue(review_case):
    previous = copy.deepcopy(review_case[2])
    previous["decision"] = "revise"
    previous["issues"] = [issue()]
    return previous


def regression(**updates):
    return {
        "issue_id": "acting-1",
        "decision": "resolved",
        "confidence": 0.94,
        "evidence_ids": ["o2"],
        "rationale": "新视频里回应完整结束后角色才转身，先前过早反应已消失。",
        **updates,
    }


def test_first_review_defaults_to_no_regression_checks(review_case):
    assert validate(review_case)["report"]["regression_checks"] == []


def test_previous_issue_cannot_disappear_without_specific_reinspection(review_case):
    previous = previous_with_issue(review_case)
    with pytest.raises(QualityContractError, match="every previous issue_id"):
        validate(review_case, previous_report=previous)


def test_resolved_issue_requires_current_local_evidence_and_matching_checks(review_case):
    previous = previous_with_issue(review_case)
    review_case[2]["regression_checks"] = [regression()]
    result = validate(review_case, previous_report=previous)
    assert result["decision"] == "pass"
    assert result["report"]["regression_checks"][0]["decision"] == "resolved"
    assert result["repair_plan"]["review_item_ids"] == []


@pytest.mark.parametrize(
    "change", [{"confidence": 0.4}, {"evidence_ids": []}, {"decision": "review"}]
)
def test_uncertain_or_unsupported_resolution_is_review(review_case, change):
    previous = previous_with_issue(review_case)
    review_case[2]["regression_checks"] = [regression(**change)]
    result = validate(review_case, previous_report=previous)
    assert result["decision"] == "review"
    assert result["report"]["regression_checks"][0]["decision"] == "review"
    assert result["repair_plan"]["review_item_ids"] == ["second"]


def test_regression_cannot_use_another_items_evidence(review_case):
    previous = previous_with_issue(review_case)
    review_case[2]["regression_checks"] = [regression(evidence_ids=["o1"])]
    with pytest.raises(QualityContractError, match="previous issue item"):
        validate(review_case, previous_report=previous)


@pytest.mark.parametrize(
    "checks", [[regression(issue_id="invented")], [regression(), regression()]]
)
def test_regression_ids_must_match_previous_issues_exactly(review_case, checks):
    previous = previous_with_issue(review_case)
    review_case[2]["regression_checks"] = checks
    with pytest.raises(QualityContractError, match=r"previous issue_id|duplicate issue_id"):
        validate(review_case, previous_report=previous)


def test_regression_cannot_invent_a_previous_issue_on_first_review(review_case):
    review_case[2]["regression_checks"] = [regression()]
    with pytest.raises(QualityContractError, match="previous issue_id"):
        validate(review_case)


def test_persistent_issue_must_be_actionable_in_the_new_report(review_case):
    previous = previous_with_issue(review_case)
    review_case[2]["issues"] = [issue()]
    review_case[2]["item_checks"][1]["checks"][1]["decision"] = "revise"
    review_case[2]["regression_checks"] = [regression(decision="persistent")]
    result = validate(review_case, previous_report=previous)
    assert result["decision"] == "revise"
    assert result["repair_plan"]["regenerate_item_ids"] == ["second"]
    assert result["report"]["regression_checks"][0]["decision"] == "persistent"


def test_persistent_issue_cannot_be_hidden_by_new_passing_checks(review_case):
    previous = previous_with_issue(review_case)
    review_case[2]["issues"] = [issue()]
    review_case[2]["regression_checks"] = [regression(decision="persistent")]
    with pytest.raises(QualityContractError, match="contradicts current checks"):
        validate(review_case, previous_report=previous)


def test_persistent_issue_needs_matching_item_and_criterion_in_new_issues(review_case):
    previous = previous_with_issue(review_case)
    review_case[2]["regression_checks"] = [regression(decision="persistent")]
    with pytest.raises(QualityContractError, match="current matching issue"):
        validate(review_case, previous_report=previous)


def test_resolved_label_cannot_override_an_existing_defect(review_case):
    previous = previous_with_issue(review_case)
    review_case[2]["issues"] = [issue()]
    review_case[2]["item_checks"][1]["checks"][1]["decision"] = "revise"
    review_case[2]["regression_checks"] = [regression()]
    with pytest.raises(QualityContractError, match="resolved regression contradicts"):
        validate(review_case, previous_report=previous)


def test_uncertain_current_check_prevents_resolved_regression(review_case):
    previous = previous_with_issue(review_case)
    check = review_case[2]["item_checks"][1]["checks"][1]
    check.update(decision="review", confidence=0.4)
    review_case[2]["regression_checks"] = [regression()]
    result = validate(review_case, previous_report=previous)
    assert result["decision"] == "review"
    assert result["report"]["regression_checks"][0]["decision"] == "review"


def test_uncertain_regression_suppresses_blind_retry_of_that_item(review_case):
    previous = previous_with_issue(review_case)
    review_case[2]["issues"] = [issue()]
    review_case[2]["item_checks"][1]["checks"][1]["decision"] = "revise"
    review_case[2]["regression_checks"] = [regression(decision="review")]
    result = validate(review_case, previous_report=previous)
    assert result["decision"] == "revise"
    assert result["repair_plan"]["regenerate_item_ids"] == []
    assert result["repair_plan"]["review_item_ids"] == ["second"]


def assisted_case(review_case):
    request, invocation, report = copy.deepcopy(review_case)
    observer_media = invocation["media"]
    invocation.update(
        {
            "call_id": "gpt-review-call",
            "model": "gpt-6.1-sol",
            "input_mode": "observer_assisted",
            "reviewer_modalities": ["text", "image"],
            "media": [
                {
                    "receipt_id": "gpt-frames",
                    "item_id": None,
                    "sha256": "f" * 64,
                    "transport_sha256": "9" * 64,
                    "start_seconds": 0.0,
                    "end_seconds": 10.0,
                    "modalities": ["image"],
                }
            ],
            "observer": {
                "call_id": "real-video-observer-call",
                "model": "actual-av-observer",
                "independent_context": True,
                "media": observer_media,
            },
        }
    )
    return request, invocation, report


def test_observer_assisted_review_preserves_the_actual_two_model_chain(review_case):
    case = assisted_case(review_case)
    result = validate(case)
    assert result["decision"] == "pass"
    assert result["review_method"] == "observer_assisted"
    assert result["direct_av_review"] is False
    assert result["reviewer"]["model"] == "gpt-6.1-sol"
    assert result["evidence_observer"]["model"] == "actual-av-observer"
    assert result["evidence_observer"]["call_id"] == "real-video-observer-call"
    assert result["reviewer_modalities"] == ["text", "image"]
    assert result["media"][0]["modalities"] == ["image"]
    assert result["evidence_sources"] == {"gpt-frames": "reviewer", "assembly-input": "observer"}
    assert result["coverage_by_source"]["primary"]["items"]["second"] == {
        "video": False,
        "audio": False,
    }
    assert result["coverage_by_source"]["observer"]["items"]["second"] == {
        "video": True,
        "audio": True,
    }
    assert result["coverage_by_source"]["observer"]["film"] == {"video": True, "audio": True}
    assert result["coverage"] == result["coverage_by_source"]["effective"]["items"]


def test_default_direct_review_does_not_invent_an_observer(review_case):
    result = validate(review_case)
    assert result["review_method"] == "direct_av"
    assert result["direct_av_review"] is True
    assert result["evidence_observer"] is None
    assert set(result["evidence_sources"].values()) == {"reviewer"}


@pytest.mark.parametrize("missing", ["observer", "media", "video", "audio"])
def test_missing_observer_av_cannot_pass_using_gpt_images(review_case, missing):
    case = assisted_case(review_case)
    if missing == "observer":
        case[1].pop("observer")
    elif missing == "media":
        case[1]["observer"]["media"] = []
    else:
        case[1]["observer"]["media"][0]["modalities"].remove(missing)
    for observation in case[2]["observations"]:
        observation["media_receipt_id"] = "gpt-frames"
    result = validate(case)
    assert result["decision"] == "review"
    assert result["direct_av_review"] is False
    assert any("coverage_incomplete" in value for value in result["limitations"])


@pytest.mark.parametrize("field,value", [("sha256", "0" * 64), ("end_seconds", 11.0)])
def test_observer_media_has_the_same_hash_and_time_checks_as_primary(review_case, field, value):
    case = assisted_case(review_case)
    case[1]["observer"]["media"][0][field] = value
    with pytest.raises(QualityContractError, match=r"hash mismatch|invalid time range"):
        validate(case)


@pytest.mark.parametrize("modalities", [["video", "audio"], ["image", "audio"], ["text", "video"]])
def test_assisted_gpt_cannot_claim_direct_av_modalities(review_case, modalities):
    case = assisted_case(review_case)
    case[1]["reviewer_modalities"] = modalities
    with pytest.raises(QualityContractError, match="only text/image"):
        validate(case)


def test_assisted_primary_media_must_be_actual_image_transports(review_case):
    case = assisted_case(review_case)
    case[1]["media"][0].pop("transport_sha256")
    with pytest.raises(QualityContractError, match="actual image transports"):
        validate(case)
    case[1]["media"][0]["transport_sha256"] = "9" * 64
    case[1]["media"][0]["modalities"] = ["video", "audio"]
    with pytest.raises(QualityContractError, match="actual image transports"):
        validate(case)


def test_direct_label_cannot_borrow_observer_evidence(review_case):
    case = assisted_case(review_case)
    case[1]["input_mode"] = "direct_av"
    with pytest.raises(QualityContractError, match="cannot borrow"):
        validate(case)


def test_observer_and_reviewer_call_ids_and_media_ids_must_be_distinct(review_case):
    case = assisted_case(review_case)
    case[1]["observer"]["call_id"] = case[1]["call_id"]
    with pytest.raises(QualityContractError, match="separate invocation"):
        validate(case)
    case[1]["observer"]["call_id"] = "actual-observer"
    case[1]["media"][0]["receipt_id"] = case[1]["observer"]["media"][0]["receipt_id"]
    with pytest.raises(QualityContractError, match="duplicate receipt_id"):
        validate(case)


def test_observer_evidence_does_not_relax_user_requirement_or_finding_checks(review_case):
    case = assisted_case(review_case)
    case[2]["requirement_checks"] = []
    with pytest.raises(QualityContractError, match="requirement coverage"):
        validate(case)
    case = assisted_case(review_case)
    problem = issue()
    problem["source_quote"] = None
    case[2]["issues"] = [problem]
    result = validate(case)
    assert result["decision"] == "review"
    assert result["repair_plan"]["regenerate_item_ids"] == []


def test_partial_observer_video_remains_review_even_when_audio_is_complete(review_case):
    case = assisted_case(review_case)
    source = case[1]["observer"]["media"][0]
    source["modalities"] = ["audio"]
    case[1]["observer"]["media"].append(
        {
            **source,
            "receipt_id": "partial-observer-video",
            "modalities": ["video"],
            "end_seconds": 4.0,
        }
    )
    result = validate(case)
    assert result["decision"] == "review"
    assert result["coverage_by_source"]["observer"]["items"]["first"]["video"] is True
    assert result["coverage_by_source"]["observer"]["items"]["second"]["video"] is False


def _measured(cut_seconds):
    return {
        "status": "measured",
        "method": "pyscenedetect_content",
        "threshold": 27.0,
        "cut_seconds": cut_seconds,
    }


def test_measured_single_shot_is_reported_with_the_review(review_case):
    request = review_case[0]
    request["items"][0]["cut_detection"] = _measured([])
    request["items"][0]["cuts"] = measured_shots([], 4.0)
    request["items"][0]["cuts"][0]["cut_id"] = "cut-1"
    with pytest.raises(QualityContractError, match="measured shots"):
        validate(review_case)
    request["items"][0]["cuts"] = measured_shots([], 4.0)
    review_case[2]["observations"][0]["cut_id"] = "shot-1"
    result = validate(review_case)
    assert result["decision"] == "pass"
    assert result["cut_detection"] == {"first": _measured([])}


@pytest.mark.parametrize("cut_seconds", [[2.5, 1.0], [0.0], [4.0], [1.0, 1.0]])
def test_measured_cuts_must_lie_ordered_inside_the_item(review_case, cut_seconds):
    item = review_case[0]["items"][0]
    item["cut_detection"] = _measured(cut_seconds)
    item["cuts"] = measured_shots(cut_seconds, 4.0)
    with pytest.raises(QualityContractError):
        validate(review_case)


def test_measured_hard_cut_splits_the_item_into_citable_shots(review_case):
    item = review_case[0]["items"][0]
    item["cut_detection"] = _measured([1.5])
    item["cuts"] = measured_shots([1.5], 4.0)
    assert [cut["cut_id"] for cut in item["cuts"]] == ["shot-1", "shot-2"]
    observation = review_case[2]["observations"][0]
    observation.update(cut_id="shot-2", start_seconds=1.5, end_seconds=4.0)
    assert validate(review_case)["cut_detection"]["first"]["cut_seconds"] == [1.5]
    observation.update(start_seconds=1.0)
    with pytest.raises(QualityContractError, match="observation cut"):
        validate(review_case)


def test_unmeasured_detection_keeps_cuts_unmapped(review_case):
    item = review_case[0]["items"][0]
    item["cut_detection"] = {**_measured([]), "status": "unavailable"}
    assert validate(review_case)["cut_detection"]["first"]["status"] == "unavailable"
