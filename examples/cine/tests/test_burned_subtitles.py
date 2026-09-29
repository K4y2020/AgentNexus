"""Captions burned into the frames are source dialogue that ASR can miss."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import burned_subtitles as burned  # noqa: E402
from pipeline import transcribe  # noqa: E402


def caption(start, end, text, **extra):
    return {"start": start, "end": end, "text": text, **extra}


def asr(start, end, text):
    return {"start": start, "end": end, "text": text}


def test_a_caption_asr_never_heard_becomes_a_row_without_a_speaker():
    rows, counts = burned.merge_into_transcript(
        [asr(0.0, 3.0, "老夫夜观天象")], [caption(93.3, 95.7, "这家人的钱太难赚了")]
    )
    assert counts == {"added": 1, "corrected": 0}
    assert [r["text"] for r in rows] == ["老夫夜观天象", "这家人的钱太难赚了"]
    assert rows[1]["source"] == burned.SOURCE_TAG
    assert "speaker" not in rows[1]


def test_a_caption_asr_already_holds_adds_nothing():
    heard = [asr(20.0, 28.0, "我叫林黛玉，是林氏集团的继承人。老妈刚刚去世，家里一团乱麻")]
    rows, counts = burned.merge_into_transcript(
        heard,
        [caption(21.3, 23.0, "我叫林黛玉"), caption(24.1, 25.3, "老妈刚刚去世")],
    )
    assert counts == {"added": 0, "corrected": 0}
    assert rows == heard


def test_a_misheard_row_takes_the_caption_wording_and_keeps_what_was_heard():
    rows, counts = burned.merge_into_transcript(
        [asr(98.4, 99.4, "家这么颠的吗？")], [caption(98.4, 99.4, "这个家这么癫的吗")]
    )
    assert counts == {"added": 0, "corrected": 1}
    assert rows[0]["text"] == "这个家这么癫的吗"
    assert rows[0]["asr_text"] == "家这么颠的吗？"
    assert rows[0]["source"] == burned.SOURCE_TAG


def test_a_variant_character_inside_a_heard_row_is_not_a_second_line():
    heard = [asr(102.5, 106.3, "完蛋了，这个表哥我听说过，颠中之颠！对不起，对不起！")]
    rows, counts = burned.merge_into_transcript(heard, [caption(104.8, 105.9, "癫中之癫")])
    assert counts == {"added": 0, "corrected": 0}
    assert rows == heard


def test_a_variant_caption_straddling_two_heard_rows_is_not_a_second_line():
    heard = [
        asr(102.5, 104.8, "完蛋了，这个表哥我听说过，颠中之颠。"),
        asr(105.0, 106.3, "好好好好好好！"),
    ]
    rows, counts = burned.merge_into_transcript(heard, [caption(104.8, 105.9, "癫中之癫")])
    assert counts == {"added": 0, "corrected": 0}
    assert rows == heard


def test_a_short_caption_sharing_only_a_common_character_is_still_added():
    heard = [asr(10.0, 12.0, "我们去吃饭吧")]
    rows, counts = burned.merge_into_transcript(heard, [caption(12.2, 13.0, "我知道了")])
    assert counts["added"] == 1
    assert [r["text"] for r in rows] == ["我们去吃饭吧", "我知道了"]


def test_a_caption_beside_unrelated_speech_is_still_added():
    heard = [asr(0.0, 6.0, "今天天气真好我们去公园散步吧")]
    rows, counts = burned.merge_into_transcript(heard, [caption(2.0, 3.0, "宝少爷回来了")])
    assert counts["added"] == 1
    assert [r["text"] for r in rows] == ["今天天气真好我们去公园散步吧", "宝少爷回来了"]


def test_a_repeated_caption_is_added_once():
    rows, _ = burned.merge_into_transcript(
        [], [caption(1.0, 2.0, "当然是陪伴啊"), caption(1.5, 2.5, "当然是陪伴啊")]
    )
    assert len(rows) == 1


def test_a_bilingual_caption_matches_the_line_asr_heard():
    heard = [asr(28.2, 29.3, "What the hell?")]
    rows, counts = burned.merge_into_transcript(
        heard, [caption(28.2, 29.3, "搞什么鬼", alt_text="What the hell?")]
    )
    assert counts == {"added": 0, "corrected": 0}
    assert rows == heard


def test_the_merge_never_edits_its_input():
    original = [asr(0.0, 1.0, "你好")]
    burned.merge_into_transcript(original, [caption(5.0, 6.0, "再见了朋友")])
    assert original == [asr(0.0, 1.0, "你好")]


def test_readings_become_rows_and_a_single_blank_frame_does_not_split_a_caption():
    samples = [
        (0.0, "", ""),
        (0.35, "完蛋了", ""),
        (0.7, "", ""),
        (1.05, "完蛋了", ""),
        (1.4, "", ""),
        (1.75, "", ""),
        (2.1, "一个字", ""),
    ]
    rows = burned._runs(samples, duration=10.0)
    assert [(r["text"], r["start"], r["end"]) for r in rows] == [("完蛋了", 0.175, 1.225)]


def test_a_stray_short_reading_is_noise_but_a_long_single_one_is_kept():
    assert burned._runs([(1.0, "妈", "")], duration=5.0) == []
    (row,) = burned._runs([(1.0, "宝少爷回来了", "")], duration=5.0)
    assert row["text"] == "宝少爷回来了"


def test_stacked_lines_are_split_by_script_and_annotations_are_dropped():
    boxes = [
        ("搞什么鬼（口误）", 0.5, 10.0, 8.0),
        ("what the hell", 0.5, 22.0, 8.0),
    ]
    assert burned._lines(boxes, prefer_cjk=True) == ("搞什么鬼", "what the hell")


def test_boxes_on_one_row_read_left_to_right():
    boxes = [("回来了", 0.6, 10.0, 8.0), ("宝少爷", 0.4, 10.5, 8.0)]
    assert burned._lines(boxes, prefer_cjk=True)[0] == "宝少爷回来了"


def test_text_only_in_the_other_script_is_a_scene_label_not_a_caption():
    assert burned._lines([("SANDWICH", 0.5, 10.0, 8.0)], prefer_cjk=True) == ("", "")


def test_only_readings_on_the_caption_baseline_survive():
    caps = [(0.35 * i, "我们走吧", "", 0.80) for i in range(1, 5)]
    stray = [(9.0, "退回寄件人", "", 0.62), (9.35, "退回寄件人", "", 0.62)]
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
    words = [type("S", (), {"start": r["start"], "end": r["end"], "text": r["text"]}) for r in rows]
    return transcribe.serialize_transcript(
        words, media=Path("v.mp4"), model="m", language="zh", **kwargs
    )


def test_transcript_merges_captions_and_records_what_it_did():
    burned_result = {
        "status": "read",
        "engine": "rapidocr",
        "rows": [caption(5.0, 6.0, "宝少爷回来了")],
    }
    text, plain, srt = _serialize([asr(0.0, 2.0, "你好")], burned=burned_result)
    payload = json.loads(text)
    assert [r["text"] for r in payload["segments"]] == ["你好", "宝少爷回来了"]
    assert payload["burned_subtitles"] == {
        "status": "read",
        "engine": "rapidocr",
        "added": 1,
        "corrected": 0,
    }
    assert "宝少爷回来了" in plain and "宝少爷回来了" in srt


def test_transcript_without_a_caption_pass_records_nothing():
    payload = json.loads(_serialize([asr(0.0, 2.0, "你好")])[0])
    assert "burned_subtitles" not in payload


def test_an_unavailable_reader_is_recorded_not_hidden():
    result = {"status": "unavailable", "engine": "rapidocr", "rows": [], "error": "no module"}
    payload = json.loads(_serialize([asr(0.0, 2.0, "你好")], burned=result)[0])
    assert payload["burned_subtitles"]["status"] == "unavailable"
    assert payload["burned_subtitles"]["error"] == "no module"
