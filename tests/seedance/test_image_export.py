import hashlib
import io
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from PIL import Image

from agentnexus.seedance.image_export import export_job_image
from agentnexus.seedance.production_gate import ProductionRejected


@pytest.mark.asyncio
@respx.mock
async def test_export_verified_image_and_reuse_without_overwrite(tmp_path):
    out = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(out, "PNG")
    raw = out.getvalue()
    sha = hashlib.sha256(raw).hexdigest()
    respx.get("http://server/v1/sessions/topic").respond(
        200,
        json={
            "workspace": str(tmp_path),
            "labels": {"seedance.project_id": "project"},
        },
    )
    v3 = AsyncMock()
    v3.get_job.return_value = {
        "projectId": "project",
        "kind": "image",
        "status": "succeeded",
        "outputRef": f"local://{sha}",
    }
    v3.get_local_media.return_value = raw
    async with httpx.AsyncClient(base_url="http://server") as server:
        result = await export_job_image(
            server, "topic", v3, job_id="job", output_path="outputs/frame.png"
        )
        assert Path(result["path"]).read_bytes() == raw
        assert result["visual_review_status"] == "unreviewed"
        assert result["generation_submitted"] is False
        assert (
            await export_job_image(
                server, "topic", v3, job_id="job", output_path="outputs/frame.png"
            )
        )["sha256"] == sha
        Path(result["path"]).write_bytes(b"user file")
        with pytest.raises(ProductionRejected, match="CINE_OUTPUT_ALREADY_EXISTS"):
            await export_job_image(
                server, "topic", v3, job_id="job", output_path="outputs/frame.png"
            )
        assert Path(result["path"]).read_bytes() == b"user file"
        with pytest.raises(ProductionRejected, match="CINE_PRODUCTION_PATH_ESCAPE"):
            await export_job_image(server, "topic", v3, job_id="job", output_path="../escape.png")
        v3.get_job.return_value["projectId"] = "other"
        with pytest.raises(ProductionRejected, match="CINE_PROJECT_BINDING_MISMATCH"):
            await export_job_image(server, "topic", v3, job_id="job")
    v3.submit_command.assert_not_called()


@pytest.mark.asyncio
@respx.mock
async def test_export_verified_video(tmp_path):
    raw = b"\x00\x00\x00\x18ftypisom" + b"video-data"
    sha = hashlib.sha256(raw).hexdigest()
    respx.get("http://server/v1/sessions/topic").respond(
        200,
        json={"workspace": str(tmp_path), "labels": {"seedance.project_id": "project"}},
    )
    v3 = AsyncMock()
    v3.get_job.return_value = {
        "projectId": "project",
        "kind": "video",
        "status": "succeeded",
        "outputRef": f"local://{sha}",
    }
    v3.get_local_media.return_value = raw
    async with httpx.AsyncClient(base_url="http://server") as server:
        result = await export_job_image(
            server, "topic", v3, job_id="job", output_path="outputs/clip.mp4"
        )
    assert Path(result["path"]).read_bytes() == raw
    assert result["media_kind"] == "video"
    assert result["width"] is None
    assert result["height"] is None


@pytest.mark.asyncio
@respx.mock
async def test_explicit_video_export_is_kind_checked_and_stays_unreviewed(tmp_path):
    from agentnexus.seedance.bridge import execute_seedance_canvas_edit

    raw = b"\x00\x00\x00\x18ftypisom" + b"video-data"
    sha = hashlib.sha256(raw).hexdigest()
    respx.get("http://server/v1/sessions/topic").respond(
        200,
        json={"workspace": str(tmp_path), "labels": {"seedance.project_id": "project"}},
    )
    v3 = AsyncMock()
    v3.get_job.return_value = {
        "projectId": "project",
        "kind": "video",
        "status": "succeeded",
        "outputRef": f"local://{sha}",
    }
    v3.get_local_media.return_value = raw
    async with httpx.AsyncClient(base_url="http://server") as server:
        exported = await execute_seedance_canvas_edit(
            server,
            "topic",
            "export_video",
            job_id="video-job",
            seedance_client=v3,
        )
        assert exported["media_kind"] == "video"
        assert exported["visual_review_status"] == "unreviewed"
        assert "audio" in exported["next_step"]
        assert "sys_os_view_image" not in exported["next_step"]
        assert Path(exported["path"]).read_bytes() == raw
        v3.get_job.return_value["kind"] = "image"
        rejected = await execute_seedance_canvas_edit(
            server,
            "topic",
            "export_video",
            job_id="video-job",
            seedance_client=v3,
        )
        assert rejected["error_code"] == "CINE_MEDIA_KIND_MISMATCH"
    v3.submit_command.assert_not_called()
