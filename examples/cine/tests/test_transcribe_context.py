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


def test_refine_can_be_restricted_to_text_only():
    """Attribution belongs to JEV; the generative pass should not also guess it.

    Splitting them matters because attribution needs a cast document that does
    not exist yet at transcription time, while text repair does not.
    """
    import inspect

    signature = inspect.signature(transcribe.llm_refine_transcript)
    assert "speakers" in signature.parameters
    assert signature.parameters["speakers"].default is True
