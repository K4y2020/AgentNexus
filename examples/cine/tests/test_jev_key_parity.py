"""The runner and a Topic resolve the same TypeSafe key and proxy.

agentnexus core and the film-analysis pipeline each carry a lookup, because the
Topic python has no agentnexus; this keeps the two in step.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import jev_transport

from agentnexus.seedance import jev
from agentnexus.seedance.env_keys import env_files, read_key


@pytest.fixture
def home(monkeypatch, tmp_path):
    for name in ("TYPESAFE_API_KEY", "AGENTNEXUS_CONFIG_HOME", *jev._PROXY_ENV_VARS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path / "home"))
    monkeypatch.setattr("urllib.request.getproxies", dict)
    (tmp_path / "work").mkdir()
    monkeypatch.chdir(tmp_path / "work")
    return tmp_path


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


SCENARIOS = {
    "nothing": {},
    "cwd": {"work/.env": b"TYPESAFE_API_KEY=cwd\n"},
    "config home": {"home/.agentnexus/.env": b"TYPESAFE_API_KEY='config'\n"},
    "home": {"home/.env": b'TYPESAFE_API_KEY="home"\n'},
    "first file wins": {
        "work/.env": b"OTHER=1\nTYPESAFE_API_KEY=cwd\n",
        "home/.env": b"TYPESAFE_API_KEY=home\n",
    },
    "empty value is skipped": {
        "work/.env": b"TYPESAFE_API_KEY=\n",
        "home/.agentnexus/.env": b"TYPESAFE_API_KEY=config\n",
    },
    "bom and undecodable": {
        "work/.env": b"\xff\xfe\x00TYPESAFE",
        "home/.env": "﻿TYPESAFE_API_KEY=bom\n".encode(),
    },
}


@pytest.mark.parametrize("files", SCENARIOS.values(), ids=SCENARIOS.keys())
def test_core_and_pipeline_find_the_same_key(home, files):
    for relative, data in files.items():
        _write(home / relative, data)

    assert read_key("TYPESAFE_API_KEY") == jev_transport.find_api_key()


def test_core_and_pipeline_search_the_same_files(home, monkeypatch):
    assert env_files() == jev_transport.env_files()
    monkeypatch.setenv("AGENTNEXUS_CONFIG_HOME", str(home / "isolated"))
    assert env_files() == jev_transport.env_files()


@pytest.mark.parametrize(
    "proxy",
    [None, "https://127.0.0.1:7890", "https://localhost:1080/", "http://proxy:3128"],
)
def test_core_and_pipeline_use_the_same_proxy(home, monkeypatch, proxy):
    if proxy:
        monkeypatch.setenv("https_proxy", proxy)

    assert jev.effective_proxy() == jev_transport.effective_proxy()
