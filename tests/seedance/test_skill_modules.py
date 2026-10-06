from pathlib import Path
from types import SimpleNamespace

import pytest

from agentnexus.seedance.skill_modules import (
    SkillModuleUnavailable,
    film_analysis_skills_dir,
    pipeline_module,
)

SKILLS = Path(__file__).resolve().parents[2] / "examples/cine/skills"


def test_skills_dir_comes_from_the_registered_film_analysis_skill(tmp_path):
    film = SimpleNamespace(name="film-analysis", skill_dir=tmp_path / "skills/film-analysis")
    other = SimpleNamespace(name="cine-art", skill_dir=tmp_path / "elsewhere/cine-art")

    assert film_analysis_skills_dir(SimpleNamespace(skills=[other, film])) == tmp_path / "skills"
    assert film_analysis_skills_dir(SimpleNamespace(skills=[other])) is None
    assert film_analysis_skills_dir(None) is None


def test_only_the_contract_modules_can_be_loaded():
    assert pipeline_module(SKILLS, "draft_check").story_draft_gaps
    with pytest.raises(SkillModuleUnavailable, match="does not load"):
        pipeline_module(SKILLS, "transcribe")


def test_loaded_modules_share_one_package_per_pipeline():
    identity = pipeline_module(SKILLS, "source_identity")
    production = pipeline_module(SKILLS, "production")

    assert production._source_identity()[0] is identity.fingerprint_of
