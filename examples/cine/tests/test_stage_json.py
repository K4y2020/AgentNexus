import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "film-analysis"))

from pipeline import production


def test_broken_json_in_the_stage_file_goes_back_for_repair(tmp_path):
    (tmp_path / "outline.json").write_text('{"scenes": [}', encoding="utf-8")
    row = production.check_stage(tmp_path, "outline")
    assert row["status"] == "invalid_shape"
    assert "outline.json 不是合法 JSON" in row["errors"][0]


def test_broken_json_upstream_is_still_a_blocker(tmp_path):
    (tmp_path / "outline.json").write_text("{", encoding="utf-8")
    for name in ("script", "art", "cast"):
        (tmp_path / f"{name}.json").write_text(json.dumps({}), encoding="utf-8")
    assert production.check_stage(tmp_path, "script")["status"] == "blocked"
