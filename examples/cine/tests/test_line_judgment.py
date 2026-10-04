"""Context completes visual gaps, preserves A, and retries incomplete batches."""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import line_judgment as lj
from pipeline import line_performance as lp

GATEWAY = ("http://gateway.test/v1", "test-key", "vision-model")
NOTES = {
    "locations": [{"id": "L1"}, {"id": "L2"}],
    "people": [
        {"id": "P1", "look": "戴安全帽的人", "name_caption": "安然"},
        {"id": "P2", "look": "穿背心的人", "name_caption": None},
        {"id": "P3", "look": "拿文件夹的人", "name_caption": None},
    ],
    "shots": [
        {
            "start": 0,
            "end": 2,
            "place": "L1",
            "what": "P1站在机器旁",
            "people": ["P1"],
            "crowd": False,
        },
        {
            "start": 2,
            "end": 4,
            "place": "L1",
            "what": "P2看向机器",
            "people": ["P2"],
            "crowd": False,
        },
        {
            "start": 4,
            "end": 6,
            "place": "L2",
            "what": "P3站在文件架旁",
            "people": ["P3"],
            "crowd": False,
        },
        {
            "start": 6,
            "end": 8,
            "place": "L2",
            "what": "P3走出房间",
            "people": ["P3"],
            "crowd": True,
        },
    ],
}


def with_a(row=None, *, speaker="unclear", notes=None, model=GATEWAY[2]):
    notes = NOTES if notes is None else notes
    row = {"start": 2.2, "end": 3.8, "text": "请先检查电源。"} if row is None else row
    row["performance"] = {
        "speaker": speaker,
        "emotion": "平静",
        "visible": "面部放松",
        "who": "穿背心的人",
        "at": lp.frame_times(row),
        "schema_version": 2,
        "model": model,
        "input_sha256": lp.performance_inputs(notes, row),
    }
    return row


def judgment(speaker="P2", delivery="spoken", basis="context"):
    return {
        "speaker": speaker,
        "delivery": delivery,
        "basis": basis,
        "reason": "前句称呼对应接话者",
    }


def reply(batch, **overrides):
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        [{"n": i, **judgment(), **overrides} for i, _row, _frames in batch],
                        ensure_ascii=False,
                    )
                }
            }
        ],
        "usage": {"prompt_tokens": 21, "completion_tokens": 34, "total_tokens": 55},
    }


@pytest.fixture
def frames(monkeypatch):
    taken = []

    def extract(_media, at, _target):
        taken.append(at)
        return b"jpeg"

    monkeypatch.setattr(lj, "_extract", extract)
    return taken


def test_actual_and_adjacent_presence_and_missing_person():
    row = with_a()
    direct = lj.check_constraints(judgment(), NOTES, row)
    adjacent = lj.check_constraints(judgment("P1"), NOTES, row)
    absent = lj.check_constraints(judgment("P3"), NOTES, row)
    assert direct["status"] == adjacent["status"] == "passed"
    assert direct["overlap_people"] == ["P2"]
    assert adjacent["adjacent_people"] == ["P1"]
    assert absent["needs_review"] and absent["counts_as_present"] is False


def test_offscreen_excludes_people_in_frame_shots_but_unclear_allows_context_choice():
    row = with_a(speaker="offscreen")
    result = lj.check_constraints(judgment(), NOTES, row)
    assert result["needs_review"] and result["frame_people"] == ["P2"]
    assert "A步未见说话人" in result["review_reasons"][0]
    assert lj.check_constraints(judgment("P1"), NOTES, row)["status"] == "passed"
    assert lj.check_constraints(judgment(), NOTES, with_a())["status"] == "passed"


def test_offscreen_boundary_uses_sampled_shots_not_all_overlaps():
    notes = copy.deepcopy(NOTES)
    notes["shots"][0].update(place="insert", people=[])
    row = with_a(
        {"start": 0, "end": 2.05, "text": "请等我回复。"}, speaker="offscreen", notes=notes
    )
    assert all(at < 2 for at in row["performance"]["at"])
    result = lj.check_constraints(judgment("P2"), notes, row)
    assert result["overlap_people"] == ["P2"]
    assert result["frame_people"] == [] and result["frame_shots"] == [0]
    assert result["status"] == "passed"
    row["performance"]["at"] = [2.01]
    assert lj.check_constraints(judgment("P2"), notes, row)["needs_review"]


def test_frame_at_cut_belongs_to_new_shot_and_offscreen_does_not_block_voiceover():
    row = with_a(speaker="offscreen")
    row["performance"]["at"] = [2.0]
    result = lj.check_constraints(judgment("P2", "voice_over"), NOTES, row)
    assert result["frame_shots"] == [1] and result["status"] == "passed"
    assert result["counts_as_present"] is False


@pytest.mark.parametrize(
    "change", ["location", "insert", "unknown", "missing", "teaser", "gap", "nonexistent"]
)
def test_adjacent_requires_known_same_place_immediate_ordinary_shot(change):
    notes = copy.deepcopy(NOTES)
    previous = notes["shots"][0]
    if change == "location":
        previous["place"] = "L2"
    elif change == "insert":
        previous["place"] = "insert"
    elif change == "unknown":
        previous["place"] = "?"
    elif change == "missing":
        previous.pop("place")
    elif change == "teaser":
        previous["teaser"] = True
    elif change == "gap":
        previous["end"] = 1.9
    else:
        previous["place"] = notes["shots"][1]["place"] = "L404"
    assert lj.check_constraints(judgment("P1"), notes, with_a(notes=notes))["needs_review"]


def test_unknown_overlap_can_prove_person_but_cannot_borrow_neighbor():
    notes = copy.deepcopy(NOTES)
    notes["shots"][0]["place"] = notes["shots"][1]["place"] = "?"
    row = with_a(notes=notes)
    assert lj.check_constraints(judgment(), notes, row)["status"] == "passed"
    assert lj.check_constraints(judgment("P1"), notes, row)["needs_review"]


@pytest.mark.parametrize("kind", ["insert", "teaser"])
def test_insert_or_teaser_cannot_supply_live_person_or_crowd(kind):
    notes = copy.deepcopy(NOTES)
    shot = notes["shots"][1]
    shot["crowd"] = True
    if kind == "insert":
        shot["place"] = "insert"
    else:
        shot["teaser"] = True
    row = with_a(notes=notes)
    assert lj.check_constraints(judgment(), notes, row)["needs_review"]
    assert lj.check_constraints(judgment("crowd"), notes, row)["needs_review"]


def test_cannot_skip_insert_to_find_a_distant_same_place_person():
    notes = copy.deepcopy(NOTES)
    notes["shots"].insert(
        1, {"start": 1.8, "end": 2, "place": "insert", "people": [], "crowd": False}
    )
    notes["shots"][0]["end"] = 1.8
    assert lj.check_constraints(judgment("P1"), notes, with_a(notes=notes))["needs_review"]


def test_voiceover_may_use_all_known_people_but_never_adds_presence():
    result = lj.check_constraints(judgment("P3", "voice_over"), NOTES, with_a())
    assert result["status"] == "passed" and result["counts_as_present"] is False
    assert result["overlap_people"] == ["P2"]
    assert lj.check_constraints(judgment("P99", "voice_over"), NOTES, with_a())["needs_review"]


@pytest.mark.parametrize("delivery", ["spoken", "voice_over"])
def test_crowd_requires_actual_crowd_or_matched_extra_and_no_character_presence(delivery):
    value = judgment("crowd", delivery)
    crowd_row = with_a({"start": 6.1, "end": 7.8, "text": "清单真长。"})
    assert lj.check_constraints(value, NOTES, crowd_row)["status"] == "passed"
    row = with_a()
    missing = lj.check_constraints(value, NOTES, row)
    assert missing["needs_review"] and missing["post_mapping_required"]
    mapping = {"people": [{"person_id": "P2", "status": "extra"}]}
    extra = lj.check_constraints(value, NOTES, row, mapping)
    assert extra["status"] == "passed" and extra["counts_as_present"] is False
    assert extra["extra_people"] == ["P2"]
    adjacent = with_a({"start": 4.1, "end": 5.8, "text": "清单真长。"})
    assert lj.check_constraints(value, NOTES, adjacent)["needs_review"]


@pytest.mark.parametrize("basis", ["context", "caption"])
def test_unclear_a_can_select_visible_person_from_allowed_context_basis(basis):
    assert lj.check_constraints(judgment(basis=basis), NOTES, with_a())["status"] == "passed"


@pytest.mark.parametrize(
    "value",
    [
        judgment(basis="mouth"),
        judgment(basis="none"),
        judgment("unclear"),
        judgment(delivery="unclear"),
        judgment(delivery="offscreen"),
    ],
)
def test_mouth_none_unknown_and_illegal_delivery_stay_review(value):
    result = lj.check_constraints(value, NOTES, with_a())
    assert result["needs_review"] and result["counts_as_present"] is False


@pytest.mark.parametrize("field", ["speaker", "delivery", "basis"])
def test_illegal_json_types_are_reviewed(field):
    assert lj.check_constraints({**judgment(), field: ["bad"]}, NOTES, with_a())["needs_review"]


def test_missing_sentence_timing_remains_review():
    row = with_a()
    row["sentence_boundary_review"] = True
    assert lj.check_constraints(judgment(), NOTES, row)["needs_review"]


def test_b_input_contains_a_conclusion_and_bounded_context_without_old_emotions_or_roles():
    rows = [
        with_a(
            {
                "id": f"seg{i}",
                "start": i / 2,
                "end": i / 2 + 0.4,
                "text": f"工序{i}",
                "speaker": "C99",
                "voice": {"class": "low"},
                "judgment": {"speaker": "P3"},
            }
        )
        for i in range(15)
    ]
    rows[6]["performance"].update(emotion="独有旧情绪", visible="独有旧描述")
    notes = copy.deepcopy(NOTES)
    notes["people"][0]["character_id"] = "C88"
    messages = lj.build_request(
        [(6, rows[6], [b"jpeg"]), (7, rows[7], [b"jpeg", b"jpeg"])], notes, rows
    )
    parts = messages[0]["content"]
    assert json.loads(parts[1]["text"])["people"] == NOTES["people"]
    first = json.loads(parts[2]["text"])
    assert first["n"] == 6 and first["a_step"]["speaker"] == "unclear"
    assert [r["source_index"] for r in first["context_lines"]] == [3, 4, 5, 7, 8, 9]
    assert first["a_step"]["frame_people"] == ["P2"]
    assert [s["relation"] for s in first["shots"]] == ["before", "overlap", "after"]
    serialized = json.dumps(messages, ensure_ascii=False)
    assert all(
        word not in serialized
        for word in (
            "C99",
            "C88",
            "独有旧情绪",
            "独有旧描述",
            '"voice"',
            '"judgment"',
            '"performance"',
        )
    )
    assert sum(p["type"] == "image_url" for p in parts) == 3


def test_b_only_handles_current_a_gaps_and_preserves_direct_visual_reading(monkeypatch, frames):
    rows = [with_a(speaker="P2"), with_a(speaker="offscreen"), with_a(), with_a(), with_a()]
    rows[3]["teaser"] = True
    rows[4]["performance"]["input_sha256"] = {}
    rows.append({"start": 2.2, "end": 3.8, "text": "尚未读画面。"})
    asked = []

    def ask(batch, *_args):
        asked.extend(i for i, *_rest in batch)
        return reply(batch, speaker="P1", basis="context")

    monkeypatch.setattr(lj, "_ask", ask)
    original = copy.deepcopy(rows)
    result = lj.read_line_judgments("source.mp4", rows, shot_notes=NOTES, gateway=GATEWAY)
    assert asked == [1, 2] and result["lines"] == result["read"] == 2
    assert len(frames) == 4 and rows[0] == original[0]
    assert "judgment" not in rows[3] and "judgment" not in rows[4] and "judgment" not in rows[5]
    for row, before in zip(rows, original, strict=True):
        assert {k: v for k, v in row.items() if k != "judgment"} == before


def test_no_emotion_fields_or_confidence_but_raw_reply_and_a_stay_unchanged(monkeypatch, frames):
    rows = [with_a()]
    a = copy.deepcopy(rows[0]["performance"])
    raw = reply(
        [(0, rows[0], [])],
        confidence=0.001,
        emotion="伪造情绪",
        visible="伪造嘴型",
        who="伪造人物",
    )
    monkeypatch.setattr(lj, "_ask", lambda *_args: raw)
    result = lj.read_line_judgments("source.mp4", rows, shot_notes=NOTES, gateway=GATEWAY)
    value = result["results"][0]
    assert rows[0]["performance"] == a
    assert all(k not in value for k in ("emotion", "visible", "who", "confidence"))
    assert value["needs_review"] is False
    attempt = result["batches"][0]["attempts"][0]
    assert attempt["reply"] == raw and attempt["usage"]["total_tokens"] == 55
    assert result["model_calls"] == 1 and len(frames) == 2


@pytest.mark.usefixtures("frames")
@pytest.mark.parametrize(
    "overrides",
    [
        {"speaker": "C1"},
        {"speaker": "P99"},
        {"speaker": "extra"},
        {"delivery": "offscreen"},
        {"delivery": "unclear"},
        {"basis": "mouth"},
        {"basis": "guess"},
        {"reason": "长" * 31},
        {"reason": ""},
    ],
)
def test_semantic_illegal_values_are_reviewed_once_and_raw_preserved(monkeypatch, overrides):
    rows = [with_a()]
    raw = reply([(0, rows[0], [])], **overrides)
    monkeypatch.setattr(lj, "_ask", lambda *_args: raw)
    result = lj.read_line_judgments("source.mp4", rows, shot_notes=NOTES, gateway=GATEWAY)
    value = result["results"][0]
    assert value["needs_review"] and value["review_reasons"]
    assert value["speaker"] in {"P1", "P2", "P3", "crowd", "unclear"}
    assert value["delivery"] in lj.DELIVERIES | {"unclear"} and value["basis"] in lj.BASES
    assert len(value["reason"]) <= 30 and result["model_calls"] == 1
    assert result["batches"][0]["attempts"][0]["reply"] == raw
    assert value["receipt_sha256"] == result["batches"][0]["receipt_sha256"]
    assert lj.judgment_current(NOTES, rows, 0, GATEWAY[2])


@pytest.mark.usefixtures("frames")
@pytest.mark.parametrize(
    "first", ["bad_json", "missing", "duplicate", "wrong_index", "wrong_shape"]
)
def test_first_structural_failure_retries_whole_batch_once_then_succeeds(monkeypatch, first):
    rows = [with_a(), with_a()]
    asked = []

    def ask(batch, *_args):
        asked.append([i for i, *_rest in batch])
        if len(asked) == 1:
            raw = reply(batch)
            if first == "bad_json":
                raw["choices"][0]["message"]["content"] = "bad"
            elif first == "missing":
                return reply(batch[:1])
            elif first == "duplicate":
                return reply([batch[0], batch[0]])
            elif first == "wrong_index":
                return reply([(99, rows[0], [])])
            else:
                raw["choices"][0]["message"]["content"] = "{}"
            return raw
        return reply(batch)

    monkeypatch.setattr(lj, "_ask", ask)
    result = lj.read_line_judgments("source.mp4", rows, shot_notes=NOTES, gateway=GATEWAY)
    assert asked == [[0, 1], [0, 1]] and result["read"] == 2
    assert result["status"] == "read" and result["model_calls"] == 2
    attempts = result["batches"][0]["attempts"]
    assert len(attempts) == 2 and attempts[0]["error"] and "error" not in attempts[1]


def test_two_structural_failures_leave_entire_batch_unread_then_retry_with_cached_frames(
    monkeypatch, tmp_path, frames
):
    media = tmp_path / "source.mp4"
    media.write_bytes(b"video")
    rows = [with_a(), with_a()]
    cache = tmp_path / "frames"
    monkeypatch.setattr(lj, "_ask", lambda batch, *_args: reply(batch[:1]))
    first = lj.read_line_judgments(
        media, rows, shot_notes=NOTES, gateway=GATEWAY, frame_cache=cache
    )
    assert first["status"] == "unavailable" and first["read"] == 0
    assert first["batches"][0]["status"] == "unread" and first["model_calls"] == 2
    assert all("judgment" not in row for row in rows)
    assert all(row["read"] is False and row["needs_review"] for row in first["results"])
    assert len(frames) == 2
    monkeypatch.setattr(lj, "_ask", lambda batch, *_args: reply(batch))
    second = lj.read_line_judgments(
        media, rows, shot_notes=NOTES, gateway=GATEWAY, frame_cache=cache
    )
    assert second["status"] == "read" and second["read"] == 2 and len(frames) == 2


@pytest.mark.usefixtures("frames")
def test_a_failed_batch_does_not_discard_another_successful_batch(monkeypatch):
    rows = [with_a({"start": i, "end": i + 0.5, "text": "下一项。"}) for i in range(12)]

    def ask(batch, *_args):
        return reply(batch[:1] if batch[0][0] == 0 else batch, delivery="voice_over")

    monkeypatch.setattr(lj, "_ask", ask)
    first = lj.read_line_judgments("source.mp4", rows, shot_notes=NOTES, gateway=GATEWAY)
    assert first["status"] == "partial" and first["read"] == 4 and first["model_calls"] == 3
    assert all("judgment" not in row for row in rows[:8])
    requested = []

    def finish(batch, *_args):
        requested.extend(i for i, *_rest in batch)
        return reply(batch, delivery="voice_over")

    monkeypatch.setattr(lj, "_ask", finish)
    second = lj.read_line_judgments("source.mp4", rows, shot_notes=NOTES, gateway=GATEWAY)
    assert second["read"] == 12 and second["reused"] == 4 and requested == list(range(8))


def test_unchanged_context_reuses_without_frames_or_model(monkeypatch, frames):
    rows = [with_a()]
    monkeypatch.setattr(lj, "_ask", lambda batch, *_args: reply(batch))
    lj.read_line_judgments("source.mp4", rows, shot_notes=NOTES, gateway=GATEWAY)
    old_receipt = rows[0]["judgment"]["receipt_sha256"]
    monkeypatch.setattr(lj, "_ask", lambda *_args: pytest.fail("B should be reused"))
    monkeypatch.setattr(lj, "_extract", lambda *_args: pytest.fail("frames should be reused"))
    second = lj.read_line_judgments("source.mp4", rows, shot_notes=NOTES, gateway=GATEWAY)
    assert second["requested"] == second["model_calls"] == 0
    assert second["reused"] == 1 and second["batches"] == [] and len(frames) == 2
    assert rows[0]["judgment"]["receipt_sha256"] == old_receipt


@pytest.mark.usefixtures("frames")
def test_extra_mapping_can_change_constraint_without_rerequest(monkeypatch):
    rows = [with_a()]
    monkeypatch.setattr(lj, "_ask", lambda batch, *_args: reply(batch, speaker="crowd"))
    first = lj.read_line_judgments("source.mp4", rows, shot_notes=NOTES, gateway=GATEWAY)
    assert first["results"][0]["needs_review"]
    monkeypatch.setattr(
        lj, "_ask", lambda *_args: pytest.fail("mapping only rechecks constraints")
    )
    mapping = {"people": [{"person_id": "P2", "status": "extra"}]}
    second = lj.read_line_judgments(
        "source.mp4", rows, shot_notes=NOTES, gateway=GATEWAY, person_map=mapping
    )
    assert second["model_calls"] == 0 and second["results"][0]["needs_review"] is False


@pytest.mark.usefixtures("frames")
@pytest.mark.parametrize("change", ["context", "shot", "people", "a", "prompt", "model"])
def test_cache_key_tracks_a_context_shots_people_prompt_and_model(monkeypatch, change):
    notes = copy.deepcopy(NOTES)
    rows = [with_a(notes=notes), with_a(notes=notes)]
    monkeypatch.setattr(lj, "_ask", lambda batch, *_args: reply(batch))
    lj.read_line_judgments("source.mp4", rows, shot_notes=notes, gateway=GATEWAY)
    assert lj.judgment_current(notes, rows, 0, GATEWAY[2])
    model = GATEWAY[2]
    if change == "context":
        rows[1]["text"] = "请检查下一项。"
    elif change == "shot":
        notes["shots"][0]["what"] = "P1放下工具"
    elif change == "people":
        notes["people"][2]["look"] += "，戴手套"
    elif change == "a":
        rows[0]["performance"]["speaker"] = "offscreen"
    elif change == "prompt":
        monkeypatch.setattr(lj, "PROMPT", lj.PROMPT + "额外规则")
    else:
        model = "different-model"
    assert not lj.judgment_current(notes, rows, 0, model)


@pytest.mark.usefixtures("frames")
def test_context_edit_rereads_only_rows_whose_three_row_context_changed(monkeypatch):
    rows = [with_a({"start": i, "end": i + 0.5, "text": f"工序{i}"}) for i in range(18)]
    monkeypatch.setattr(lj, "_ask", lambda batch, *_args: reply(batch, delivery="voice_over"))
    lj.read_line_judgments("source.mp4", rows, shot_notes=NOTES, gateway=GATEWAY)
    rows[8]["text"] = "工序八修订"
    with_a(rows[8])
    requested = []

    def ask(batch, *_args):
        requested.extend(i for i, *_rest in batch)
        return reply(batch, delivery="voice_over")

    monkeypatch.setattr(lj, "_ask", ask)
    second = lj.read_line_judgments("source.mp4", rows, shot_notes=NOTES, gateway=GATEWAY)
    assert requested == list(range(5, 12)) and second["reused"] == 11


@pytest.mark.usefixtures("frames")
def test_a_emotion_edit_and_distant_shot_description_do_not_invalidate_b(monkeypatch):
    notes = copy.deepcopy(NOTES)
    rows = [with_a(notes=notes)]
    monkeypatch.setattr(lj, "_ask", lambda batch, *_args: reply(batch))
    lj.read_line_judgments("source.mp4", rows, shot_notes=notes, gateway=GATEWAY)
    rows[0]["performance"]["emotion"] = "喜悦"
    notes["shots"][3]["what"] = "无关远处镜头描述更新"
    assert lj.judgment_current(notes, rows, 0, GATEWAY[2])


def test_frame_failure_leaves_unread_without_model(monkeypatch):
    monkeypatch.setattr(lj, "_extract", lambda *_args: (_ for _ in ()).throw(OSError("no frame")))
    monkeypatch.setattr(lj, "_ask", lambda *_args: pytest.fail("no frames"))
    rows = [with_a()]
    result = lj.read_line_judgments("source.mp4", rows, shot_notes=NOTES, gateway=GATEWAY)
    assert result["read"] == result["model_calls"] == 0 and "judgment" not in rows[0]
    assert result["results"][0]["needs_review"]


def test_actual_http_request_and_shared_sampling_settings(monkeypatch):
    rows = [with_a()]
    batch = [(0, rows[0], [b"jpeg"])]
    raw = reply(batch)
    sent = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(raw).encode("utf-8")

    def open_url(request, timeout):
        sent.append((request, timeout))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", open_url)
    assert lj._ask(batch, GATEWAY, NOTES, rows) == raw
    request, timeout = sent[0]
    body = json.loads(request.data)
    assert request.full_url == "http://gateway.test/v1/chat/completions"
    assert body["model"] == GATEWAY[2] and timeout == lp.TIMEOUT
    assert body["messages"] == lj.build_request(batch, NOTES, rows)
    assert lj.frame_times is lp.frame_times and lj.BATCH_ROWS == 8
    assert (lj.FRAME_WIDTH, lj.FRAME_QUALITY) == (lp.FRAME_WIDTH, lp.FRAME_QUALITY)


@pytest.mark.usefixtures("frames")
@pytest.mark.parametrize("malformed", ['{"choices":', "[]", "null"])
@pytest.mark.parametrize("failures", [1, 2])
def test_malformed_http_envelope_preserves_raw_and_retries_or_leaves_unread(
    monkeypatch, malformed, failures
):
    rows = [with_a()]
    good = json.dumps(reply([(0, rows[0], [])]), ensure_ascii=False)
    responses = iter([*[malformed] * failures, good])

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return next(responses).encode("utf-8")

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: Response())
    first = lj.read_line_judgments("source.mp4", rows, shot_notes=NOTES, gateway=GATEWAY)
    attempts = first["batches"][0]["attempts"]
    assert len(attempts) == 2
    assert all(a["raw_response"] == malformed and a["error"] for a in attempts[:failures])
    if failures == 1:
        assert first["status"] == "read" and first["read"] == 1
        assert attempts[1]["raw_response"] == good and "error" not in attempts[1]
    else:
        assert first["status"] == "unavailable" and first["read"] == 0
        assert "judgment" not in rows[0]
        second = lj.read_line_judgments("source.mp4", rows, shot_notes=NOTES, gateway=GATEWAY)
        assert second["status"] == "read" and second["read"] == 1
        assert second["batches"][0]["attempts"][0]["raw_response"] == good


def _arrival_notes(second_start=3.0):
    """A voice over P1's shot, a reaction shot, then the speaker walks in."""
    return {
        "locations": [{"id": "L1"}, {"id": "L2"}],
        "people": [
            {"id": f"P{i}", "look": f"人物{i}", "name_caption": None} for i in (1, 2, 3, 4)
        ],
        "shots": [
            {
                "start": 0,
                "end": 2,
                "place": "L1",
                "what": "P1低头",
                "people": ["P1"],
                "crowd": False,
            },
            {
                "start": 2,
                "end": second_start,
                "place": "L1",
                "what": "P2转头",
                "people": ["P2"],
                "crowd": False,
            },
            {
                "start": second_start,
                "end": second_start + 3,
                "place": "L1",
                "what": "P3走进门",
                "people": ["P3"],
                "crowd": False,
            },
            {
                "start": second_start + 3,
                "end": second_start + 6,
                "place": "L1",
                "what": "P4坐下",
                "people": ["P4"],
                "crowd": False,
            },
        ],
    }


def test_a_voice_heard_before_its_speaker_walks_in_may_belong_to_who_enters():
    notes = _arrival_notes()
    row = with_a(
        {"start": 0.5, "end": 1.8, "text": "我来晚了！"}, speaker="offscreen", notes=notes
    )
    entering = lj.check_constraints(judgment("P3"), notes, row)
    assert not entering["needs_review"] and entering["ahead_people"] == ["P2", "P3"]
    # Three shots later is no longer the voice's own entrance.
    assert lj.check_constraints(judgment("P4"), notes, row)["needs_review"]
    context = lj._line_state(notes, [row], 0)["shots"]
    assert [shot["index"] for shot in context if shot["relation"] == "after"] == [1, 2]


def test_an_entrance_long_after_the_line_or_in_another_place_does_not_count():
    late = _arrival_notes(second_start=9.0)
    row = with_a({"start": 0.5, "end": 1.8, "text": "我来晚了！"}, speaker="offscreen", notes=late)
    assert lj.check_constraints(judgment("P3"), late, row)["needs_review"]
    moved = _arrival_notes()
    moved["shots"][1]["place"] = "L2"
    row = with_a(
        {"start": 0.5, "end": 1.8, "text": "我来晚了！"}, speaker="offscreen", notes=moved
    )
    assert lj.check_constraints(judgment("P3"), moved, row)["needs_review"]
