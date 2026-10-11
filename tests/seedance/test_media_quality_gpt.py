"""The exact GPT judge sees factual AV observations and timestamped images, never AV."""

import copy
import hashlib
import json
import shutil
import subprocess

import httpx
import pytest

from agentnexus.seedance import media_quality_gpt as gpt
from agentnexus.seedance import media_quality_provider as native

SETTINGS = ("https://gateway.test/v1", "judge-key", "gpt-6.1-sol")
OBSERVER = ("https://observer.test/v1", "observer-key", "av-observer")
SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["pass", "revise", "review"]},
        "optional_note": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
    },
    "required": ["decision"],
    "additionalProperties": False,
}


def catalog(modalities=None):
    return {
        "models": [
            {
                "name": "models/gpt-6.1-sol",
                "supportedInputModalities": modalities or ["text", "image"],
            }
        ]
    }


def reply(**changes):
    result = {
        "id": "gpt-response-one",
        "model": "gpt-6.1-sol",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": '{"decision":"review","optional_note":null}'}
                ],
            }
        ],
        "usage": {"input_tokens": 123, "output_tokens": 12},
    }
    result.update(changes)
    return result


@pytest.fixture(scope="module")
def media_files(tmp_path_factory):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe required for actual image extraction")
    root = tmp_path_factory.mktemp("gpt-review-media")
    files = {}
    for audio in (True, False):
        path = root / ("av.mp4" if audio else "silent.mp4")
        command = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=25",
        ]
        if audio:
            command += ["-f", "lavfi", "-i", "sine=frequency=450:sample_rate=48000"]
        command += ["-t", "2.4", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
        if audio:
            command += ["-c:a", "aac"]
        command += ["-n", str(path)]
        subprocess.run(command, capture_output=True, check=True, timeout=30)
        files[audio] = path
    return files


@pytest.fixture
def video(media_files, tmp_path):
    path = tmp_path / "clip.mp4"
    shutil.copyfile(media_files[True], path)
    return path


class Observer:
    def __init__(self, change=None):
        self.calls = []
        self.change = change

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        path = kwargs["path"]
        metadata = native.probe_video(path)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        result = {
            **metadata,
            "original_sha256": sha,
            "transport_sha256": sha,
            "transport_duration_seconds": metadata["duration_seconds"],
            "transport_bytes": path.stat().st_size,
            "transcoded": False,
            "request_bytes": 100,
            "fps": 5.0,
            "invocation": {
                "requested_model": "av-observer",
                "model_version": "av-observer-v1",
                "response_id": "observer-call",
                "finish_reason": "STOP",
                "usage": {"promptTokenCount": 70},
                "capabilities": {
                    "supported_input_modalities": ["text", "image", "video", "audio"]
                },
            },
            "report": {
                "observations": [
                    {
                        "id": "fact-1",
                        "start_seconds": 0.0,
                        "end_seconds": metadata["duration_seconds"],
                        "visible": "Colored test pattern moves.",
                        "audible": "Steady tone." if metadata["has_audio"] else "",
                        "uncertainty": "No people or speech.",
                    }
                ]
            },
        }
        if self.change:
            self.change(result)
        return result


@pytest.mark.parametrize(
    "base,expected",
    [
        ("https://gateway.test/v1", "https://gateway.test/v1/responses"),
        ("http://gateway.test:1234/proxy/v1beta", "http://gateway.test:1234/proxy/v1/responses"),
    ],
)
def test_responses_endpoint_preserves_configured_origin(base, expected):
    assert gpt.responses_endpoint(base, SETTINGS[2]) == expected


def test_strict_schema_keeps_types_and_does_not_mutate_caller():
    before = copy.deepcopy(SCHEMA)
    schema = gpt.strict_schema(SCHEMA)
    assert before == SCHEMA
    assert schema["required"] == ["decision", "optional_note"]
    assert (
        schema["properties"]["optional_note"]["anyOf"]
        == before["properties"]["optional_note"]["anyOf"]
    )
    assert "default" not in schema["properties"]["optional_note"]


async def test_one_observer_then_exact_gpt_with_real_frames_and_separate_receipts(video):
    observer, posted, methods = Observer(), [], []

    def handle(request):
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=catalog())
        assert len(observer.calls) == 1
        assert str(request.url) == "https://gateway.test/v1/responses"
        assert request.headers["Authorization"] == "Bearer judge-key"
        posted.append(json.loads(request.content))
        return httpx.Response(200, json=reply())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await gpt.review_evidence(
            path=video,
            prompt="Review these scenes using rendered-media",
            response_schema=SCHEMA,
            settings=SETTINGS,
            observer_settings=OBSERVER,
            client=client,
            observer=observer,
        )
    assert methods == ["GET", "POST"]
    assert observer.calls[0]["response_schema"] == gpt.OBSERVER_SCHEMA
    assert observer.calls[0]["settings"] == OBSERVER
    assert "Review these scenes" not in observer.calls[0]["prompt"]
    body = posted[0]
    assert body["model"] == "gpt-6.1-sol"
    assert body["text"]["format"]["strict"] is True
    assert body["text"]["format"]["schema"]["required"] == ["decision", "optional_note"]
    content = body["input"][0]["content"]
    assert {p["type"] for p in content} == {"input_text", "input_image"}
    images = [p for p in content if p["type"] == "input_image"]
    assert len(images) == len(result["reviewer_media"]) == 4
    assert all(p["image_url"].startswith("data:image/jpeg;base64,") for p in images)
    assert result["sampling"]["actual_timestamps"] == [0.0, 1.0, 2.0, 2.36]
    assert result["reviewer_modalities"] == ["text", "image"]
    assert result["direct_av_review"] is False
    assert result["input_mode"] == "observer_assisted"
    assert result["observer_invocation"]["media"][0]["modalities"] == ["video", "audio"]
    assert result["observer_invocation"]["media"][0]["receipt_id"] == "observer-av"
    assert all(row["modalities"] == ["image"] for row in result["reviewer_media"])
    assert result["invocation"]["response_id"] == "gpt-response-one"
    assert result["observer_invocation"]["call_id"] == "observer-call"
    assert result["invocation"]["model_version"] == "gpt-6.1-sol"
    assert result["observer_invocation"]["model"] == "av-observer-v1"
    assert "judge-key" not in json.dumps(result) and "observer-key" not in json.dumps(result)
    assert "input_audio" not in json.dumps(body) and "input_video" not in json.dumps(body)


async def test_default_native_observer_and_gpt_are_two_distinct_real_protocols(video):
    calls = []

    def handle(request):
        calls.append((request.method, request.url.host, request.url.path))
        if request.method == "GET":
            if request.url.host == "gateway.test":
                return httpx.Response(200, json=catalog())
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/av-observer",
                            "supportedInputModalities": ["text", "video", "audio"],
                            "supportedGenerationMethods": ["generateContent"],
                        }
                    ]
                },
            )
        if request.url.host == "observer.test":
            body = json.loads(request.content)
            assert body["contents"][0]["parts"][0]["inlineData"]["mimeType"] == "video/mp4"
            facts = {
                "observations": [
                    {
                        "id": "fact",
                        "start_seconds": 0.0,
                        "end_seconds": 2.4,
                        "visible": "Moving pattern",
                        "audible": "Steady tone",
                        "uncertainty": "",
                    }
                ]
            }
            return httpx.Response(
                200,
                json={
                    "modelVersion": "observer-v1",
                    "responseId": "native-observer-call",
                    "candidates": [
                        {
                            "finishReason": "STOP",
                            "content": {"parts": [{"text": json.dumps(facts)}]},
                        }
                    ],
                },
            )
        return httpx.Response(200, json=reply())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await gpt.review_evidence(
            path=video,
            prompt="Review",
            response_schema=SCHEMA,
            settings=SETTINGS,
            observer_settings=OBSERVER,
            client=client,
        )
    assert len([c for c in calls if c[0] == "POST"]) == 2
    assert result["observer_invocation"]["call_id"] == "native-observer-call"
    assert result["invocation"]["response_id"] == "gpt-response-one"


async def test_silent_video_never_gains_an_audio_receipt(media_files):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json=catalog() if r.method == "GET" else reply())
        )
    ) as client:
        result = await gpt.review_evidence(
            path=media_files[False],
            prompt="Review",
            response_schema=SCHEMA,
            settings=SETTINGS,
            observer_settings=OBSERVER,
            client=client,
            observer=Observer(),
        )
    assert result["has_audio"] is False
    assert result["observer_invocation"]["media"][0]["modalities"] == ["video"]


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(original_sha256="0" * 64),
        lambda r: r.update(report={"decision": "pass", "observations": []}),
        lambda r: r["report"]["observations"][0].update(end_seconds=999),
        lambda r: r["invocation"]["capabilities"].update(
            supported_input_modalities=["text", "image"]
        ),
    ],
)
async def test_invalid_or_non_av_observer_cannot_reach_gpt(video, change):
    posts = []

    def handle(request):
        if request.method == "POST":
            posts.append(request)
        return httpx.Response(200, json=catalog())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(native.MediaReviewUnavailable, match="OBSERVER_INVALID"):
            await gpt.review_evidence(
                path=video,
                prompt="Review",
                response_schema=SCHEMA,
                settings=SETTINGS,
                observer_settings=OBSERVER,
                client=client,
                observer=Observer(change),
            )
    assert not posts


async def test_observer_failure_does_not_fallback_to_another_judge(video):
    async def failed(**kwargs):
        raise native.MediaReviewUnavailable("CAPABILITIES_UNAVAILABLE", "No AV support")

    methods = []

    def handle(request):
        methods.append(request.method)
        return httpx.Response(200, json=catalog())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(native.MediaReviewUnavailable, match="CAPABILITIES_UNAVAILABLE"):
            await gpt.review_evidence(
                path=video,
                prompt="Review",
                response_schema=SCHEMA,
                settings=SETTINGS,
                observer_settings=OBSERVER,
                client=client,
                observer=failed,
            )
    assert methods == ["GET"]


@pytest.mark.parametrize(
    "response,code",
    [
        (reply(status="incomplete"), "RESPONSE_INCOMPLETE"),
        (reply(model="gemini-pro-agent"), "MODEL_MISMATCH"),
        (reply(output=[]), "RESPONSE_INVALID"),
        (
            reply(output=[{"type": "message", "content": [{"type": "refusal", "refusal": "No"}]}]),
            "REVIEW_BLOCKED",
        ),
        (
            reply(
                output=[
                    {"type": "message", "content": [{"type": "output_text", "text": "not JSON"}]}
                ]
            ),
            "RESPONSE_INVALID",
        ),
    ],
)
async def test_gpt_failure_is_not_accepted_or_retried(video, response, code):
    posts = []

    def handle(request):
        if request.method == "POST":
            posts.append(request)
        return httpx.Response(200, json=catalog() if request.method == "GET" else response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(native.MediaReviewUnavailable, match=code):
            await gpt.review_evidence(
                path=video,
                prompt="Review",
                response_schema=SCHEMA,
                settings=SETTINGS,
                observer_settings=OBSERVER,
                client=client,
                observer=Observer(),
            )
    assert len(posts) == 1


async def test_source_changes_after_observer_abort_before_gpt(video):
    original = Observer()

    async def changed(**kwargs):
        result = await original(**kwargs)
        video.write_bytes(b"changed")
        return result

    methods = []

    def handle(request):
        methods.append(request.method)
        return httpx.Response(200, json=catalog())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(native.MediaReviewUnavailable, match="MEDIA_CHANGED"):
            await gpt.review_evidence(
                path=video,
                prompt="Review",
                response_schema=SCHEMA,
                settings=SETTINGS,
                observer_settings=OBSERVER,
                client=client,
                observer=changed,
            )
    assert methods == ["GET"]


async def test_gpt_modality_is_probed_not_assumed_from_its_name(video):
    observer = Observer()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=catalog(["text"])))
    ) as client:
        with pytest.raises(native.MediaReviewUnavailable, match="CAPABILITIES_UNAVAILABLE"):
            await gpt.review_evidence(
                path=video,
                prompt="Review",
                response_schema=SCHEMA,
                settings=SETTINGS,
                observer_settings=OBSERVER,
                client=client,
                observer=observer,
            )
    assert not observer.calls


def test_long_video_sampling_is_bounded_and_keeps_actual_first_last_frames():
    times = [i / 25 for i in range(25 * 600)]
    chosen = gpt._frame_indices(times, 600.0)
    assert len(chosen) <= 122
    assert chosen[0] == 0 and chosen[-1] == len(times) - 1
