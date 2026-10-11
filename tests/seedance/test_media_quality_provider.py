"""Native media requests carry complete audiovisual bytes and never hide failure."""

import base64
import hashlib
import json
import shutil
import subprocess

import httpx
import pytest

from agentnexus.seedance import media_quality_provider as provider

SETTINGS = ("https://gateway.test/v1", "test-secret", "review-model")
SCHEMA = {
    "type": "object",
    "properties": {"verdict": {"type": "string"}},
    "required": ["verdict"],
    "additionalProperties": False,
}


def model_metadata(modalities=None):
    return {
        "models": [
            {
                "name": "models/review-model",
                "supportedInputModalities": modalities or ["text", "video", "audio"],
                "supportedGenerationMethods": ["generateContent"],
            }
        ]
    }


def model_reply(**changes):
    reply = {
        "candidates": [
            {
                "content": {"parts": [{"text": '{"verdict":"review"}'}]},
                "finishReason": "STOP",
            }
        ],
        "modelVersion": "review-model-2026",
        "usageMetadata": {
            "promptTokenCount": 123,
            "promptTokensDetails": [{"modality": "AUDIO", "tokenCount": 42}],
        },
        "responseId": "response-one",
    }
    reply.update(changes)
    return reply


@pytest.fixture(scope="module")
def media_files(tmp_path_factory):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg and ffprobe are required for real media tests")
    root = tmp_path_factory.mktemp("media-quality")
    result = {}
    for audio in (True, False):
        target = root / ("audiovisual.mp4" if audio else "silent.mp4")
        args = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=30",
        ]
        if audio:
            args += ["-f", "lavfi", "-i", "sine=frequency=640:sample_rate=48000"]
        args += [
            "-t",
            "1.6",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "12",
            "-pix_fmt",
            "yuv420p",
        ]
        if audio:
            args += ["-c:a", "aac", "-b:a", "96k"]
        args += ["-movflags", "+faststart", "-n", str(target)]
        subprocess.run(args, capture_output=True, check=True, timeout=30)
        result[audio] = target
    return result


@pytest.fixture
def video(media_files, tmp_path):
    target = tmp_path / "clip.mp4"
    shutil.copyfile(media_files[True], target)
    return target


@pytest.mark.parametrize(
    "base,expected",
    [
        ("https://gateway.test/v1", "https://gateway.test/v1beta"),
        ("http://gateway.test:1234/v1beta/", "http://gateway.test:1234/v1beta"),
        ("https://gateway.test/proxy/v1", "https://gateway.test/proxy/v1beta"),
        ("https://gateway.test", "https://gateway.test/v1beta"),
    ],
)
def test_native_endpoint_preserves_origin_and_proxy_prefix(base, expected):
    assert (
        provider.native_endpoint(base, "models/review-model")
        == expected + "/models/review-model:generateContent"
    )


@pytest.mark.parametrize(
    "base,model",
    [
        ("https://gateway.test/v1?key=secret", "review-model"),
        ("https://user:secret@gateway.test/v1", "review-model"),
        ("https://gateway.test/other", "review-model"),
        ("file:///v1", "review-model"),
        ("https://gateway.test/v1", "../../other"),
    ],
)
def test_invalid_endpoint_never_switches_provider(base, model):
    with pytest.raises(provider.MediaReviewUnavailable) as caught:
        provider.native_endpoint(base, model)
    assert "secret" not in str(caught.value)


async def test_original_video_and_audio_bytes_are_sent_with_native_schema(video):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["x-goog-api-key"] == "test-secret"
        return httpx.Response(
            200, json=model_metadata() if request.method == "GET" else model_reply()
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await provider.review_video(
            path=video,
            prompt="Review the complete performance",
            response_schema=SCHEMA,
            settings=SETTINGS,
            client=client,
        )
    assert [request.method for request in requests] == ["GET", "POST"]
    body = json.loads(requests[-1].content)
    part = body["contents"][0]["parts"][0]
    assert base64.b64decode(part["inlineData"]["data"]) == video.read_bytes()
    assert part["inlineData"]["mimeType"] == "video/mp4"
    assert part["videoMetadata"] == {"fps": 5.0}
    assert body["generationConfig"]["responseJsonSchema"] == SCHEMA
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert result["has_audio"] is True and result["has_video"] is True
    assert result["duration_seconds"] == pytest.approx(1.6, abs=0.1)
    assert result["transcoded"] is False
    assert (
        result["transport_sha256"]
        == result["original_sha256"]
        == hashlib.sha256(video.read_bytes()).hexdigest()
    )
    assert result["report"] == {"verdict": "review"}
    assert result["invocation"]["model_version"] == "review-model-2026"
    assert result["invocation"]["usage"]["promptTokensDetails"][0]["modality"] == "AUDIO"
    assert "test-secret" not in json.dumps(result)


async def test_no_audio_is_reported_without_fabricated_listening(media_files):
    posted = []

    def handle(request):
        if request.method == "POST":
            posted.append(json.loads(request.content))
        return httpx.Response(
            200, json=model_metadata() if request.method == "GET" else model_reply()
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await provider.review_video(
            path=media_files[False],
            prompt="Review",
            response_schema=SCHEMA,
            settings=SETTINGS,
            client=client,
        )
    assert result["has_audio"] is False and result["audio_streams"] == 0
    assert "NO audio stream" in posted[0]["contents"][0]["parts"][1]["text"]


async def test_large_clip_uses_a_full_duration_review_copy(video, monkeypatch):
    monkeypatch.setattr(provider, "MAX_REQUEST_BYTES", 100_000)
    assert video.stat().st_size > 100_000
    posted = []

    def handle(request):
        if request.method == "POST":
            posted.append(request.content)
        return httpx.Response(
            200, json=model_metadata() if request.method == "GET" else model_reply()
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await provider.review_video(
            path=video, prompt="Review", response_schema=SCHEMA, settings=SETTINGS, client=client
        )
    assert result["transcoded"] is True
    assert result["transport_sha256"] != result["original_sha256"]
    assert result["transport_duration_seconds"] == pytest.approx(
        result["duration_seconds"], abs=0.25
    )
    assert result["transport_media"]["has_audio"] is True
    assert max(result["transport_media"]["width"], result["transport_media"]["height"]) <= 720
    assert result["request_bytes"] == len(posted[0]) < 100_000
    media = json.loads(posted[0])["contents"][0]["parts"][0]["inlineData"]["data"]
    assert hashlib.sha256(base64.b64decode(media)).hexdigest() == result["transport_sha256"]


@pytest.mark.parametrize("modalities", [["text", "image"], ["video"], ["audio"]])
async def test_missing_declared_modality_prevents_generation_request(video, modalities):
    methods = []

    def handle(request):
        methods.append(request.method)
        return httpx.Response(200, json=model_metadata(modalities))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(provider.MediaReviewUnavailable, match="CAPABILITIES_UNAVAILABLE"):
            await provider.review_video(
                path=video,
                prompt="Review",
                response_schema=SCHEMA,
                settings=SETTINGS,
                client=client,
            )
    assert methods == ["GET"]


async def test_capability_redirect_does_not_forward_credentials():
    visited = []

    def handle(request):
        visited.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://other.test/v1beta/models"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), follow_redirects=True
    ) as client:
        with pytest.raises(provider.MediaReviewUnavailable, match="HTTP 302"):
            await provider.probe_capabilities(SETTINGS, client)
    assert visited == ["https://gateway.test/v1beta/models"]


async def test_capability_pagination_and_exact_model_selection():
    def handle(request):
        if request.url.params.get("pageToken") == "second":
            return httpx.Response(200, json=model_metadata())
        return httpx.Response(200, json={"models": [], "nextPageToken": "second"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await provider.probe_capabilities(SETTINGS, client)
    assert result["model"] == "review-model"


@pytest.mark.parametrize(
    "reply,code",
    [
        ({"candidates": []}, "RESPONSE_INVALID"),
        ({"promptFeedback": {"blockReason": "SAFETY"}}, "REVIEW_BLOCKED"),
        (model_reply(candidates=[{"finishReason": "MAX_TOKENS"}]), "RESPONSE_INCOMPLETE"),
        (
            model_reply(
                candidates=[{"finishReason": "STOP", "content": {"parts": [{"text": "not JSON"}]}}]
            ),
            "RESPONSE_INVALID",
        ),
        (
            model_reply(
                candidates=[{"finishReason": "STOP", "content": {"parts": [{"text": "[]"}]}}]
            ),
            "RESPONSE_INVALID",
        ),
        (
            model_reply(
                candidates=[
                    {"finishReason": "STOP", "content": {"parts": [{"text": '{"score":NaN}'}]}}
                ]
            ),
            "RESPONSE_INVALID",
        ),
    ],
)
async def test_incomplete_or_invalid_review_is_never_hidden(video, reply, code):
    def handle(request):
        return httpx.Response(200, json=model_metadata() if request.method == "GET" else reply)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(provider.MediaReviewUnavailable, match=code):
            await provider.review_video(
                path=video,
                prompt="Review",
                response_schema=SCHEMA,
                settings=SETTINGS,
                client=client,
            )


async def test_transport_failure_does_not_echo_key_or_request(video):
    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json=model_metadata())
        raise httpx.ReadTimeout("test-secret data:video/mp4;base64,private", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(provider.MediaReviewUnavailable) as caught:
            await provider.review_video(
                path=video,
                prompt="Review",
                response_schema=SCHEMA,
                settings=SETTINGS,
                client=client,
            )
    assert caught.value.code == "TRANSPORT_FAILED"
    assert "test-secret" not in str(caught.value) and "private" not in str(caught.value)


async def test_source_changed_during_request_invalidates_review(video):
    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json=model_metadata())
        video.write_bytes(b"changed")
        return httpx.Response(200, json=model_reply())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(provider.MediaReviewUnavailable, match="MEDIA_CHANGED"):
            await provider.review_video(
                path=video,
                prompt="Review",
                response_schema=SCHEMA,
                settings=SETTINGS,
                client=client,
            )


async def test_unfit_full_request_is_not_clipped_or_submitted(video, monkeypatch):
    monkeypatch.setattr(provider, "MAX_REQUEST_BYTES", 100)
    methods = []

    def handle(request):
        methods.append(request.method)
        return httpx.Response(200, json=model_metadata())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(provider.MediaReviewUnavailable, match="REQUEST_TOO_LARGE"):
            await provider.review_video(
                path=video,
                prompt="Review",
                response_schema=SCHEMA,
                settings=SETTINGS,
                client=client,
            )
    assert methods == ["GET"]


def test_duration_and_audio_loss_are_rejected():
    original = {"duration_seconds": 10.0, "has_audio": True, "stream_timing": {}}
    for transport in [{**original, "duration_seconds": 8.0}, {**original, "has_audio": False}]:
        with pytest.raises(provider.MediaReviewUnavailable, match="MEDIA_TRUNCATED"):
            provider._verify_duration(original, transport)


def test_public_probe_reports_real_video_and_audio(video):
    meta = provider.probe_video(video)
    assert meta["has_video"] and meta["has_audio"]
    assert meta["video_streams"] == meta["audio_streams"] == 1
    assert meta["duration_seconds"] == pytest.approx(1.6, abs=0.1)
