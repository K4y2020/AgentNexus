from pathlib import Path

import pytest

from agentnexus.bot_workspace import ensure_bot_task_workspace


def test_topics_are_isolated_and_resumes_preserve_files(tmp_path):
    legacy = tmp_path / "old-report.html"
    legacy.write_text("keep", encoding="utf-8")
    first = Path(ensure_bot_task_workspace(str(tmp_path), "session_a"))
    second = Path(ensure_bot_task_workspace(str(tmp_path), "session_b"))
    assert first != second
    assert {p.name for p in first.iterdir()} == {"inputs", "work", "outputs", "temp"}
    report = first / "outputs" / "report.md"
    report.write_text("result", encoding="utf-8")
    assert ensure_bot_task_workspace(str(tmp_path), "session_a") == str(first)
    assert report.read_text(encoding="utf-8") == "result"
    assert legacy.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("session_id", ["", "..", "../escape", "a/b", "a\\b", "C:escape"])
def test_rejects_unsafe_session_ids(tmp_path, session_id):
    with pytest.raises(ValueError):
        ensure_bot_task_workspace(str(tmp_path), session_id)
    assert not list(tmp_path.iterdir())
