"""The film-analysis JEV review finds its key through the pipeline's shared lookup."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import jev_client, jev_review, jev_transport


@pytest.fixture
def home(monkeypatch, tmp_path):
    """No key in the environment, an empty home and an empty working directory."""
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("AGENTNEXUS_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.usefixtures("home")
def test_environment_key_configures_jev(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", " env-key ")
    assert jev_review.has_jev_configured()
    assert jev_review._find_api_key() == "env-key"


@pytest.mark.usefixtures("home")
def test_without_a_key_jev_is_not_configured():
    assert not jev_review.has_jev_configured()


def test_agentnexus_env_file_supplies_the_key(home):
    (home / ".agentnexus").mkdir()
    (home / ".agentnexus" / ".env").write_text("TYPESAFE_API_KEY=k\n", encoding="utf-8")
    assert jev_review._find_api_key() == "k"
    assert jev_review.has_jev_configured()


def test_every_pipeline_caller_shares_one_lookup():
    assert jev_review._find_api_key is jev_transport.find_api_key
    assert jev_client._find_api_key is jev_transport.find_api_key
    assert jev_review.open_client is jev_transport.open_client
