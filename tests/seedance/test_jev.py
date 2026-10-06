import httpx
import pytest
import respx

from agentnexus.seedance import jev
from agentnexus.seedance.env_keys import read_key


@pytest.fixture
def home(monkeypatch, tmp_path):
    """No key or proxy in the environment, an empty home and working directory."""
    for name in ("TYPESAFE_API_KEY", "AGENTNEXUS_CONFIG_HOME", *jev._PROXY_ENV_VARS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda _cls: tmp_path / "home"))
    monkeypatch.setattr("urllib.request.getproxies", dict)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return tmp_path


def _env(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_environment_wins_over_env_files(home, monkeypatch):
    _env(home / "work/.env", "TYPESAFE_API_KEY=file\n")
    monkeypatch.setenv("TYPESAFE_API_KEY", " env ")

    assert read_key("TYPESAFE_API_KEY") == "env"


def test_env_files_are_read_in_order(home, monkeypatch):
    _env(home / "home/.env", "TYPESAFE_API_KEY=home\n")
    assert read_key("TYPESAFE_API_KEY") == "home"
    _env(home / "home/.agentnexus/.env", "TYPESAFE_API_KEY='config'\n")
    assert read_key("TYPESAFE_API_KEY") == "config"
    _env(home / "work/.env", 'TYPESAFE_API_KEY="cwd"\n')
    assert read_key("TYPESAFE_API_KEY") == "cwd"


def test_the_config_home_override_is_honoured(home, monkeypatch):
    _env(home / "home/.agentnexus/.env", "TYPESAFE_API_KEY=default\n")
    _env(home / "isolated/.env", "TYPESAFE_API_KEY=isolated\n")
    monkeypatch.setenv("AGENTNEXUS_CONFIG_HOME", str(home / "isolated"))

    assert read_key("TYPESAFE_API_KEY") == "isolated"


def test_an_unreadable_env_file_is_skipped(home):
    (home / "work/.env").write_bytes(b"\xff\xfeTYPESAFE_API_KEY=x")
    _env(home / "home/.env", "﻿TYPESAFE_API_KEY=after-bom\n")

    assert read_key("TYPESAFE_API_KEY") == "after-bom"


@pytest.mark.asyncio
@pytest.mark.usefixtures("home")
async def test_without_a_key_nothing_is_sent():
    with respx.mock(assert_all_called=False) as router:
        route = router.post(jev.SYSTEM_ONE_URL)
        result = await jev.ask_system_one({"a": 1}, {"q": {"type": "noul"}})

    assert result == {"error": "TYPESAFE_API_KEY is not configured on this host"}
    assert not route.called


@pytest.mark.asyncio
async def test_answers_come_back_flat(home, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    with respx.mock() as router:
        route = router.post(jev.SYSTEM_ONE_URL).respond(
            200,
            json={
                "model": "jev-1",
                "answers": {
                    "pick": {"type": "choice", "choice": "a", "confidence": 0.9, "extra": 1},
                    "yes": {"type": "noul", "noul": 0.8},
                },
            },
        )
        result = await jev.ask_system_one(
            {"a": 1},
            {
                "pick": {"type": "Choice", "instructions": "?", "criteria": {"a": "A"}},
                "yes": {"type": "noul", "instructions": "?"},
            },
        )

    assert route.calls[0].request.headers["Authorization"] == "Bearer k"
    assert result == {
        "model": "jev-1",
        "answers": {
            "pick": {"type": "choice", "choice": "a", "probabilities": None, "confidence": 0.9},
            "yes": {"type": "noul", "noul": 0.8},
        },
    }


@pytest.mark.asyncio
async def test_an_incomplete_answer_set_is_an_error(home, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    with respx.mock() as router:
        router.post(jev.SYSTEM_ONE_URL).respond(200, json={"answers": {}})
        result = await jev.ask_system_one({}, {"q": {"type": "noul"}})

    assert result["error"] == "typesafe api returned an incomplete answer set"
    assert result["expected_questions"] == ["q"]


@pytest.mark.asyncio
async def test_http_errors_are_reported(home, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    with respx.mock() as router:
        router.post(jev.SYSTEM_ONE_URL).respond(401, text="bad key")
        result = await jev.ask_system_one({}, {"q": {"type": "noul"}})

    assert result == {"error": "typesafe api 401: bad key"}


@pytest.mark.asyncio
async def test_invalid_questions_are_rejected_before_a_call(home, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")

    assert await jev.ask_system_one({}, {}) == {"error": "questions must be a non-empty object"}
    assert await jev.ask_system_one({}, {"q": {"type": "vote"}}) == {
        "error": "question q has unknown type 'vote'"
    }


def test_an_https_loopback_proxy_is_corrected(home, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "https://127.0.0.1:7890")
    assert jev.effective_proxy() == "http://127.0.0.1:7890"
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy.example:8443")
    assert jev.effective_proxy() == "https://proxy.example:8443"


@pytest.mark.asyncio
async def test_a_broken_proxy_falls_back_to_a_direct_connection(home, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    attempts = []

    class Client(httpx.AsyncClient):
        def __init__(self, **kwargs):
            attempts.append(kwargs.get("proxy"))
            self._broken = bool(kwargs.get("proxy"))
            super().__init__(**kwargs)

        async def post(self, *args, **kwargs):
            if self._broken:
                raise httpx.ConnectError("proxy refused")
            return httpx.Response(
                200,
                json={"answers": {"q": {"type": "noul", "noul": 0.5}}},
                request=httpx.Request("POST", jev.SYSTEM_ONE_URL),
            )

    monkeypatch.setattr(jev.httpx, "AsyncClient", Client)
    result = await jev.ask_system_one({}, {"q": {"type": "noul"}})

    assert attempts == ["http://127.0.0.1:9", None]
    assert result["answers"] == {"q": {"type": "noul", "noul": 0.5}}
