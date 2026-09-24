"""Read bounded image payloads through the session OS environment."""

import base64
import hashlib
import io
import json
import uuid
from pathlib import Path
from typing import Any

from PIL import Image

MAX_IMAGE_BYTES = 5 * 1024 * 1024


def image_instructions(schemas: list[dict[str, Any]]) -> tuple[str, ...]:
    if not any(s.get("function", s).get("name") == "sys_os_view_image" for s in schemas):
        return ()
    return (
        "For visual analysis of a local image, use sys_os_view_image and inspect its actual "
        "image block before describing it. A saved path, shell success, pixel statistics, "
        "face count, ASCII art or failed browser call is not visual evidence. If no image "
        "arrives, report visual review blocked; do not guess or mark the image reviewed. "
        "Reference the inspected path and source timestamp when reviewing extracted video "
        "frames. A still frame alone does not establish motion, speaker identity or dialogue.",
        "For Cine evidence, supply evidence_index and evidence_id with the image path. "
        "Keep the receipt ID with observations in the revision's review records. "
        "Image preparation does not mark a shot reviewed or prove factual correctness.",
    )


async def read_image(
    os_env: Any,
    path: str,
    *,
    evidence_index: str | None = None,
    evidence_id: str | None = None,
) -> dict[str, Any]:
    result = await os_env.read(path=path, max_binary_bytes=MAX_IMAGE_BYTES)
    if result.get("error"):
        return {"error": result["error"], "visual_input_available": False}
    try:
        if result.get("truncated"):
            raise ValueError("Image exceeds 5 MiB; create a smaller evidence frame first")
        if result.get("encoding") != "base64" or not result.get("content"):
            raise ValueError("Not a supported binary image")
        raw = base64.b64decode(result["content"], validate=True)
        with Image.open(io.BytesIO(raw)) as img:
            mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(
                img.format or ""
            )
            if mime is None or getattr(img, "n_frames", 1) != 1:
                raise ValueError("Use a single-frame JPEG, PNG or WebP")
            width, height = img.size
            if width * height > 20_000_000:
                raise ValueError("Image exceeds 20 megapixels; create a smaller evidence frame")
            img.verify()
        digest = hashlib.sha256(raw).hexdigest()
        evidence = {}
        if evidence_index or evidence_id:
            if not evidence_index or not evidence_id:
                raise ValueError("evidence_index and evidence_id must be supplied together")
            index = await os_env.read(path=evidence_index, limit=None)
            if index.get("error") or index.get("truncated") or index.get("encoding") != "utf-8":
                raise ValueError("Evidence index is unavailable or truncated")
            records = json.loads(index["content"])
            if not isinstance(records, list):
                raise ValueError("Evidence index must be a list")
            matches = [
                e for e in records if isinstance(e, dict) and e.get("evidence_id") == evidence_id
            ]
            if len(matches) != 1:
                raise ValueError("Evidence identifier is missing or ambiguous")
            record = matches[0]
            if record.get("extraction_status") != "ok" or record.get("sha256") != digest:
                raise ValueError("Evidence extraction failed or image hash changed")
            parent = Path(index["path"]).parent.resolve()
            relative = record.get("relative_path")
            if not isinstance(relative, str):
                raise ValueError("Evidence path is missing")
            expected = (parent / relative).resolve()
            if not expected.is_relative_to(parent) or expected != Path(result["path"]).resolve():
                raise ValueError("Image does not match the evidence index path")
            if not all(record.get(k) for k in ("source_id", "revision_id", "source_interval")):
                raise ValueError("Evidence source, revision or timestamp is missing")
            evidence = {
                k: record[k]
                for k in ("source_id", "revision_id", "evidence_id", "source_interval")
            }
        return {
            "metadata": {
                **evidence,
                "receipt_id": uuid.uuid4().hex,
                "transport_status": "image_prepared",
                "semantic_review_status": "unreviewed",
                "path": result.get("path", path),
                "width": width,
                "height": height,
                "bytes": len(raw),
                "sha256": digest,
                "mime_type": mime,
                "resized": False,
            },
            "image": {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime,
                    "data": base64.b64encode(raw).decode("ascii"),
                },
            },
        }
    except (ValueError, OSError, Image.DecompressionBombError) as exc:
        return {"error": str(exc), "visual_input_available": False}


def image_mcp_response(result: dict[str, Any]) -> dict[str, Any]:
    """Only the dedicated image tool promotes bytes to image content."""
    source = result.get("image", {}).get("source", {})
    if result.get("error") or not source.get("data"):
        return {
            "isError": True,
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "error": result.get("error", "Image payload missing"),
                            "visual_input_available": False,
                        }
                    ),
                }
            ],
        }
    return {
        "content": [
            {"type": "text", "text": json.dumps(result.get("metadata", {}))},
            {"type": "image", "mimeType": source["media_type"], "data": source["data"]},
        ]
    }
