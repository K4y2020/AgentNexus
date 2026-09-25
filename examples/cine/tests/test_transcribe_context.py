"""ASR refinement context: relationships, never a bare name list."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import transcribe  # noqa: E402


def test_name_list_is_detected_as_unusable_context():
    """A name list says who may appear, not who addresses whom.

    Passing 「林黛玉 贾宝玉 贾母 贾敏…」 labelled 「男人靠不住」 as 贾母 and a
    unison chorus line as 贾宝玉 — a character who never appears in the episode.
    """
    assert transcribe._looks_like_name_list("林黛玉 贾宝玉 贾母 贾敏 林如海 算命 算命先生")


def test_relational_context_is_not_mistaken_for_a_name_list():
    assert not transcribe._looks_like_name_list("贾敏是林如海之妻、黛玉之母；贾母是黛玉的外婆")
    assert not transcribe._looks_like_name_list(
        "人物设定与关系：\n- 林黛玉（别名黛玉；林氏集团独女继承人）"
    )
    assert not transcribe._looks_like_name_list("")
    assert not transcribe._looks_like_name_list("短片")


def test_usable_context_warns_instead_of_silently_using_a_list():
    out = transcribe._usable_context("林黛玉 贾宝玉 贾母 贾敏 林如海 算命 算命先生")
    assert "人名清单" in out
    assert "不要按人名顺序" in out


def test_usable_context_passes_relationships_through_unchanged():
    text = "贾敏是林如海之妻、黛玉之母"
    assert transcribe._usable_context(text) == text


def test_context_from_cast_emits_relationships_not_names():
    cast = {
        "characters": [
            {
                "id": "C02",
                "name": "贾敏",
                "aliases": ["大姐"],
                "oneLiner": "黛玉之母、林如海之妻",
                "persona": {"gender": "女", "identity": "林夫人"},
            }
        ]
    }
    out = transcribe.context_from_cast(json.dumps(cast, ensure_ascii=False))
    assert "贾敏" in out
    assert "黛玉之母、林如海之妻" in out     # kinship reaches the model
    assert "不要按人物出现顺序指派" in out


def test_context_from_cast_marks_a_group_as_speaking_in_unison():
    """A chorus line otherwise reads as one person's thought, or as narration."""
    cast = {
        "characters": [
            {"id": "C08", "name": "佣人群体", "persona": {"gender": "群体", "identity": "贾府侍从"}}
        ]
    }
    out = transcribe.context_from_cast(json.dumps(cast, ensure_ascii=False))
    assert "齐声念白" in out
    assert "不是旁白" in out


def test_context_from_cast_falls_back_to_raw_text():
    assert transcribe.context_from_cast("不是 JSON") == "不是 JSON"


def test_context_from_cast_carries_persona_relationships():
    """The documented cast schema keeps kinship in persona.relationships."""
    cast = {
        "characters": [
            {
                "name": "老周",
                "persona": {"relationships": [{"name": "小满", "relation": "外孙"}]},
            }
        ]
    }
    out = transcribe.context_from_cast(json.dumps(cast, ensure_ascii=False))
    assert "与小满：外孙" in out


def test_clean_asr_text_applies_no_story_specific_substitutions():
    """A fixed homophone table rewrites every other story's dialogue."""
    for text in ("复课仪式开始了", "出世那碗饭", "储物隔里有信"):
        assert transcribe.clean_asr_text(text) == text
    # Story-independent repairs still apply.
    assert transcribe.clean_asr_text("你好、世界。") == "你好，世界"
    assert transcribe.clean_asr_text("JakenＢ来了") == "Jaken，来了"


ROWS = [
    {"start": 0.0, "end": 1.2, "speaker": "说话人未标注", "text": "外公雾大吗"},
    {"start": 1.5, "end": 2.4, "speaker": "说话人未标注", "text": "今天不开船"},
]


def test_text_only_refinement_keeps_rows_timings_and_speakers():
    refined = [
        {"start": 0.0, "end": 1.2, "speaker": "外公", "text": "外公，雾大吗？"},
        {"start": 1.5, "end": 2.4, "speaker": "小满", "text": "今天不开船。"},
    ]
    kept = transcribe._accepted_refinement(ROWS, refined, speakers=False)
    assert [r["text"] for r in kept] == ["外公，雾大吗？", "今天不开船。"]
    # Speaker labels are left for JEV, even if the model filled them in.
    assert [r["speaker"] for r in kept] == ["说话人未标注", "说话人未标注"]


def test_text_only_refinement_that_merges_or_retimes_rows_is_rejected():
    merged = [{"start": 0.0, "end": 2.4, "text": "外公，雾大吗？今天不开船。"}]
    retimed = [
        {"start": 0.0, "end": 1.9, "text": "外公，雾大吗？"},
        {"start": 1.9, "end": 2.4, "text": "今天不开船。"},
    ]
    blank = [{"start": 0.0, "end": 1.2, "text": ""}, {"start": 1.5, "end": 2.4, "text": "x"}]
    for reply in (merged, retimed, blank, {"not": "a list"}, []):
        assert transcribe._accepted_refinement(ROWS, reply, speakers=False) == ROWS


def test_refine_prompt_carries_no_sample_story_examples(monkeypatch):
    import urllib.request

    sent = {}

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            content = json.dumps(
                [
                    {"start": 0.0, "end": 1.2, "text": "外公，雾大吗？"},
                    {"start": 1.5, "end": 2.4, "text": "今天不开船。"},
                ],
                ensure_ascii=False,
            )
            return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")

    def fake_urlopen(request, **_kwargs):
        sent["body"] = json.loads(request.data.decode("utf-8"))
        return Reply()

    monkeypatch.setenv("AGENTNEXUS_GATEWAY_URL", "http://gateway.test/v1")
    monkeypatch.setenv("AGENTNEXUS_API_KEY", "test")
    monkeypatch.setenv("AGENTNEXUS_TRANSCRIBE_MODEL", "test-transcribe-model")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    rows = transcribe.llm_refine_transcript(ROWS, None, speakers=False)
    assert sent["body"]["model"] == "test-transcribe-model"
    prompt = sent["body"]["messages"][1]["content"]
    for sample in ("复课", "出世那碗", "储物隔", "母亲叹息", "女儿拆台", "卖惨"):
        assert sample not in prompt
    assert [r["text"] for r in rows] == ["外公，雾大吗？", "今天不开船。"]


def _gateway(monkeypatch, reply=None):
    """Point the refine pass at a fake gateway: ``reply`` rows, or a failure."""
    import urllib.request

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            content = json.dumps(reply, ensure_ascii=False)
            return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")

    def fake_urlopen(_request, **_kwargs):
        if reply is None:
            raise OSError("gateway unreachable")
        return Reply()

    monkeypatch.setenv("AGENTNEXUS_GATEWAY_URL", "http://gateway.test/v1")
    monkeypatch.setenv("AGENTNEXUS_API_KEY", "test")
    monkeypatch.setenv("AGENTNEXUS_TRANSCRIBE_MODEL", "test-transcribe-model")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def _asr(start, end, text):
    from types import SimpleNamespace

    return SimpleNamespace(start=start, end=end, text=text)


def test_failed_refinement_does_not_certify_the_transcript(monkeypatch, tmp_path):
    _gateway(monkeypatch, reply=None)
    json_text, plain_text, srt_text = transcribe.serialize_transcript(
        [_asr(0.0, 1.2, "外公雾大吗")],
        media=tmp_path / "source.mp4",
        model="small",
        language="zh",
        llm_refine=True,
        refine_speakers=False,
    )
    payload = json.loads(json_text)
    assert payload["verbatim_certified"] is False
    assert payload["text_refinement"] == "not_applied"
    assert payload["segments"][0]["text"] == "外公雾大吗"
    assert "外公雾大吗" in plain_text and "外公雾大吗" in srt_text


def test_applied_refinement_is_still_not_verbatim(monkeypatch, tmp_path):
    _gateway(monkeypatch, reply=[{"start": 0.0, "end": 1.2, "text": "外公，雾大吗？"}])
    json_text, _plain, srt_text = transcribe.serialize_transcript(
        [_asr(0.0, 1.2, "外公雾大吗")],
        media=tmp_path / "source.mp4",
        model="small",
        language="zh",
        llm_refine=True,
        refine_speakers=False,
    )
    payload = json.loads(json_text)
    assert payload["text_refinement"] == "applied"
    assert payload["verbatim_certified"] is False
    assert "外公，雾大吗？" in srt_text


def test_transcribe_status_reports_a_failed_refinement(monkeypatch, tmp_path):
    import sys
    import types

    class WhisperModel:
        def __init__(self, *_args, **_kwargs):
            pass

        def transcribe(self, *_args, **_kwargs):
            return [_asr(0.0, 1.2, "外公雾大吗")], types.SimpleNamespace(language="zh")

    fake_whisper = types.SimpleNamespace(WhisperModel=WhisperModel)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_whisper)
    _gateway(monkeypatch, reply=None)
    media = tmp_path / "inputs" / "source.mp4"
    media.parent.mkdir()
    media.write_bytes(b"")

    result = transcribe.transcribe(
        media,
        tmp_path / "inputs" / "source-transcript",
        tmp_path,
        model="small",
        language="zh",
        llm_refine=True,
        refine_speakers=False,
    )
    assert result["verbatim_certified"] is False
    assert result["text_refinement"] == "not_applied"
    saved_path = tmp_path / "inputs" / "source-transcript.json"
    saved = json.loads(saved_path.read_text(encoding="utf-8"))
    assert saved["verbatim_certified"] is False


def test_refine_can_be_restricted_to_text_only():
    """Attribution belongs to JEV; the generative pass should not also guess it.

    Splitting them matters because attribution needs a cast document that does
    not exist yet at transcription time, while text repair does not.
    """
    import inspect

    signature = inspect.signature(transcribe.llm_refine_transcript)
    assert "speakers" in signature.parameters
    assert signature.parameters["speakers"].default is True


# ---------------------------------------------------------------------------
# Source identity: fingerprint in metadata, per-source vocal cache, SRT mirror
# ---------------------------------------------------------------------------


HEARD: list = []  # audio files the fake Whisper was given, in order


class _FakeWhisper:
    """faster_whisper stand-in that records which audio file it was given."""

    def __init__(self, *_args, **_kwargs):
        pass

    def transcribe(self, audio, **_kwargs):
        from types import SimpleNamespace

        HEARD.append(audio)
        text = Path(audio).read_text(encoding="utf-8") if audio.endswith(".wav") else "原声"
        return [_asr(0.0, 1.0, text)], SimpleNamespace(language="zh")


def _whisper(monkeypatch):
    import types

    HEARD.clear()
    fake = types.SimpleNamespace(WhisperModel=_FakeWhisper)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)


def _demucs(monkeypatch):
    """Fake ffmpeg + demucs: the vocal stem records which source it came from."""
    import subprocess

    runs = []

    def run(argv, **_kwargs):
        runs.append(argv)
        if argv[0] == "ffmpeg":
            media, audio = Path(argv[2]), Path(argv[-2])
            audio.write_text(media.read_bytes().decode("utf-8"), encoding="utf-8")
        else:
            out, audio = Path(argv[argv.index("-o") + 1]), Path(argv[-1])
            vocals = out / "htdemucs" / "audio" / "vocals.wav"
            vocals.parent.mkdir(parents=True, exist_ok=True)
            vocals.write_text("vocals of " + audio.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(subprocess, "run", run)
    return runs


def _run(tmp_path, content=None):
    media = tmp_path / "inputs" / "source.mp4"
    media.parent.mkdir(exist_ok=True)
    if content is not None:
        media.write_bytes(content.encode("utf-8"))
    return transcribe.transcribe(
        media,
        tmp_path / "inputs" / "source-transcript",
        tmp_path,
        model="small",
        language="zh",
        separate_vocals=True,
    )


def test_transcript_records_the_source_fingerprint(monkeypatch, tmp_path):
    _whisper(monkeypatch)
    _demucs(monkeypatch)
    result = _run(tmp_path, "film A")
    saved = json.loads((tmp_path / "inputs/source-transcript.json").read_text(encoding="utf-8"))
    expected = transcribe.media_fingerprint(tmp_path / "inputs/source.mp4")
    assert saved["source_fingerprint"] == expected == result["source_fingerprint"]


def test_vocal_cache_is_keyed_by_source_identity(monkeypatch, tmp_path):
    _whisper(monkeypatch)
    runs = _demucs(monkeypatch)
    # A separation left at the old fixed path by some earlier video.
    legacy = tmp_path / "inputs/separated/htdemucs/audio/vocals.wav"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("vocals of an old film", encoding="utf-8")

    _run(tmp_path, "film A")
    assert HEARD[-1] != str(legacy)
    assert Path(HEARD[-1]).read_text(encoding="utf-8") == "vocals of film A"
    separations = len(runs)

    # Same file name, different film: separated afresh, never the cached stem.
    _run(tmp_path, "film B")
    assert len(runs) == separations + 2
    assert Path(HEARD[-1]).read_text(encoding="utf-8") == "vocals of film B"

    # Back to film A: its own cached stem is reused without separating again.
    _run(tmp_path, "film A")
    assert len(runs) == separations + 2
    assert Path(HEARD[-1]).read_text(encoding="utf-8") == "vocals of film A"


def test_retranscription_updates_the_generated_mirror_but_not_a_supplied_subtitle(
    monkeypatch, tmp_path
):
    _whisper(monkeypatch)
    _demucs(monkeypatch)
    mirror = tmp_path / "inputs" / "source.srt"

    first = _run(tmp_path, "film A")
    assert first["srt_mirror"] == "created"
    assert "vocals of film A" in mirror.read_text(encoding="utf-8")

    second = _run(tmp_path, "film B")
    assert second["srt_mirror"] == "updated"
    assert "vocals of film B" in mirror.read_text(encoding="utf-8")

    mirror.write_text("1\n00:00:00,000 --> 00:00:01,000\nTrusted subtitle\n", encoding="utf-8")
    third = _run(tmp_path, "film C")
    assert third["srt_mirror"] == "preserved"
    assert "Trusted subtitle" in mirror.read_text(encoding="utf-8")
    generated = (tmp_path / "inputs/source-transcript.srt").read_text(encoding="utf-8")
    assert "vocals of film C" in generated
