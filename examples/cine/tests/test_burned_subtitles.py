"""Captions burned into the frames are source dialogue that ASR can miss or misspell."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import burned_subtitles as burned
from pipeline import transcribe


def caption(start, end, text, hits=3, **extra):
    return {"start": start, "end": end, "text": text, "hits": hits, **extra}


def asr(start, end, text):
    return {"start": start, "end": end, "text": text}


def test_a_caption_asr_never_heard_becomes_a_row_without_a_speaker():
    rows, counts = burned.merge_into_transcript(
        [asr(0.0, 3.0, "雾太大，今天不开船")], [caption(93.3, 95.7, "这趟船的钱不好赚")]
    )
    assert counts == {"added": 1, "corrected": 0, "worded": 0}
    assert [r["text"] for r in rows] == ["雾太大，今天不开船", "这趟船的钱不好赚"]
    assert rows[1]["source"] == burned.SOURCE_TAG
    assert "speaker" not in rows[1]


def test_a_caption_asr_already_holds_adds_nothing():
    heard = [asr(20.0, 28.0, "我叫阿青，是船家的女儿。我爹刚刚走了，家里没人撑船")]
    rows, counts = burned.merge_into_transcript(
        heard,
        [caption(21.3, 23.0, "我叫阿青"), caption(24.1, 25.3, "我爹刚刚走了")],
    )
    assert counts == {"added": 0, "corrected": 0, "worded": 0}
    assert rows == heard


def test_a_misheard_row_takes_the_caption_wording_and_keeps_what_was_heard():
    rows, counts = burned.merge_into_transcript(
        [asr(98.4, 99.4, "船这么晃的吗？")], [caption(98.4, 99.4, "这个船这么摇的吗")]
    )
    assert counts["corrected"] == 1
    assert rows[0]["text"] == "这个船这么摇的吗"
    assert rows[0]["asr_text"] == "船这么晃的吗？"
    assert rows[0]["source"] == burned.SOURCE_TAG


def test_a_homophone_inside_a_heard_row_takes_the_caption_spelling():
    heard = [asr(102.5, 106.3, "完了，这个码头我听说过，鬼中之鬼！对不起，对不起！")]
    rows, counts = burned.merge_into_transcript(heard, [caption(104.8, 105.9, "诡中之诡")])
    assert counts == {"added": 0, "corrected": 0, "worded": 1}
    assert rows[0]["text"] == "完了，这个码头我听说过，诡中之诡！对不起，对不起！"
    assert rows[0]["asr_text"] == heard[0]["text"]
    assert rows[0]["wording"] == burned.SOURCE_TAG
    assert heard[0]["text"].startswith("完了") and "鬼中之鬼" in heard[0]["text"]


def test_a_variant_caption_straddling_two_heard_rows_respells_the_row_that_holds_it():
    heard = [
        asr(102.5, 104.8, "完了，这个码头我听说过，鬼中之鬼。"),
        asr(105.0, 106.3, "好好好好好！"),
    ]
    rows, counts = burned.merge_into_transcript(heard, [caption(104.8, 105.9, "诡中之诡")])
    assert counts == {"added": 0, "corrected": 0, "worded": 1}
    assert [r["text"] for r in rows] == ["完了，这个码头我听说过，诡中之诡。", "好好好好好！"]


def test_a_dropped_character_is_put_back_and_words_only_asr_heard_stay():
    heard = [asr(28.0, 32.0, "我的好表妹，你怎么一个人来了？这个。")]
    rows, counts = burned.merge_into_transcript(heard, [caption(28.0, 29.5, "我的好小表妹")])
    assert counts["worded"] == 1
    assert rows[0]["text"] == "我的好小表妹，你怎么一个人来了？这个。"


def test_a_caption_read_only_once_does_not_respell_heard_words():
    heard = [asr(102.5, 106.3, "完了，这个码头我听说过，鬼中之鬼！")]
    rows, counts = burned.merge_into_transcript(heard, [caption(104.8, 105.9, "诡中之诡", hits=1)])
    assert counts["worded"] == 0 and rows == heard


def test_a_short_caption_sharing_only_a_common_character_is_still_added():
    heard = [asr(10.0, 12.0, "我们去吃饭吧")]
    rows, counts = burned.merge_into_transcript(heard, [caption(12.2, 13.0, "我知道了")])
    assert counts["added"] == 1
    assert [r["text"] for r in rows] == ["我们去吃饭吧", "我知道了"]


def test_a_caption_beside_unrelated_speech_is_still_added():
    heard = [asr(0.0, 6.0, "今天天气真好我们去公园散步吧")]
    rows, counts = burned.merge_into_transcript(heard, [caption(2.0, 3.0, "少东家回来了")])
    assert counts["added"] == 1
    assert [r["text"] for r in rows] == ["今天天气真好我们去公园散步吧", "少东家回来了"]


def test_a_repeated_caption_is_added_once():
    rows, _ = burned.merge_into_transcript(
        [], [caption(1.0, 2.0, "当然要等人齐"), caption(1.5, 2.5, "当然要等人齐")]
    )
    assert len(rows) == 1


def test_a_bilingual_caption_matches_the_line_asr_heard():
    heard = [asr(28.2, 29.3, "Hold the rope!")]
    rows, counts = burned.merge_into_transcript(
        heard, [caption(28.2, 29.3, "抓紧绳子", alt_text="Hold the rope!")]
    )
    assert counts == {"added": 0, "corrected": 0, "worded": 0}
    assert rows == heard


def test_the_merge_never_edits_its_input():
    original = [asr(0.0, 1.0, "你好")]
    burned.merge_into_transcript(original, [caption(5.0, 6.0, "再见了朋友")])
    assert original == [asr(0.0, 1.0, "你好")]


def test_two_sentences_heard_as_one_row_are_cut_where_the_caption_line_changes():
    row = {
        "start": 65.4,
        "end": 67.3,
        "text": "——先走。我来断后。",
        "timing_words": [
            {"word": "先走", "start": 65.5, "end": 66.0},
            {"word": "我来断后", "start": 66.5, "end": 67.2},
        ],
        "sentence_boundary_review": True,
    }
    rows = transcribe.split_at_captions(
        [row], [caption(65.3, 66.0, "你先走"), caption(66.4, 67.3, "我来断后")]
    )
    assert [(r["text"], r["start"], r["end"]) for r in rows] == [
        ("——先走。", 65.4, 66.2),
        ("我来断后。", 66.2, 67.3),
    ]
    assert all(r["split_timing"] == "burned_subtitles" for r in rows)
    assert not any(r.get("sentence_boundary_review") for r in rows)
    assert [w["word"] for w in rows[1]["timing_words"]] == ["我来断后"]


def test_a_row_stays_whole_without_a_caption_pair_for_its_sentences():
    row = {"start": 0.0, "end": 3.0, "text": "先走。我来断后。"}
    assert transcribe.split_at_captions([row], [caption(0.0, 3.0, "先走我来断后")]) == [row]
    assert transcribe.split_at_captions([row], []) == [row]


def test_a_comma_between_two_caption_lines_is_cut_only_where_the_picture_cuts():
    row = {"start": 65.4, "end": 67.3, "text": "船来了，大家上船。"}
    lines = [caption(65.3, 66.0, "船来了"), caption(66.4, 67.3, "大家上船")]
    rows = transcribe.split_at_captions([row], lines, cuts=[30.0, 66.4])
    assert [(r["text"], r["end"]) for r in rows] == [("船来了，", 66.2), ("大家上船。", 67.3)]
    # Without a cut there, one speaker's sentence simply wrapped onto a second line.
    assert transcribe.split_at_captions([row], lines, cuts=[30.0]) == [row]
    assert transcribe.split_at_captions([row], lines) == [row]


def test_speech_after_the_last_caption_that_no_caption_spells_becomes_its_own_row():
    row = {"start": 102.5, "end": 106.3, "text": "完了，这个码头我听说过。好啦好啦，下次再见。"}
    lines = [
        caption(102.5, 103.4, "完了"),
        caption(103.5, 104.6, "这个码头我听说过"),
    ]
    rows = transcribe.split_at_captions([row], lines)
    assert [(r["text"], r["start"], r["end"]) for r in rows] == [
        ("完了，这个码头我听说过。", 102.5, 104.6),
        ("好啦好啦，下次再见。", 104.6, 106.3),
    ]


def test_readings_become_rows_and_a_single_blank_frame_does_not_split_a_caption():
    samples = [
        (0.0, "", ""),
        (0.35, "开船咯", ""),
        (0.7, "", ""),
        (1.05, "开船咯", ""),
        (1.4, "", ""),
        (1.75, "", ""),
        (2.1, "一个字", ""),
    ]
    rows = burned._runs(samples, duration=10.0)
    assert [(r["text"], r["start"], r["end"], r["hits"]) for r in rows] == [
        ("开船咯", 0.175, 1.225, 2)
    ]


def test_a_stray_short_reading_is_noise_but_a_long_single_one_is_kept():
    assert burned._runs([(1.0, "妈", "")], duration=5.0) == []
    (row,) = burned._runs([(1.0, "少东家回来了", "")], duration=5.0)
    assert row["text"] == "少东家回来了" and row["hits"] == 1


def test_stacked_lines_are_split_by_script_and_annotations_are_dropped():
    boxes = [
        ("抓紧绳子（口误）", 0.5, 10.0, 8.0),
        ("hold the rope", 0.5, 22.0, 8.0),
    ]
    assert burned._lines(boxes, prefer_cjk=True) == ("抓紧绳子", "hold the rope")


def test_boxes_on_one_row_read_left_to_right():
    boxes = [("回来了", 0.6, 10.0, 8.0), ("少东家", 0.4, 10.5, 8.0)]
    assert burned._lines(boxes, prefer_cjk=True)[0] == "少东家回来了"


def test_text_only_in_the_other_script_is_a_scene_label_not_a_caption():
    assert burned._lines([("FERRY", 0.5, 10.0, 8.0)], prefer_cjk=True) == ("", "")


def test_only_readings_on_the_caption_baseline_survive():
    caps = [(0.35 * i, "我们走吧", "", 0.80) for i in range(1, 5)]
    stray = [(9.0, "禁止停泊", "", 0.62), (9.35, "禁止停泊", "", 0.62)]
    kept = burned._on_baseline([(at, t, a, y) for at, t, a, y in caps + stray])
    assert [r[1] for r in kept if r[1]] == ["我们走吧"] * 4


def test_scattered_readings_mean_there_is_no_caption_track():
    scattered = [(i * 0.35, "路牌文字", "", 0.60 + 0.05 * (i % 6)) for i in range(6)]
    assert not any(r[1] for r in burned._on_baseline(scattered))


@pytest.mark.parametrize("text", ["（口误）", "()"])
def test_a_caption_that_is_only_an_annotation_yields_no_line(text):
    assert burned._lines([(text, 0.5, 10.0, 8.0)], prefer_cjk=True) == ("", "")


def test_reader_reports_unavailable_when_ocr_is_missing(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)
    result = burned.read_burned_subtitles(tmp_path / "x.mp4")
    assert result["status"] == "unavailable"
    assert result["rows"] == []


def _serialize(rows, **kwargs):
    words = [
        type("S", (), {"start": r["start"], "end": r["end"], "text": r["text"]}) for r in rows
    ]
    return transcribe.serialize_transcript(
        words, media=Path("v.mp4"), model="m", language="zh", **kwargs
    )


def test_transcript_merges_captions_and_records_what_it_did():
    burned_result = {
        "status": "read",
        "engine": "rapidocr",
        "rows": [caption(5.0, 6.0, "少东家回来了")],
    }
    text, plain, srt = _serialize([asr(0.0, 2.0, "你好")], burned=burned_result)
    payload = json.loads(text)
    assert [r["text"] for r in payload["segments"]] == ["你好", "少东家回来了"]
    assert payload["burned_subtitles"] == {
        "status": "read",
        "engine": "rapidocr",
        "merge": burned.MERGE_METHOD,
        "added": 1,
        "corrected": 0,
        "worded": 0,
        "split": 0,
    }
    assert "少东家回来了" in plain and "少东家回来了" in srt


def test_transcript_without_a_caption_pass_records_nothing():
    payload = json.loads(_serialize([asr(0.0, 2.0, "你好")])[0])
    assert "burned_subtitles" not in payload


def test_an_unavailable_reader_is_recorded_not_hidden():
    result = {"status": "unavailable", "engine": "rapidocr", "rows": [], "error": "no module"}
    payload = json.loads(_serialize([asr(0.0, 2.0, "你好")], burned=result)[0])
    assert payload["burned_subtitles"]["status"] == "unavailable"
    assert payload["burned_subtitles"]["error"] == "no module"
