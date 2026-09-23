import importlib.util
from pathlib import Path

from agentnexus.spec import load

BUNDLE = Path(__file__).resolve().parents[1] / "examples" / "cine"


def test_cine_bundle_loads():
    spec = load(BUNDLE)
    assert spec.name == "cine"
    assert [s.name for s in spec.skills] == [
        "cine-art",
        "cine-camera-evidence",
        "cine-characters",
        "cine-outline",
        "cine-script",
        "cine-storyboard",
        "film-analysis",
    ]
    assert spec.executor.config["harness"] == "openai-agents"
    skill_paths = [
        BUNDLE / "skills" / name / "SKILL.md"
        for name in [
            "film-analysis",
            "cine-outline",
            "cine-script",
            "cine-storyboard",
            "cine-characters",
            "cine-art",
            "cine-camera-evidence",
        ]
    ]
    for path in (BUNDLE / "config.yaml", *skill_paths):
        content = path.read_text(encoding="utf-8")
        assert "EVA" not in content
        assert "U:/movie" not in content


def test_media_project_delegates_to_canonical_pipeline(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(BUNDLE / "skills/film-analysis"))
    spec = importlib.util.spec_from_file_location(
        "media_project", BUNDLE / "skills/film-analysis/media_project.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def index(video, output):
        calls.append((video, output))
        return {"analysis_status": "indexed_unreviewed", "reviewed_shot_count": 0}

    monkeypatch.setattr(module, "index_media", index)
    video, output = tmp_path / "source.mp4", tmp_path / "film"
    assert module.initialize(video, output)["reviewed_shot_count"] == 0
    assert calls == [(video, output)]
