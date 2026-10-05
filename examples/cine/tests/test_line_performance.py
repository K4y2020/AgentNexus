"""Reading each spoken line's performance from frames taken while it is said."""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import line_performance as lp
from pipeline.transcribe import render_plain

GATEWAY = ("http://gateway.test/v1", "key", "vision-model")


def _rows():
    return [
        {"start": 0.0, "end": 3.0, "text": "娃记住了吧，雾天别下水！"},
        {"start": 3.0, "end": 3.4, "text": "娘"},
        {"start": 4.0, "end": 5.0, "text": "娘子，船票买到了！", "teaser": True},
        {"start": 6.0, "end": 7.0, "text": "  "},
    ]


@pytest.fixture
def frames(monkeypatch):
    taken = []

    def fake_extract(_media, seconds, _target):
        taken.append(seconds)
        return b"jpeg"

    monkeypatch.setattr(lp, "_extract", fake_extract)
    return taken


def _replying(monkeypatch, reply):
    asked = []

    def fake_ask(batch, _gateway):
        asked.append([row["text"] for _n, row, _f in batch])
        return reply(batch)

    monkeypatch.setattr(lp, "_ask", fake_ask)
    return asked


def test_a_line_gets_two_frames_and_a_short_one_gets_its_middle():
    assert lp.frame_times({"start": 1.0, "end": 3.0}) == [1.6, 2.5]
    assert lp.frame_times({"start": 3.0, "end": 3.4}) == [3.2]


def test_only_spoken_lines_of_the_film_proper_are_read(monkeypatch, frames):
    asked = _replying(
        monkeypatch,
        lambda batch: {
            n: {"emotion": "哭诉", "visible": "满脸泪水", "who": "妇人"} for n, *_ in batch
        },
    )
    rows = _rows()

    summary = lp.read_line_performance("film.mp4", rows, gateway=GATEWAY)

    assert asked == [["娃记住了吧，雾天别下水！", "娘"]]
    assert frames == [0.9, 2.25, 3.2]
    assert summary["status"] == "read" and summary["read"] == 2 and summary["lines"] == 2
    assert rows[0]["performance"] == {
        "emotion": "哭诉",
        "visible": "满脸泪水",
        "who": "妇人",
        "at": [0.9, 2.25],
    }
    assert "performance" not in rows[2] and "performance" not in rows[3]


@pytest.mark.usefixtures("frames")
def test_a_partial_reading_is_finished_later_without_rereading(monkeypatch):
    monkeypatch.setattr(lp, "BATCH_ROWS", 1)
    rows = _rows()
    _replying(monkeypatch, lambda _batch: {1: {"emotion": "哭诉", "visible": "", "who": ""}})
    assert lp.read_line_performance("film.mp4", rows, gateway=GATEWAY)["status"] == "partial"

    asked = _replying(
        monkeypatch, lambda _batch: {1: {"emotion": "平静", "visible": "", "who": ""}}
    )
    summary = lp.read_line_performance("film.mp4", rows, gateway=GATEWAY)

    assert asked == [["娘"]]
    assert summary["status"] == "read"
    assert rows[0]["performance"]["emotion"] == "哭诉"
    assert rows[1]["performance"]["emotion"] == "平静"


@pytest.mark.usefixtures("frames")
def test_a_failed_request_leaves_its_lines_unread_and_says_why(monkeypatch):
    def fail(_batch):
        raise OSError("gateway unreachable")

    _replying(monkeypatch, fail)
    rows = _rows()

    summary = lp.read_line_performance("film.mp4", rows, gateway=GATEWAY)

    assert summary["status"] == "unavailable"
    assert "gateway unreachable" in summary["errors"][0]
    assert not any(row.get("performance") for row in rows)


def test_no_configured_model_reads_nothing():
    rows = _rows()
    summary = lp.read_line_performance("film.mp4", rows, gateway=(None, None, None))
    assert summary["status"] == "unavailable"
    assert not any(row.get("performance") for row in rows)


def test_the_reply_is_kept_only_for_the_lines_asked(monkeypatch):
    class Response:
        def __init__(self, content):
            self.body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    content = (
        "```json\n"
        + json.dumps(
            [
                {"n": 1, "emotion": "委屈痛哭", "visible": "满脸泪水", "who": "妇人"},
                {"n": 2, "emotion": "看不清", "visible": "黑场", "who": "无"},
                {"n": 7, "emotion": "大笑"},
                {"n": 3, "emotion": ""},
            ],
            ensure_ascii=False,
        )
        + "\n```"
    )
    sent = []

    def fake_urlopen(request, **_kwargs):
        sent.append(json.loads(request.data))
        return Response(content)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    batch = [(n, {"start": 0.0, "end": 1.0, "text": f"第{n}句"}, [b"jpeg"]) for n in (1, 2, 3)]

    found = lp._ask(batch, GATEWAY)

    assert found == {
        1: {"emotion": "委屈痛哭", "visible": "满脸泪水", "who": "妇人"},
        2: {"emotion": "看不清", "visible": "", "who": ""},
    }
    parts = sent[0]["messages"][0]["content"]
    assert sum(1 for p in parts if p["type"] == "image_url") == 3
    assert parts[0]["text"].startswith("下面是一段影片")


def test_the_text_transcript_carries_what_the_frames_show():
    rows = [
        {
            "start": 0.0,
            "end": 3.0,
            "text": "雾天别下水！",
            "speaker": "秀娥",
            "performance": {"emotion": "委屈痛哭", "visible": "满脸泪水", "who": "包头巾的妇人"},
        },
        {"start": 3.0, "end": 4.0, "text": "来来来！", "performance": {"emotion": "看不清"}},
        {"start": 4.0, "end": 5.0, "text": "娘"},
    ]

    text = render_plain(rows)

    assert "[秀娥] 雾天别下水！  〔画面：包头巾的妇人，委屈痛哭：满脸泪水〕" in text
    assert text.count("〔画面") == 1
    assert lp.describe(rows[0]["performance"], who=False) == "委屈痛哭：满脸泪水"


PEOPLE_NOTES = {
    "schema_version": 2,
    "people": [
        {"id": "P1", "look": "短发男子", "name_caption": None},
        {"id": "P2", "look": "长发女子", "name_caption": None},
    ],
    "shots": [
        {"start": 0, "end": 2, "people": ["P1"]},
        {"start": 2, "end": 4, "people": ["P2"]},
    ],
}


def test_candidates_are_only_people_visible_during_the_line():
    assert [p["id"] for p in lp.people_for_line(PEOPLE_NOTES, {"start": 1, "end": 2})] == ["P1"]
    assert [p["id"] for p in lp.people_for_line(PEOPLE_NOTES, {"start": 1, "end": 3})] == [
        "P1",
        "P2",
    ]
    assert lp.people_for_line(PEOPLE_NOTES, {"start": 4, "end": 5}) == []


@pytest.mark.usefixtures("frames")
def test_invalid_speaker_stays_unread_and_only_that_line_is_retried(monkeypatch):
    rows = [{"start": 0, "end": 1, "text": "请坐"}, {"start": 2, "end": 3, "text": "好的"}]
    _replying(monkeypatch, lambda b: {n: {"emotion": "平静", "speaker": "P1"} for n, *_ in b})
    first = lp.read_line_performance("film.mp4", rows, gateway=GATEWAY, shot_notes=PEOPLE_NOTES)
    assert first["status"] == "partial" and first["read"] == 1
    assert "performance" not in rows[1]
    seen = _replying(monkeypatch, lambda _b: {1: {"emotion": "惊喜", "speaker": "P2"}})
    second = lp.read_line_performance("film.mp4", rows, gateway=GATEWAY, shot_notes=PEOPLE_NOTES)
    assert seen == [["好的"]] and second["status"] == "read"
    assert rows[0]["performance"]["emotion"] == "平静"


@pytest.mark.parametrize(
    "change", ["model", "text", "timing", "people", "quality", "missing_model"]
)
@pytest.mark.usefixtures("frames")
def test_visual_speaker_cache_tracks_model_line_and_people(monkeypatch, change):
    rows = [{"start": 0, "end": 1, "text": "请坐"}]
    notes = copy.deepcopy(PEOPLE_NOTES)
    calls = _replying(
        monkeypatch, lambda b: {n: {"emotion": "平静", "speaker": "P1"} for n, *_ in b}
    )
    lp.read_line_performance("film.mp4", rows, gateway=GATEWAY, shot_notes=notes)
    lp.read_line_performance("film.mp4", rows, gateway=GATEWAY, shot_notes=notes)
    assert len(calls) == 1
    gateway = GATEWAY
    if change == "model":
        gateway = (*GATEWAY[:2], "other")
    elif change == "text":
        rows[0]["text"] = "请等一等"
    elif change == "timing":
        rows[0]["end"] = 1.1
    elif change == "people":
        notes["people"][0]["look"] = "短发男子，戴帽子"
    elif change == "quality":
        monkeypatch.setattr(lp, "FRAME_QUALITY", 4)
    else:
        gateway = (None, None, None)
    summary = lp.read_line_performance("film.mp4", rows, gateway=gateway, shot_notes=notes)
    if change == "missing_model":
        assert summary["status"] == "unavailable" and "performance" not in rows[0]
    else:
        assert len(calls) == 2 and summary["status"] == "read"


@pytest.mark.usefixtures("frames")
def test_speaker_reading_keeps_eight_line_batches(monkeypatch):
    sizes = []

    def ask(batch, _gateway):
        sizes.append(len(batch))
        return {n: {"emotion": "平静", "speaker": "offscreen"} for n, *_ in batch}

    monkeypatch.setattr(lp, "_ask", ask)
    rows = [{"start": i, "end": i + 0.5, "text": "请稍候"} for i in range(19)]
    assert (
        lp.read_line_performance("film.mp4", rows, gateway=GATEWAY, shot_notes=PEOPLE_NOTES)[
            "read"
        ]
        == 19
    )
    assert sorted(sizes) == [3, 8, 8]


@pytest.mark.usefixtures("frames")
def test_local_changes_only_reread_affected_visual_candidates(monkeypatch):
    notes = copy.deepcopy(PEOPLE_NOTES)
    rows = [{"start": 0, "end": 1, "text": "请坐"}, {"start": 2, "end": 3, "text": "好的"}]
    calls = _replying(
        monkeypatch,
        lambda batch: {
            n: {"emotion": "平静", "speaker": row["_speaker_candidates"][0]["id"]}
            for n, row, _f in batch
        },
    )
    lp.read_line_performance("film.mp4", rows, gateway=GATEWAY, shot_notes=notes)
    notes["shots"][1]["what"] = "女子坐下"
    notes["shots"][1]["place"] = "L2"
    notes["attempts"] = [{"seconds": 99}]
    lp.read_line_performance("film.mp4", rows, gateway=GATEWAY, shot_notes=notes)
    assert calls == [["请坐", "好的"]]
    notes["people"][1]["look"] += "，戴帽子"
    lp.read_line_performance("film.mp4", rows, gateway=GATEWAY, shot_notes=notes)
    assert calls[-1] == ["好的"] and len(calls) == 2
    notes["shots"][0]["people"] = ["P2"]
    lp.read_line_performance("film.mp4", rows, gateway=GATEWAY, shot_notes=notes)
    assert calls[-1] == ["请坐"] and len(calls) == 3


def test_current_legacy_visual_reading_migrates_without_model_or_frames(monkeypatch):
    import hashlib

    notes = copy.deepcopy(PEOPLE_NOTES)
    rows = [{"start": 0, "end": 1, "text": "请坐"}]
    legacy_table = {
        k: notes.get(k) for k in ("schema_version", "source_id", "revision_id", "people", "shots")
    }
    legacy_hash = hashlib.sha256(
        json.dumps(legacy_table, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    rows[0]["performance"] = {
        "schema_version": 2,
        "model": GATEWAY[2],
        "speaker": "P1",
        "emotion": "平静",
        "input_sha256": {
            **{k: v for k, v in lp.performance_inputs(notes, rows[0]).items() if k != "sampling"},
            "shot_notes": legacy_hash,
        },
    }
    monkeypatch.setattr(lp, "_ask", lambda *_a: pytest.fail("legacy reading must be reused"))
    monkeypatch.setattr(lp, "_extract", lambda *_a: pytest.fail("legacy frames must be reused"))
    assert (
        lp.read_line_performance("film.mp4", rows, gateway=GATEWAY, shot_notes=notes)["read"] == 1
    )
    notes["shots"][1]["what"] = "无关镜头的新描述"
    assert lp.performance_current(notes, rows[0], GATEWAY[2])


def test_failed_visual_request_reuses_its_extracted_frames(monkeypatch, tmp_path, frames):
    media = tmp_path / "film.mp4"
    media.write_bytes(b"video")
    rows = [{"start": 0, "end": 1, "text": "请坐"}]
    cache = tmp_path / "frames"
    _replying(monkeypatch, lambda _b: (_ for _ in ()).throw(OSError("retry later")))
    assert (
        lp.read_line_performance(
            media, rows, gateway=GATEWAY, shot_notes=PEOPLE_NOTES, frame_cache=cache
        )["status"]
        == "unavailable"
    )
    assert len(frames) == 2
    _replying(monkeypatch, lambda _b: {1: {"emotion": "平静", "speaker": "P1"}})
    assert (
        lp.read_line_performance(
            media, rows, gateway=GATEWAY, shot_notes=PEOPLE_NOTES, frame_cache=cache
        )["status"]
        == "read"
    )
    assert len(frames) == 2


def _raw_replies(monkeypatch, contents):
    sent = []
    replies = iter(contents)

    class Response:
        def __init__(self, content):
            self.body = json.dumps(
                {
                    "choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
                }
            ).encode()

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def urlopen(request, **_kwargs):
        sent.append(json.loads(request.data))
        return Response(next(replies))

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return sent


def _complete_reply(numbers):
    return json.dumps(
        [{"n": n, "emotion": "平静", "visible": "望向工作台", "who": "短发男子"} for n in numbers],
        ensure_ascii=False,
    )


@pytest.mark.parametrize("failure", ["bad_json", "missing", "duplicate"])
@pytest.mark.usefixtures("frames")
def test_invalid_batch_retries_once_and_keeps_both_raw_receipts(monkeypatch, failure):
    first = {
        "bad_json": "this is not JSON",
        "missing": _complete_reply([1]),
        "duplicate": _complete_reply([1, 1, 2]),
    }[failure]
    sent = _raw_replies(monkeypatch, [first, _complete_reply([1, 2])])
    rows = [
        {"start": 0, "end": 1, "text": "把工具箱放在这里。"},
        {"start": 1, "end": 2, "text": "请接着检查仪表。"},
    ]

    summary = lp.read_line_performance("film.mp4", rows, gateway=GATEWAY)

    assert summary["status"] == "read" and summary["read"] == 2
    assert sent[0] == sent[1]
    assert summary["model_calls"] == 2
    assert summary["usage"] == {
        "prompt_tokens": 20,
        "completion_tokens": 6,
        "total_tokens": 26,
    }
    attempts = summary["batches"][0]["attempts"]
    assert len(attempts) == 2 and "error" in attempts[0] and "error" not in attempts[1]
    assert attempts[0]["reply"]["choices"][0]["message"]["content"] == first
    assert all(attempt["seconds"] >= 0 and attempt["raw_response"] for attempt in attempts)
    assert summary["batches"][0]["status"] == "read"


@pytest.mark.usefixtures("frames")
def test_two_invalid_batches_stay_unread_then_only_that_batch_is_completed(monkeypatch):
    monkeypatch.setattr(lp, "WORKERS", 1)
    sent = _raw_replies(
        monkeypatch,
        [_complete_reply([1]), _complete_reply([1, 1]), _complete_reply([9])],
    )
    rows = [{"start": n, "end": n + 0.5, "text": f"请检查第{n + 1}个仪表。"} for n in range(9)]
    summary = lp.read_line_performance("film.mp4", rows, gateway=GATEWAY)
    assert summary["status"] == "partial" and summary["read"] == 1
    assert summary["model_calls"] == 3 and len(sent) == 3
    assert not any(row.get("performance") for row in rows[:8])
    assert rows[8]["performance"]["emotion"] == "平静"
    saved = copy.deepcopy(rows[8]["performance"])
    failed = summary["batches"][0]
    assert failed["status"] == "unread" and len(failed["attempts"]) == 2
    assert all(attempt["reply"] for attempt in failed["attempts"])

    later = _raw_replies(monkeypatch, [_complete_reply(range(1, 9))])
    completed = lp.read_line_performance("film.mp4", rows, gateway=GATEWAY)
    assert completed["status"] == "read" and completed["read"] == 9
    assert completed["model_calls"] == 1 and len(later) == 1
    assert completed["batches"][0]["source_indices"] == list(range(8))
    assert rows[8]["performance"] == saved
