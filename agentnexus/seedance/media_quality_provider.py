"""Read complete local videos through an explicitly configured native Gemini endpoint."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

MAX_REQUEST_BYTES = 20_000_000
REVIEW_FPS = 5.0
MAX_DIMENSION = 720


class MediaReviewUnavailable(RuntimeError):
    """A review could not be performed; this is never a passing quality verdict."""

    def __init__(self, code: str, detail: str):
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}")


def native_endpoint(base_url: str, model: str) -> str:
    """Keep the configured origin and proxy prefix when selecting the native API."""
    if not isinstance(base_url, str) or not isinstance(model, str):
        raise MediaReviewUnavailable("CONFIG_INVALID", "A base URL and model are required")
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise MediaReviewUnavailable("CONFIG_INVALID", "Use an HTTP API base without credentials")
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3] + "/v1beta"
    elif not path:
        path = "/v1beta"
    elif not path.endswith("/v1beta"):
        raise MediaReviewUnavailable("CONFIG_INVALID", "API base must end in /v1 or /v1beta")
    name = model.removeprefix("models/")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise MediaReviewUnavailable("CONFIG_INVALID", "Use a single native model identifier")
    return urlunsplit(
        (parsed.scheme, parsed.netloc, f"{path}/models/{name}:generateContent", "", "")
    )


def _settings(settings: tuple[str, str, str]) -> tuple[str, str, str]:
    if (
        not isinstance(settings, tuple)
        or len(settings) != 3
        or not isinstance(settings[1], str)
        or not settings[1].strip()
    ):
        raise MediaReviewUnavailable(
            "CONFIG_INVALID", "Configured provider credentials are required"
        )
    base, key, model = settings
    return native_endpoint(base, model), key, model.removeprefix("models/")


async def _request(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> dict:
    try:
        response = await client.request(method, url, follow_redirects=False, **kwargs)
    except httpx.HTTPError as exc:
        # Exception strings and response bodies can contain credentials or uploaded media.
        raise MediaReviewUnavailable("TRANSPORT_FAILED", type(exc).__name__) from None
    if response.status_code != 200:
        raise MediaReviewUnavailable(
            "HTTP_FAILED", f"Provider returned HTTP {response.status_code}"
        )
    try:
        body = response.json()
    except ValueError:
        raise MediaReviewUnavailable("RESPONSE_INVALID", "Provider response is not JSON") from None
    if not isinstance(body, dict):
        raise MediaReviewUnavailable("RESPONSE_INVALID", "Provider response must be an object")
    return body


async def probe_capabilities(
    settings: tuple[str, str, str], client: httpx.AsyncClient | None = None
) -> dict:
    """Require the exact model's declared video/audio support using read-only requests."""
    endpoint, key, model = _settings(settings)
    if client is None:
        async with httpx.AsyncClient(trust_env=False) as owned:
            return await probe_capabilities(settings, owned)
    url = endpoint.rsplit("/models/", 1)[0] + "/models"
    token = None
    seen = set()
    for _ in range(20):
        body = await _request(
            client,
            "GET",
            url,
            headers={"x-goog-api-key": key},
            params={"pageToken": token} if token else None,
            timeout=20,
        )
        rows = body.get("models")
        if not isinstance(rows, list):
            raise MediaReviewUnavailable("CAPABILITIES_UNAVAILABLE", "No native model metadata")
        for row in rows:
            if not isinstance(row, dict) or row.get("name") not in {model, "models/" + model}:
                continue
            modalities = row.get("supportedInputModalities")
            methods = row.get("supportedGenerationMethods")
            if (
                not isinstance(modalities, list)
                or not all(isinstance(value, str) for value in modalities)
                or not {"video", "audio"}.issubset({value.lower() for value in modalities})
                or not isinstance(methods, list)
                or "generateContent" not in methods
            ):
                raise MediaReviewUnavailable(
                    "CAPABILITIES_UNAVAILABLE",
                    "Model must declare video, audio and generateContent",
                )
            return {
                "model": model,
                "supported_input_modalities": modalities,
                "supported_generation_methods": methods,
                "endpoint": endpoint,
            }
        token = body.get("nextPageToken")
        if not isinstance(token, str) or not token or token in seen:
            break
        seen.add(token)
    raise MediaReviewUnavailable(
        "MODEL_UNAVAILABLE", "Requested model is absent from native metadata"
    )


def _digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _run(command: list[str], timeout: float) -> bytes:
    try:
        result = subprocess.run(
            command, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaReviewUnavailable("MEDIA_TOOL_FAILED", type(exc).__name__) from None
    if result.returncode:
        raise MediaReviewUnavailable(
            "MEDIA_TOOL_FAILED", f"{Path(command[0]).name} returned {result.returncode}"
        )
    return result.stdout


def _number(value) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def probe_video(path: Path) -> dict:
    """Inspect local stream durations and audio presence without reading model credentials."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise MediaReviewUnavailable("MEDIA_TOOL_MISSING", "ffprobe is required")
    try:
        body = json.loads(
            _run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(path),
                ],
                60,
            )
        )
        streams = body.get("streams", [])
        video = [row for row in streams if row.get("codec_type") == "video"]
        audio = [row for row in streams if row.get("codec_type") == "audio"]
        format_data = body.get("format") or {}
        duration = _number(format_data.get("duration"))
        origin = _number(format_data.get("start_time")) or 0.0
        if len(video) != 1 or not duration or duration <= 0:
            raise MediaReviewUnavailable(
                "VIDEO_REQUIRED", "One finite-duration video stream is required"
            )
        if len(audio) > 1:
            raise MediaReviewUnavailable(
                "AUDIO_AMBIGUOUS", "Select the intended soundtrack before review"
            )
        if not video[0].get("width") or not video[0].get("height"):
            raise MediaReviewUnavailable("VIDEO_REQUIRED", "Video dimensions are unavailable")
        timing = {}
        for kind, rows in (("video", video), ("audio", audio)):
            if rows:
                start = _number(rows[0].get("start_time"))
                span = _number(rows[0].get("duration"))
                timing[kind] = {
                    "start_seconds": start - origin if start is not None else None,
                    "duration_seconds": span,
                }
        return {
            "duration_seconds": duration,
            "has_video": True,
            "has_audio": bool(audio),
            "video_streams": len(video),
            "audio_streams": len(audio),
            "width": video[0]["width"],
            "height": video[0]["height"],
            "container": format_data.get("format_name", ""),
            "stream_timing": timing,
        }
    except (ValueError, TypeError, KeyError, AttributeError):
        raise MediaReviewUnavailable(
            "MEDIA_INVALID", "ffprobe returned incomplete metadata"
        ) from None


def _body(prompt: str, response_schema: dict, data: bytes, has_audio: bool) -> bytes:
    note = (
        "The attached MP4 contains the complete video and its soundtrack. Inspect both. "
        if has_audio
        else "The attached MP4 has NO audio stream. Do not claim to hear or verify any sound. "
    )
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "inlineData": {
                            "mimeType": "video/mp4",
                            "data": base64.b64encode(data).decode("ascii"),
                        },
                        "videoMetadata": {"fps": REVIEW_FPS},
                    },
                    {"text": note + prompt},
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            # Native GenerationConfig documents JSON Schema through this field.
            "responseJsonSchema": response_schema,
        },
    }
    try:
        return json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError):
        raise MediaReviewUnavailable(
            "REQUEST_INVALID", "Prompt/schema must be JSON serializable"
        ) from None


def _verify_duration(original: dict, transport: dict) -> None:
    tolerance = 0.25  # One 5 FPS frame plus codec padding.
    if (
        abs(original["duration_seconds"] - transport["duration_seconds"]) > tolerance
        or original["has_audio"] != transport["has_audio"]
    ):
        raise MediaReviewUnavailable(
            "MEDIA_TRUNCATED", "Review copy changed duration or lost soundtrack"
        )
    for kind, timing in original["stream_timing"].items():
        actual = transport["stream_timing"].get(kind, {})
        for field, expected in timing.items():
            if expected is not None and (
                actual.get(field) is None or abs(expected - actual[field]) > tolerance
            ):
                raise MediaReviewUnavailable(
                    "MEDIA_TRUNCATED", "Review copy changed stream timing"
                )


def _prepare(path: Path, prompt: str, schema: dict, temporary: Path) -> tuple[bytes, dict]:
    try:
        path = path.resolve(strict=True)
        if not path.is_file():
            raise MediaReviewUnavailable("MEDIA_INVALID", "Review input must be a local file")
        original_hash = _digest(path)
        original = probe_video(path)
        overhead = len(_body(prompt, schema, b"", original["has_audio"]))
        raw_limit = (MAX_REQUEST_BYTES - overhead) // 4 * 3 - 3
        if raw_limit <= 0:
            raise MediaReviewUnavailable(
                "REQUEST_TOO_LARGE", "Review instructions exceed the request limit"
            )
        transcode = path.stat().st_size > raw_limit or "mp4" not in original["container"].split(
            ","
        )
        transport = path
        if transcode:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise MediaReviewUnavailable(
                    "MEDIA_TOOL_MISSING", "ffmpeg is required for a review copy"
                )
            duration = original["duration_seconds"]
            total_rate = int(raw_limit * 8 * 0.75 / duration)
            audio_rate = 96_000 if original["has_audio"] else 0
            video_rate = min(1_500_000, total_rate - audio_rate)
            if video_rate < 32_000:
                raise MediaReviewUnavailable(
                    "REQUEST_TOO_LARGE", "Full-duration media cannot fit safely"
                )
            transport = temporary / "review.mp4"
            command = [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-copyts",
                "-start_at_zero",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-vf",
                f"scale=w='min({MAX_DIMENSION},iw)':h='min({MAX_DIMENSION},ih)':"
                f"force_original_aspect_ratio=decrease:force_divisible_by=2,fps={REVIEW_FPS}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-b:v",
                str(video_rate),
                "-maxrate",
                str(video_rate),
                "-bufsize",
                str(video_rate * 2),
                "-c:a",
                "aac",
                "-b:a",
                str(audio_rate or 96_000),
                "-movflags",
                "+faststart",
                "-n",
                str(transport),
            ]
            _run(command, max(60, min(900, duration * 5)))
        transported = probe_video(transport) if transcode else original
        _verify_duration(original, transported)
        if transport.stat().st_size > raw_limit:
            raise MediaReviewUnavailable(
                "REQUEST_TOO_LARGE", "Full review copy exceeds the inline limit"
            )
        media = transport.read_bytes()
        body = _body(prompt, schema, media, original["has_audio"])
        if len(body) >= MAX_REQUEST_BYTES:
            raise MediaReviewUnavailable(
                "REQUEST_TOO_LARGE", "Complete JSON request exceeds the inline limit"
            )
        if _digest(path) != original_hash:
            raise MediaReviewUnavailable(
                "MEDIA_CHANGED", "Review input changed while preparing it"
            )
        return body, {
            "original_sha256": original_hash,
            "transport_sha256": hashlib.sha256(media).hexdigest(),
            "duration_seconds": original["duration_seconds"],
            "transport_duration_seconds": transported["duration_seconds"],
            "has_audio": original["has_audio"],
            "has_video": True,
            "audio_streams": original["audio_streams"],
            "video_streams": 1,
            "transcoded": transcode,
            "transport_bytes": len(media),
            "request_bytes": len(body),
            "fps": REVIEW_FPS,
            "original_media": original,
            "transport_media": transported,
        }
    except OSError as exc:
        raise MediaReviewUnavailable("MEDIA_UNREADABLE", type(exc).__name__) from None


def _report(body: dict) -> tuple[dict, dict]:
    feedback = body.get("promptFeedback") or {}
    if not isinstance(feedback, dict):
        raise MediaReviewUnavailable("RESPONSE_INVALID", "Invalid provider feedback")
    if feedback.get("blockReason"):
        raise MediaReviewUnavailable("REVIEW_BLOCKED", "Provider blocked the media review")
    candidates = body.get("candidates")
    if (
        not isinstance(candidates, list)
        or len(candidates) != 1
        or not isinstance(candidates[0], dict)
    ):
        raise MediaReviewUnavailable(
            "RESPONSE_INVALID", "Exactly one review candidate is required"
        )
    candidate = candidates[0]
    if candidate.get("finishReason") != "STOP":
        raise MediaReviewUnavailable(
            "RESPONSE_INCOMPLETE", "Provider did not finish the review normally"
        )
    try:
        parts = candidate["content"]["parts"]
        if not isinstance(parts, list) or any(not isinstance(part, dict) for part in parts):
            raise ValueError("Invalid content parts")
        text = "".join(
            part["text"] for part in parts if "text" in part and not part.get("thought")
        )

        def invalid_constant(_value):
            raise ValueError("Non-finite JSON value")

        report = json.loads(text, parse_constant=invalid_constant)
    except (ValueError, TypeError, KeyError):
        raise MediaReviewUnavailable("RESPONSE_INVALID", "Review is not complete JSON") from None
    if not isinstance(report, dict):
        raise MediaReviewUnavailable("RESPONSE_INVALID", "Review report must be an object")
    usage = body.get("usageMetadata") or {}
    if not isinstance(usage, dict):
        raise MediaReviewUnavailable("RESPONSE_INVALID", "Invalid provider usage metadata")
    return report, {
        "model_version": body.get("modelVersion"),
        "usage": usage,
        "finish_reason": candidate["finishReason"],
        "response_id": body.get("responseId"),
    }


async def review_video(
    *,
    path: Path,
    prompt: str,
    response_schema: dict,
    settings: tuple[str, str, str],
    timeout_seconds: float = 180,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Review full audiovisual bytes; the controller validates the returned domain report."""
    endpoint, key, model = _settings(settings)
    if not isinstance(prompt, str) or not prompt.strip() or not isinstance(response_schema, dict):
        raise MediaReviewUnavailable(
            "REQUEST_INVALID", "A prompt and JSON response schema are required"
        )
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not _number(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise MediaReviewUnavailable("REQUEST_INVALID", "A positive finite timeout is required")
    if client is None:
        async with httpx.AsyncClient(trust_env=False) as owned:
            return await review_video(
                path=path,
                prompt=prompt,
                response_schema=response_schema,
                settings=settings,
                timeout_seconds=timeout_seconds,
                client=owned,
            )
    capabilities = await probe_capabilities(settings, client)
    with tempfile.TemporaryDirectory(prefix="cine-media-review-") as temporary:
        body, metadata = await asyncio.to_thread(
            _prepare, Path(path), prompt, response_schema, Path(temporary)
        )
        response = await _request(
            client,
            "POST",
            endpoint,
            content=body,
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            timeout=timeout_seconds,
        )
        report, invocation = _report(response)
        try:
            current_hash = await asyncio.to_thread(_digest, Path(path))
        except OSError:
            raise MediaReviewUnavailable(
                "MEDIA_CHANGED", "Review input disappeared during review"
            ) from None
        if current_hash != metadata["original_sha256"]:
            raise MediaReviewUnavailable("MEDIA_CHANGED", "Review input changed during review")
        return {
            "report": report,
            "invocation": {
                "requested_model": model,
                "endpoint": endpoint,
                "capabilities": capabilities,
                **invocation,
            },
            **metadata,
        }
