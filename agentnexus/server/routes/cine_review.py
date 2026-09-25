"""Owner-scoped report reads and range streaming through the existing Host tunnel."""

import base64
import re

from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from agentnexus.cine_review import CHUNK_BYTES
from agentnexus.server.auth import LEVEL_OWNER


def media_range(value: str | None, size: int) -> tuple[int, int, int]:
    if not value:
        return 0, size - 1, 200
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
    if match and size > 0 and any(match.groups()):
        left, right = match.groups()
        start = int(left) if left else max(0, size - int(right))
        end = min(int(right), size - 1) if left and right else size - 1
        if 0 <= start <= end < size:
            return start, end, 206
    raise HTTPException(
        416, "Unsupported media range", headers={"Content-Range": f"bytes */{size}"}
    )


def register_cine_review_routes(router, validate_session, read_host):
    async def read(request, session_id, params):
        conv = await validate_session(session_id, request, LEVEL_OWNER)
        result = await read_host(session_id, conv, "cine_review", params)
        if result is None:
            raise HTTPException(503, "Cine workspace host is offline")
        return result

    @router.get("/sessions/{session_id}/cine-review")
    async def report(request: Request, session_id: str):
        production = request.query_params.get("production")
        return await read(
            request,
            session_id,
            {"production": production} if production else {},
        )

    # Separate routes give GET and HEAD distinct, stable OpenAPI operation ids.
    @router.get("/sessions/{session_id}/cine-review/assets/{asset}")
    @router.head("/sessions/{session_id}/cine-review/assets/{asset}")
    async def media(request: Request, session_id: str, asset: str, token: str):
        params = {"asset": asset, "token": token}
        info = await read(request, session_id, params)
        etag = f'"{info["etag"]}"'
        requested_range = request.headers.get("range")
        if request.headers.get("if-range") not in (None, etag):
            requested_range = None
        start, end, status = media_range(requested_range, info["size"])
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
            "ETag": etag,
            "Cache-Control": "private, no-cache",
            "X-Content-Type-Options": "nosniff",
        }
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{info['size']}"
        if request.method == "HEAD":
            return Response(status_code=status, headers=headers, media_type=info["media_type"])

        async def chunks():
            offset = start
            while offset <= end:
                if await request.is_disconnected():
                    break
                part = await read(
                    request,
                    session_id,
                    {
                        **params,
                        "offset": offset,
                        "length": min(CHUNK_BYTES, end - offset + 1),
                        "etag": info["etag"],
                    },
                )
                raw = base64.b64decode(part["content"], validate=True)
                if not raw:
                    raise RuntimeError("Cine media truncated during streaming")
                offset += len(raw)
                yield raw

        return StreamingResponse(
            chunks(), status_code=status, headers=headers, media_type=info["media_type"]
        )
