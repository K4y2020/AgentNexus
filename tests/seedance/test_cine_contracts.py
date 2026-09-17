import base64
import hashlib
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from omnigent.seedance.bridge import execute_seedance_agent_message
from omnigent.seedance.cine_contracts import current_ledger, reviewed_shots, session_image_receipts


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "projects" / "film"
    rev = root / "revisions" / "rev1"
    write(root / "project.json", {"current_revision": "rev1"})
    write(root / "source.json", {"source_id": "source1"})
    write(
        tmp_path / ".cine/sessions/topic1.json",
        {"session_id": "topic1", "project_path": str(root)},
    )
    interval = {"in_pts": 0, "out_pts": 24, "time_base_num": 1, "time_base_den": 24}
    write(
        rev / "source_shots.json",
        [
            {
                "shot_id": "S01",
                "source_id": "source1",
                "revision_id": "rev1",
                "interval": interval,
                "evidence_ids": ["ev1"],
                "verification": {"visual": "unreviewed"},
            }
        ],
    )
    (rev / "image.jpg").write_bytes(b"evidence")
    receipt = {
        "evidence_id": "ev1",
        "source_id": "source1",
        "revision_id": "rev1",
        "sha256": hashlib.sha256(b"evidence").hexdigest(),
        "source_interval": interval,
    }
    write(
        rev / "evidence_index.json",
        [{**receipt, "relative_path": "image.jpg", "extraction_status": "ok"}],
    )
    review = {
        "shot_id": "S01",
        "source_id": "source1",
        "revision_id": "rev1",
        "status": "model_reviewed",
        "observations": ["Visible figure"],
        "image_receipt_ids": ["r1"],
    }
    write(root / "reviews/rev1.json", [review])
    return tmp_path, root, rev, receipt, review


def test_only_current_session_and_revision_are_used(project):
    workspace, root, rev, receipt, _ = project
    write(
        root / "revisions/old/source_shots.json", [{"shot_id": "S01", "observations": ["wrong"]}]
    )
    result = reviewed_shots(workspace, "topic1", ["S01"], {"r1": receipt})
    assert result[0]["observations"] == ["Visible figure"]
    assert current_ledger(workspace, "topic1") == rev / "source_shots.json"
    with pytest.raises(FileNotFoundError):
        current_ledger(workspace, "topic2")


def test_delivery_does_not_mark_a_shot_reviewed(project):
    workspace, root, _, receipt, review = project
    review["status"] = "unreviewed"
    write(root / "reviews/rev1.json", [review])
    with pytest.raises(ValueError, match="VISUAL_REVIEW_REQUIRED"):
        reviewed_shots(workspace, "topic1", ["S01"], {"r1": receipt})


def test_missing_receipts_and_changed_evidence_block(project):
    workspace, _, rev, receipt, _ = project
    with pytest.raises(ValueError, match="RECEIPT_NOT_IN_CURRENT_SESSION"):
        reviewed_shots(workspace, "topic1", ["S01"], {})
    (rev / "image.jpg").write_bytes(b"changed")
    with pytest.raises(ValueError, match="EVIDENCE_CHANGED"):
        reviewed_shots(workspace, "topic1", ["S01"], {"r1": receipt})


def test_stale_review_or_missing_shot_blocks(project):
    workspace, root, _, receipt, review = project
    with pytest.raises(ValueError, match="SHOT_MISSING"):
        reviewed_shots(workspace, "topic1", ["absent"], {"r1": receipt})
    review["revision_id"] = "old"
    write(root / "reviews/rev1.json", [review])
    with pytest.raises(ValueError, match="REVIEW_REVISION_MISMATCH"):
        reviewed_shots(workspace, "topic1", ["S01"], {"r1": receipt})


async def test_receipts_only_come_from_image_tool_history():
    output = json.dumps(
        {"metadata": {"receipt_id": "r1"}, "image": {"type": "image", "source": {"data": "x"}}}
    )
    client = AsyncMock()
    client.get.return_value = httpx.Response(
        200,
        request=httpx.Request("GET", "http://test"),
        json={
            "data": [
                {"type": "function_call", "name": "sys_os_shell", "call_id": "c"},
                {"id": "o", "type": "function_call_output", "call_id": "c", "output": output},
            ],
            "has_more": False,
        },
    )
    assert await session_image_receipts(client, "topic1") == {}


async def test_missing_contract_blocks_before_v3_creation(tmp_path):
    client = AsyncMock()
    client.get.return_value = httpx.Response(
        200, request=httpx.Request("GET", "http://test"), json={"data": [], "has_more": False}
    )
    v3 = AsyncMock()
    result = await execute_seedance_agent_message(
        client, "topic1", "draft", shot_ids=["S01"], workspace_dir=tmp_path, seedance_client=v3
    )
    assert result["error_code"] == "CINE_AGENT_DELEGATION_DISABLED"
    v3.create_project.assert_not_called()
    v3.send_agent_message.assert_not_called()


def image_output():
    data = b"evidence"
    return json.dumps(
        {
            "metadata": {"receipt_id": "receipt", "sha256": hashlib.sha256(data).hexdigest()},
            "image": {"type": "image", "source": {"data": base64.b64encode(data).decode()}},
        }
    )


def structured_image_output():
    data = b"evidence"
    return json.dumps(
        [
            {
                "type": "text",
                "text": json.dumps(
                    {"receipt_id": "receipt", "sha256": hashlib.sha256(data).hexdigest()}
                ),
            },
            {
                "type": "image",
                "image_url": f"data:image/png;base64,{base64.b64encode(data).decode()}",
            },
        ]
    )


async def test_openai_structured_image_output_yields_receipt():
    client = AsyncMock()
    client.get.return_value = httpx.Response(
        200,
        request=httpx.Request("GET", "http://test"),
        json={
            "data": [
                {"type": "function_call", "call_id": "image", "name": "sys_os_view_image"},
                {
                    "id": "out",
                    "type": "function_call_output",
                    "call_id": "image",
                    "output": structured_image_output(),
                },
            ],
            "has_more": False,
        },
    )
    assert "receipt" in await session_image_receipts(client, "topic1")


@pytest.mark.parametrize("overlap", [False, True])
async def test_reused_call_ids_are_paired_not_globally_merged(overlap):
    first = {"type": "function_call", "call_id": "same", "name": "sys_os_read", "response_id": "r"}
    first_output = {
        "id": "out1",
        "type": "function_call_output",
        "call_id": "same",
        "response_id": "r",
        "output": "text",
    }
    second = {
        "type": "function_call",
        "call_id": "same",
        "name": "sys_os_view_image",
        "response_id": "r",
    }
    second_output = {
        "id": "out2",
        "type": "function_call_output",
        "call_id": "same",
        "response_id": "r",
        "output": image_output(),
    }
    items = (
        [first, second, first_output, second_output]
        if overlap
        else [first, first_output, second, second_output]
    )
    client = AsyncMock()
    client.get.return_value = httpx.Response(
        200, request=httpx.Request("GET", "http://test"), json={"data": items, "has_more": False}
    )
    result = await session_image_receipts(client, "topic1")
    assert bool(result) is not overlap


async def test_shell_cannot_reuse_image_call_id_to_forge_receipt():
    client = AsyncMock()
    client.get.return_value = httpx.Response(
        200,
        request=httpx.Request("GET", "http://test"),
        json={
            "data": [
                {"type": "function_call", "call_id": "same", "name": "sys_os_view_image"},
                {
                    "id": "out1",
                    "type": "function_call_output",
                    "call_id": "same",
                    "output": "error",
                },
                {"type": "function_call", "call_id": "same", "name": "sys_os_shell"},
                {
                    "id": "out2",
                    "type": "function_call_output",
                    "call_id": "same",
                    "output": image_output(),
                },
            ],
            "has_more": False,
        },
    )
    assert await session_image_receipts(client, "topic1") == {}
