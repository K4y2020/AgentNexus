import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "examples/cine/skills/film-analysis/pipeline/transcribe.py"
)
SPEC = importlib.util.spec_from_file_location("cine_transcribe", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_serialized_transcript_is_explicitly_qualified(tmp_path):
    json_text, plain_text = MODULE.serialize_transcript(
        [SimpleNamespace(start=1.25, end=2.5, text=" 你好 ")],
        media=tmp_path / "source.mp4",
        model="small",
        language="zh",
    )
    assert '"verbatim_certified": false' in json_text
    assert "qualified, not manually verified verbatim" in plain_text
    assert "[0001.250-0002.500] 你好" in plain_text


def test_asr_rejects_paths_outside_topic(tmp_path):
    with pytest.raises(ValueError, match="Topic workspace"):
        MODULE._inside(tmp_path / "topic", tmp_path / "other/source.mp4")
