import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import visual_style

GATEWAY = ("https://gateway.test/v1", "key", "vision")


def project(tmp_path, shots):
    (tmp_path / "revisions/r1").mkdir(parents=True)
    (tmp_path / "project.json").write_text(json.dumps({"current_revision": "r1"}), "utf-8")
    (tmp_path / "source.json").write_text(json.dumps({"source_id": "src"}), "utf-8")
    notes = {"source_id": "src", "revision_id": "r1", "status": "read", "shots": shots}
    (tmp_path / "revisions/r1/shot_notes.json").write_text(json.dumps(notes), "utf-8")
    media = tmp_path / "film.mp4"
    media.write_bytes(b"film")
    return media


def shot(start, end, teaser=False):
    return {"start": start, "end": end, "teaser": teaser}


def test_samples_spread_over_the_film_proper_and_skip_the_teaser():
    shots = [shot(0, 2, teaser=True)] + [shot(i, i + 2) for i in range(2, 42, 2)]
    times = visual_style.sample_times(shots, count=6)
    assert len(times) == 6
    assert times[0] == 3.0 and times[-1] == 41.0
    assert times == sorted(times)
    assert visual_style.sample_times([shot(0, 4, teaser=True)]) == [2.0]
    assert visual_style.sample_times([]) == []


def test_reading_is_saved_and_reused_until_its_inputs_change(tmp_path, monkeypatch):
    media = project(tmp_path, [shot(0, 4), shot(4, 8)])
    asked = []

    def ask(frames, _gateway):
        asked.append(len(frames))
        return {"style": "photoreal", "evidence": "真实皮肤"}, {"total_tokens": 9}

    monkeypatch.setattr(visual_style, "_frame", lambda *_: b"jpeg")
    monkeypatch.setattr(visual_style, "_ask", ask)
    first = visual_style.read_visual_style(media, tmp_path, gateway=GATEWAY)
    assert (first["status"], first["style"], first["frame_seconds"]) == (
        "read",
        "photoreal",
        [2.0, 6.0],
    )
    saved = json.loads((tmp_path / "revisions/r1/visual_style.json").read_text("utf-8"))
    assert saved["style"] == "photoreal" and saved["model"] == "vision"
    assert visual_style.read_visual_style(media, tmp_path, gateway=GATEWAY) == saved
    assert asked == [2]
    visual_style.read_visual_style(media, tmp_path, gateway=(*GATEWAY[:2], "other-vision"))
    assert asked == [2, 2]
    project_notes = tmp_path / "revisions/r1/shot_notes.json"
    notes = json.loads(project_notes.read_text("utf-8"))
    notes["shots"].append(shot(8, 12))
    project_notes.write_text(json.dumps(notes), "utf-8")
    assert visual_style.read_visual_style(media, tmp_path, gateway=GATEWAY)["frame_seconds"] == [
        2.0,
        6.0,
        10.0,
    ]
    assert asked == [2, 2, 3]


def test_a_failed_reading_is_recorded_and_retried_next_time(tmp_path, monkeypatch):
    media = project(tmp_path, [shot(0, 4)])
    monkeypatch.setattr(visual_style, "_frame", lambda *_: b"jpeg")

    def broken(*_):
        raise ValueError("reply names no known style")

    monkeypatch.setattr(visual_style, "_ask", broken)
    failed = visual_style.read_visual_style(media, tmp_path, gateway=GATEWAY)
    assert failed["status"] == "unavailable" and failed["style"] is None
    assert "reply names no known style" in failed["errors"][0]
    monkeypatch.setattr(visual_style, "_ask", lambda *_: ({"style": "ghibli", "evidence": ""}, {}))
    assert visual_style.read_visual_style(media, tmp_path, gateway=GATEWAY)["style"] == "ghibli"
    unconfigured = visual_style.read_visual_style(media, tmp_path, gateway=("", "", ""))
    assert unconfigured["errors"] == ["no vision model configured"]


def test_reply_parsing_accepts_only_known_styles(monkeypatch):
    class Response:
        def __init__(self, text):
            self.body = json.dumps({"choices": [{"message": {"content": text}}], "usage": {}})

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self.body.encode()

    replies = iter(
        ['```json\n{"style": "semi-realistic", "evidence": "笔触"}\n```', '{"style": "oil"}']
    )
    monkeypatch.setattr(
        visual_style.urllib.request, "urlopen", lambda *_, **__: Response(next(replies))
    )
    assert visual_style._ask([b"x"], GATEWAY)[0] == {"style": "semi-realistic", "evidence": "笔触"}
    try:
        visual_style._ask([b"x"], GATEWAY)
    except ValueError as exc:
        assert "no known style" in str(exc)
    else:
        raise AssertionError("an unknown style was accepted")
