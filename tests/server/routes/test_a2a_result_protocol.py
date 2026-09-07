from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.coordination.a2a_results import record_declared_results, recover_declared_result
from omnigent.coordination.channels import binding_for_session, labels_for_binding
from omnigent.coordination.store import CoordinationStore
from omnigent.server.routes.coordination import router
from omnigent.stores.bot_store.sqlalchemy_store import SqlAlchemyBotStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore


@pytest.fixture
def protocol(db_uri):
    bots = SqlAlchemyBotStore(db_uri)
    conversations = SqlAlchemyConversationStore(db_uri)
    store = CoordinationStore(db_uri)
    bot_a = bots.ensure_for_agent(
        owner_id="local", agent_id=uuid.uuid4().hex, name="debby", description=None
    )
    bot_b = bots.ensure_for_agent(
        owner_id="local", agent_id=uuid.uuid4().hex, name="polly", description=None
    )
    topic_a = conversations.create_conversation(
        agent_id=bot_a.agent_id, bot_id=bot_a.id, purpose="topic"
    )
    topic_b = conversations.create_conversation(
        agent_id=bot_a.agent_id, bot_id=bot_a.id, purpose="topic"
    )
    channel = conversations.create_conversation(
        agent_id=bot_b.agent_id, bot_id=bot_b.id, purpose="a2a", singleton_slot="a2a"
    )
    app = FastAPI()
    app.include_router(router)
    app.state.conversation_store = conversations
    app.state.bot_store = bots
    app.state.coordination_store = store
    app.state.permission_store = None
    with TestClient(app) as client:
        yield client, store, topic_a.id, topic_b.id, channel.id


def dispatch(client, sender, recipient):
    response = client.post(
        "/v1/coordination/messages",
        json={
            "root_session_id": sender,
            "sender_session_id": sender,
            "sender_role": "debby",
            "recipient_session_id": recipient,
            "recipient_role": "polly",
            "kind": "command",
            "intent": "task.request",
            "payload": {"prompt": "Review"},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["message"]["message_id"]


def test_out_of_order_results_return_to_original_topic_once(protocol):
    client, store, topic_a, topic_b, channel = protocol
    request_a = dispatch(client, topic_a, channel)
    request_b = dispatch(client, topic_b, channel)
    for request_id, target in [(request_b, topic_b), (request_a, topic_a)]:
        payload = {
            "root_session_id": channel,
            "sender_session_id": channel,
            "recipient_session_id": target,
            "in_reply_to": request_id,
            "intent": "task.result",
            "payload": {"summary": target},
        }
        response = client.post("/v1/coordination/messages", json=payload)
        assert response.status_code == 200, response.text
        result = response.json()["message"]
        assert result["recipient_session_id"] == target
        assert result["root_session_id"] == target
        assert result["correlation_id"] == request_id
        repeated = client.post("/v1/coordination/messages", json=payload).json()["message"]
        assert repeated["message_id"] == result["message_id"]
        assert len(store.list_outbox_items(message_id=result["message_id"])) == 1
    assert client.get(f"/v1/coordination/messages/{request_id}").json()["result"] == result


def test_scoped_channel_rejects_a_different_topic(protocol):
    client, store, topic_a, topic_b, channel = protocol
    conversations = client.app.state.conversation_store
    binding = binding_for_session(topic_a, purpose="topic")
    conversations.set_labels(channel, labels_for_binding(binding))

    first = dispatch(client, topic_a, channel)
    assert store.get_message(first).payload["a2a_channel_scope"] == binding.scope

    response = client.post(
        "/v1/coordination/messages",
        json={
            "root_session_id": topic_b,
            "sender_session_id": topic_b,
            "recipient_session_id": channel,
            "sender_role": "debby",
            "recipient_role": "polly",
            "kind": "command",
            "intent": "task.request",
            "payload": {"prompt": "Wrong Topic"},
        },
    )
    assert response.status_code == 409
    assert "different Chat or Topic" in response.json()["detail"]


def test_result_cannot_be_redirected_or_forged(protocol):
    client, _, topic_a, topic_b, channel = protocol
    request_id = dispatch(client, topic_a, channel)
    payload = {
        "root_session_id": channel,
        "sender_session_id": channel,
        "recipient_session_id": topic_b,
        "in_reply_to": request_id,
        "intent": "task.result",
        "payload": {"summary": "wrong topic"},
    }
    assert client.post("/v1/coordination/messages", json=payload).status_code == 403
    payload.update(sender_session_id=topic_b, recipient_session_id=topic_a)
    assert client.post("/v1/coordination/messages", json=payload).status_code == 403
    payload.pop("in_reply_to")
    assert client.post("/v1/coordination/messages", json=payload).status_code == 400


def test_terminal_declaration_is_correlated_and_recoverable(protocol):
    client, store, topic_a, _, channel = protocol
    request_id = dispatch(client, topic_a, channel)
    request = store.get_message(request_id)
    store.update_message_state(request_id, "active")
    assert [m.message_id for m in store.list_unanswered_a2a_requests()] == [request_id]
    assert record_declared_results(store, channel, "Children started, report later") == 0
    final = f"Verified report\n[A2A_RESULT:{request_id}:succeeded]"
    assert record_declared_results(store, topic_a, final) == 0
    user_item = SimpleNamespace(
        id="item-user",
        created_at=request.created_at + 1,
        status="completed",
        data=SimpleNamespace(role="user", content=[{"type": "input_text", "text": final}]),
    )
    assistant_item = SimpleNamespace(
        id="item-result",
        created_at=int(request.created_at),
        status="completed",
        data=SimpleNamespace(role="assistant", content=[{"type": "output_text", "text": final}]),
    )
    # The terminal hook was missed before a restart; the reconciler uses persisted items.
    fake_conversations = SimpleNamespace(
        list_items=lambda *a, **k: SimpleNamespace(
            data=[assistant_item, user_item], has_more=False
        )
    )
    recover_declared_result(store, fake_conversations, request)
    result = store.get_message_result(request_id)
    assert result.recipient_session_id == topic_a
    assert result.payload["summary"] == "Verified report"
    record_declared_results(store, channel, final)
    assert len(store.list_outbox_items(message_id=result.message_id)) == 1
    assert store.list_unanswered_a2a_requests() == []


def test_server_derives_forward_hop_and_blocks_reset(protocol):
    client, _, topic_a, _, channel = protocol
    request_id = dispatch(client, topic_a, channel)
    # A channel cannot reset a forwarding chain by omitting the parent request.
    response = client.post(
        "/v1/coordination/messages",
        json={
            "root_session_id": channel,
            "sender_session_id": channel,
            "recipient_session_id": channel,
            "kind": "command",
            "payload": {},
        },
    )
    assert response.status_code == 400
    # Existing request is loaded from storage, not a caller-supplied hop value.
    response = client.post(
        "/v1/coordination/messages",
        json={
            "root_session_id": channel,
            "sender_session_id": channel,
            "recipient_session_id": channel,
            "kind": "command",
            "in_reply_to": request_id,
            "hop_count": 0,
            "max_hops": 100,
            "payload": {},
        },
    )
    # Same-tree self-send is not a legal forward either.
    assert response.status_code == 400
    conversations = client.app.state.conversation_store
    source = conversations.get_conversation(topic_a)
    channel_a = conversations.create_conversation(
        agent_id=source.agent_id,
        bot_id=source.bot_id,
        purpose="a2a",
        singleton_slot="a2a",
    )
    sender, target = channel, channel_a.id
    for hop in range(1, 10):
        response = client.post(
            "/v1/coordination/messages",
            json={
                "root_session_id": sender,
                "sender_session_id": sender,
                "recipient_session_id": target,
                "kind": "command",
                "in_reply_to": request_id,
                "hop_count": 0,
                "max_hops": 100,
                "payload": {"prompt": str(hop)},
            },
        )
        if hop >= 8:
            assert response.status_code >= 400
            break
        assert response.status_code == 200, response.text
        saved = response.json()["message"]
        assert saved["hop_count"] == hop
        assert saved["max_hops"] == 8
        request_id = saved["message_id"]
        sender, target = target, sender
