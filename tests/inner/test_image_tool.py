import base64
import io
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from agentnexus.runtime.image_tool import MAX_IMAGE_BYTES, image_mcp_response, read_image


def image_result():
    buf = io.BytesIO()
    Image.new("RGB", (128, 54), "red").save(buf, format="PNG")
    return {
        "path": "frame.png",
        "encoding": "base64",
        "truncated": False,
        "content": base64.b64encode(buf.getvalue()).decode("ascii"),
    }


async def test_sdk_receives_image_not_base64_text():
    from agentnexus.inner.claude_sdk_executor import _build_mcp_tools

    env = AsyncMock()
    env.read.return_value = image_result()
    result = await read_image(env, "frame.png")
    env.read.assert_awaited_once_with(path="frame.png", max_binary_bytes=MAX_IMAGE_BYTES)
    executor = AsyncMock(return_value=result)
    tools = _build_mcp_tools(
        [
            {
                "name": "sys_os_view_image",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            }
        ],
        executor,
    )
    response = await tools[0].handler({"path": "frame.png"})
    assert [b["type"] for b in response["content"]] == ["text", "image"]
    assert response["content"][1]["mimeType"] == "image/png"
    assert response["content"][1]["data"] == image_result()["content"]
    metadata = json.loads(response["content"][0]["text"])
    assert (metadata["width"], metadata["height"]) == (128, 54)
    assert len(metadata["sha256"]) == 64


@pytest.mark.parametrize(
    "payload",
    [
        {"error": "Access denied"},
        {"encoding": "utf-8", "content": "not an image"},
        {"encoding": "base64", "content": "bad base64"},
        {"encoding": "base64", "content": "bm90IGFuIGltYWdl"},
        {"encoding": "base64", "truncated": True, "content": "YWJj"},
    ],
)
async def test_invalid_images_fail_closed(payload):
    env = AsyncMock()
    env.read.return_value = payload
    result = await read_image(env, "frame.png")
    assert result["visual_input_available"] is False
    response = image_mcp_response(result)
    assert response["isError"] is True
    assert all(block["type"] == "text" for block in response["content"])


def test_missing_payload_is_not_success():
    assert image_mcp_response({"metadata": {"width": 128}})["isError"] is True


def test_visual_guidance_is_capability_gated():
    from agentnexus.runtime.image_tool import image_instructions

    assert image_instructions([]) == ()
    assert "blocked" in image_instructions([{"name": "sys_os_view_image"}])[0]
    assert image_instructions([{"name": "sys_os_view_image"}]) == image_instructions(
        [{"function": {"name": "sys_os_view_image"}}]
    )


async def test_real_os_environment_reads_image(tmp_path):
    from agentnexus.inner.datamodel import OSEnvSandboxSpec, OSEnvSpec
    from agentnexus.inner.os_env import create_os_environment

    (tmp_path / "frame.png").write_bytes(base64.b64decode(image_result()["content"]))
    env = create_os_environment(
        OSEnvSpec(
            type="caller_process",
            cwd=str(tmp_path),
            sandbox=OSEnvSandboxSpec(type="none"),
        )
    )
    try:
        result = await read_image(env, "frame.png")
        assert result["metadata"]["width"] == 128
        assert image_mcp_response(result)["content"][1]["type"] == "image"
    finally:
        env.close()


async def test_canonical_evidence_link_is_verified():
    import hashlib

    env = AsyncMock()
    image = image_result()
    image["path"] = str(Path("revision/frame.png").resolve())
    index_path = str(Path("revision/evidence_index.json").resolve())
    record = {
        "evidence_id": "ev1",
        "source_id": "src1",
        "revision_id": "rev1",
        "relative_path": "frame.png",
        "source_interval": {"in_pts": 0, "out_pts": 1},
        "extraction_status": "ok",
        "sha256": hashlib.sha256(base64.b64decode(image["content"])).hexdigest(),
    }
    env.read.side_effect = [
        image,
        {"path": index_path, "encoding": "utf-8", "content": json.dumps([record])},
    ]
    result = await read_image(env, image["path"], evidence_index=index_path, evidence_id="ev1")
    assert result["metadata"]["evidence_id"] == "ev1"
    assert result["metadata"]["semantic_review_status"] == "unreviewed"
    assert result["metadata"]["source_interval"] == record["source_interval"]
    record["sha256"] = "changed"
    env.read.side_effect = [
        image,
        {"path": index_path, "encoding": "utf-8", "content": json.dumps([record])},
    ]
    invalid = await read_image(env, image["path"], evidence_index=index_path, evidence_id="ev1")
    assert invalid["visual_input_available"] is False
