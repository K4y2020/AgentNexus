import httpx
import pytest

from omnigent.runner.model_selection import restore_turn_model


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["gemini-3.8-flash-high", "deepseek-v4-flash", None])
async def test_background_wake_restores_current_saved_selection(model):
    async with httpx.AsyncClient(
        base_url="http://server",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"model_override": model}),
        ),
    ) as client:
        original = {"agent_id": "debby"}
        resolved = await restore_turn_model(original, "topic", client)
    assert resolved["model_override"] == model
    assert "model_override" not in original


@pytest.mark.asyncio
async def test_explicit_model_is_not_replaced():
    def unexpected(_):
        raise AssertionError("Explicit turn model requires no fallback request")

    async with httpx.AsyncClient(
        base_url="http://server",
        transport=httpx.MockTransport(unexpected),
    ) as client:
        body = {"model_override": "deepseek-v4-flash"}
        assert await restore_turn_model(body, "topic", client) is body


@pytest.mark.asyncio
@pytest.mark.parametrize("status,payload", [(503, {}), (200, {}), (200, {"model_override": 4})])
async def test_unreadable_selection_never_falls_back(status, payload):
    async with httpx.AsyncClient(
        base_url="http://server",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, json=payload),
        ),
    ) as client:
        with pytest.raises((httpx.HTTPStatusError, ValueError)):
            await restore_turn_model({}, "topic", client)


@pytest.mark.asyncio
async def test_a2a_background_uses_own_bot_not_first_roster_entry():
    def handle(request):
        if request.url.path == "/v1/bots":
            return httpx.Response(
                200,
                json={
                    "bots": [
                        {"bot": {"id": "other", "default_model": "gpt-5.6-sol"}},
                        {"bot": {"id": "polly", "default_model": "gemini-3.8-flash-high"}},
                    ]
                },
            )
        return httpx.Response(200, json={"model_override": None, "bot_id": "polly"})

    async with httpx.AsyncClient(
        base_url="http://server", transport=httpx.MockTransport(handle)
    ) as client:
        result = await restore_turn_model({}, "a2a", client)
    assert result["model_override"] == "gemini-3.8-flash-high"


@pytest.mark.asyncio
async def test_child_background_uses_current_worker_binding():
    def handle(request):
        if request.url.path.endswith("/labels"):
            return httpx.Response(
                200, json={"labels": {"subagent.model.gpt": "deepseek-v4-flash"}}
            )
        return httpx.Response(
            200,
            json={
                "model_override": "gpt-5.6-sol",
                "parent_session_id": "topic",
                "sub_agent_name": "gpt",
            },
        )

    async with httpx.AsyncClient(
        base_url="http://server", transport=httpx.MockTransport(handle)
    ) as client:
        result = await restore_turn_model({}, "child", client)
    assert result["model_override"] == "deepseek-v4-flash"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["gemini-3.8-flash-high", "deepseek-v4-flash"])
async def test_background_model_reaches_harness_request(model):
    from omnigent.runner.app import create_runner_app
    from tests.runner.test_runner_dispatch import (
        _INSTRUCTION_WARN_CHUNKS,
        _contract_resolver_for,
        _contract_run_background,
        _ContractSnapshotClient,
        _FakeProcessManager,
        _RecordingHarnessClient,
        _runner_test_client,
    )

    class Snapshot(_ContractSnapshotClient):
        async def get(self, url, **kwargs):
            if url.endswith("/v1/sessions/model-wake"):
                return httpx.Response(
                    200,
                    request=httpx.Request("GET", "http://server" + url),
                    json={
                        "agent_id": "ag_contract_root",
                        "sub_agent_name": "worker",
                        "model_override": model,
                    },
                )
            return await super().get(url, **kwargs)

    recording = _RecordingHarnessClient(_INSTRUCTION_WARN_CHUNKS)
    app = create_runner_app(
        process_manager=_FakeProcessManager(recording),
        spec_resolver=_contract_resolver_for("child_present", []),
        server_client=Snapshot("model-wake"),
    )
    async with _runner_test_client(app) as client:
        await _contract_run_background(client, "model-wake", recording)
    assert recording.posted_bodies
    assert recording.posted_bodies[-1]["model_override"] == model
