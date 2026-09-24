"""Export one successful V3 media job without filesystem searches or overwrites."""

import hashlib
import io
import os
import re
import tempfile
from pathlib import Path

from PIL import Image

from agentnexus.runtime.image_tool import MAX_IMAGE_BYTES

from .production_gate import ProductionRejected

MAX_VIDEO_BYTES = 512 * 1024 * 1024


async def export_job_image(
    server, session_id, client, *, job_id, output_path=None, output_index=0
):
    response = await server.get(f"/v1/sessions/{session_id}", timeout=10)
    response.raise_for_status()
    session = response.json()
    project = (session.get("labels") or {}).get("seedance.project_id")
    if not project or not job_id:
        raise ProductionRejected("CINE_JOB_ID_REQUIRED")
    if type(output_index) is not int or output_index < 0:
        raise ProductionRejected("CINE_OUTPUT_INDEX_INVALID")
    job = await client.get_job(job_id)
    if job.get("projectId") != project:
        raise ProductionRejected("CINE_PROJECT_BINDING_MISMATCH")
    kind = job.get("kind")
    if kind not in ("image", "video") or job.get("status") != "succeeded":
        raise ProductionRejected("CINE_MEDIA_JOB_NOT_SUCCEEDED")
    refs = job.get("outputRefs") or [job.get("outputRef")]
    if not isinstance(refs, list) or output_index >= len(refs):
        raise ProductionRejected("CINE_OUTPUT_INDEX_INVALID")
    storage = refs[output_index]
    if not isinstance(storage, str) or not re.fullmatch(r"local://[a-f0-9]{64}", storage):
        raise ProductionRejected("CINE_MEDIA_STORAGE_UNSUPPORTED")
    root = Path(session["workspace"]).resolve(strict=True)
    destination = None
    if output_path is not None:
        if not isinstance(output_path, str) or not output_path.strip():
            raise ProductionRejected("CINE_OUTPUT_PATH_INVALID")
        destination = (root / output_path).resolve()
        if not destination.is_relative_to(root):
            raise ProductionRejected("CINE_PRODUCTION_PATH_ESCAPE")
        if any(
            part.is_reserved() or any(c in part.name for c in '<>:"|?*')
            for part in [Path(p) for p in destination.relative_to(root).parts]
        ):
            raise ProductionRejected("CINE_OUTPUT_PATH_INVALID")
    raw = await client.get_local_image(
        storage, MAX_IMAGE_BYTES if kind == "image" else MAX_VIDEO_BYTES
    )
    digest = hashlib.sha256(raw).hexdigest()
    if digest != storage.removeprefix("local://"):
        raise ProductionRejected("CINE_MEDIA_HASH_MISMATCH")
    width = height = None
    if kind == "image":
        with Image.open(io.BytesIO(raw)) as image:
            extension = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}.get(image.format)
            if (
                not extension
                or getattr(image, "n_frames", 1) != 1
                or image.width * image.height > 20_000_000
            ):
                raise ProductionRejected("CINE_IMAGE_FORMAT_UNSUPPORTED")
            width, height = image.size
            image.verify()
    elif len(raw) >= 12 and raw[4:8] == b"ftyp":
        extension = ".mp4"
    elif raw.startswith(b"\x1a\x45\xdf\xa3"):
        extension = ".webm"
    else:
        raise ProductionRejected("CINE_VIDEO_FORMAT_UNSUPPORTED")
    destination = destination or root / "outputs" / "v3-media" / f"{digest}{extension}"
    if destination.suffix.lower() not in (
        {".jpg", ".jpeg"} if extension == ".jpg" else {extension}
    ):
        raise ProductionRejected("CINE_MEDIA_EXTENSION_MISMATCH", {"expected": extension})
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.resolve().is_relative_to(root):
        raise ProductionRejected("CINE_PRODUCTION_PATH_ESCAPE")
    if destination.exists():
        if (
            not destination.is_file()
            or hashlib.sha256(destination.read_bytes()).hexdigest() != digest
        ):
            raise ProductionRejected("CINE_OUTPUT_ALREADY_EXISTS")
    else:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as file:
            temp = Path(file.name)
            file.write(raw)
        try:
            os.link(temp, destination)  # Publish atomically without replacing existing work.
        finally:
            temp.unlink(missing_ok=True)
    return {
        "status": "exported",
        "path": str(destination),
        "sha256": digest,
        "width": width,
        "height": height,
        "bytes": len(raw),
        "job_id": job_id,
        "generation_submitted": False,
        "media_kind": kind,
        "visual_review_status": "unreviewed",
        "next_step": (
            "Use sys_os_view_image with this exact path, review once, then report. "
            "Do not search directories or redownload through shell."
        ),
    }
