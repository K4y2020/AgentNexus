import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import media_assembly


@pytest.fixture
def production(tmp_path):
    directory = tmp_path / "topic" / "production"
    directory.mkdir(parents=True)
    return directory


def receipt(path, job="job-1"):
    return {
        "job_id": job,
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "media_kind": "video",
    }


def clip(
    directory, name, color, *, size="64x48", rate=10, audio=True, channels=1, codec="libx264"
):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("Local FFmpeg and FFprobe are required for offline media integration.")
    path = directory.parent / "exports" / name
    path.parent.mkdir(exist_ok=True)
    args = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={size}:r={rate}:d=0.4",
    ]
    if audio:
        args.extend(
            [
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=0.4",
                "-c:a",
                "aac",
                "-ac",
                str(channels),
            ]
        )
    args.extend(["-c:v", codec, "-pix_fmt", "yuv420p"])
    if codec == "libx264":
        args.extend(["-preset", "ultrafast"])
    args.extend(["-t", "0.4", "-n", str(path)])
    subprocess.run(args, check=True, capture_output=True, timeout=30)
    return receipt(path, name)


def test_actual_concat_preserves_media_and_remains_unaccepted(production, monkeypatch):
    clips = [clip(production, "首段'001.mp4", "red"), clip(production, "second.mp4", "blue")]
    before = [Path(c["path"]).read_bytes() for c in clips]
    result = media_assembly.assemble(production, clips)
    assert result["status"] == "assembled", result
    assert result["acceptance"] == "pending"
    assert "audio_quality" in result["unverified"]
    assert result["metadata"]["decode_verified"] is True
    assert result["metadata"]["video_frames"] == 8
    assert result["duration_seconds"] == pytest.approx(0.8, abs=0.08)
    assert Path(result["path"]).parent == production.parent / "outputs" / "cine"
    assert Path(result["path"]).stem == result["assembly_id"]
    assert hashlib.sha256(Path(result["path"]).read_bytes()).hexdigest() == result["sha256"]
    assert [Path(c["path"]).read_bytes() for c in clips] == before
    # A saved result is recoverable without rerunning an external process.
    monkeypatch.setattr(
        media_assembly, "_run", lambda *_a, **_kw: pytest.fail("An unchanged film was recomputed")
    )
    reused = media_assembly.assemble(production, clips)
    assert reused["reused"] is True
    assert reused["sha256"] == result["sha256"]


def test_silent_clips_are_supported_without_inventing_audio(production):
    clips = [
        clip(production, "a.mp4", "red", audio=False),
        clip(production, "b.mp4", "blue", audio=False),
    ]
    result = media_assembly.assemble(production, clips)
    assert result["status"] == "assembled", result
    assert [s["codec_type"] for s in result["metadata"]["streams"]] == ["video"]


@pytest.mark.parametrize(
    "options",
    [{"size": "80x48"}, {"rate": 20}, {"audio": False}, {"channels": 2}, {"codec": "mpeg4"}],
)
def test_incompatible_media_is_refused_without_conversion(production, options):
    clips = [clip(production, "a.mp4", "red"), clip(production, "b.mp4", "blue", **options)]
    result = media_assembly.assemble(production, clips)
    assert result["status"] == "blocked"
    assert result["error_code"] == "CINE_ASSEMBLY_INCOMPATIBLE_CLIPS", result
    assert not (production.parent / "outputs" / "cine").exists()


def test_replaced_export_is_rejected_before_tools_run(production, monkeypatch):
    path = production / "clip.mp4"
    path.write_bytes(b"verified bytes")
    current = receipt(path)
    path.write_bytes(b"different bytes")
    monkeypatch.setattr(
        media_assembly, "_run", lambda *_a, **_kw: pytest.fail("Invalid media reached FFmpeg")
    )
    result = media_assembly.assemble(production, [current])
    assert result["error_code"] == "CINE_ASSEMBLY_CLIP_CHANGED"


def test_outside_topic_and_output_escape_are_rejected(production, tmp_path):
    outside = tmp_path / "other.mp4"
    outside.write_bytes(b"video")
    assert (
        media_assembly.assemble(production, [receipt(outside)])["error_code"]
        == "CINE_ASSEMBLY_PATH_ESCAPE"
    )
    local = production / "local.mp4"
    local.write_bytes(b"video")
    assert (
        media_assembly.assemble(production, [receipt(local)], tmp_path / "exports")["error_code"]
        == "CINE_ASSEMBLY_PATH_ESCAPE"
    )
    assert (
        media_assembly.assemble(production, [receipt(local)], production / "exports")["error_code"]
        == "CINE_ASSEMBLY_OUTPUT_INVALID"
    )


def test_duplicates_are_rejected(production):
    path = production / "clip.mp4"
    path.write_bytes(b"video")
    row = receipt(path)
    result = media_assembly.assemble(production, [row, {**row, "job_id": "second-job"}])
    assert result["error_code"] == "CINE_ASSEMBLY_DUPLICATE_CLIP"


def test_missing_ffmpeg_is_explicit(production, monkeypatch):
    path = production / "clip.mp4"
    path.write_bytes(b"video")
    monkeypatch.setattr(media_assembly.shutil, "which", lambda _: None)
    result = media_assembly.assemble(production, [receipt(path)])
    assert result["error_code"] == "CINE_ASSEMBLY_TOOL_MISSING"
    assert set(result["detail"]["missing"]) == {"ffmpeg", "ffprobe"}


def test_existing_changed_film_is_never_overwritten(production):
    clips = [clip(production, "a.mp4", "red"), clip(production, "b.mp4", "blue")]
    result = media_assembly.assemble(production, clips)
    assert result["status"] == "assembled", result
    path = Path(result["path"])
    path.write_bytes(b"user changed this film")
    again = media_assembly.assemble(production, clips)
    assert again["error_code"] == "CINE_ASSEMBLY_RECEIPT_MISMATCH"
    assert path.read_bytes() == b"user changed this film"


def test_receipt_missing_after_publish_is_recovered(production):
    clips = [clip(production, "a.mp4", "red"), clip(production, "b.mp4", "blue")]
    result = media_assembly.assemble(production, clips)
    assert result["status"] == "assembled", result
    Path(result["receipt_path"]).unlink()
    again = media_assembly.assemble(production, clips)
    assert again["status"] == "assembled", again
    assert again["sha256"] == result["sha256"]
    assert (
        json.loads(Path(again["receipt_path"]).read_text(encoding="utf-8"))["sha256"]
        == result["sha256"]
    )


def test_replaced_media_during_concat_is_not_published(production, monkeypatch):
    clips = [clip(production, "a.mp4", "red"), clip(production, "b.mp4", "blue")]
    run = media_assembly._run

    def replace_after_copy(command, **kwargs):
        result = run(command, **kwargs)
        if "copy" in command:
            Path(clips[0]["path"]).write_bytes(b"replaced during assembly")
        return result

    monkeypatch.setattr(media_assembly, "_run", replace_after_copy)
    result = media_assembly.assemble(production, clips)
    assert result["error_code"] == "CINE_ASSEMBLY_CLIP_CHANGED", result
    assert not list((production.parent / "outputs" / "cine").glob("*.mp4"))


def test_decode_failure_leaves_no_deliverable(production, monkeypatch):
    clips = [clip(production, "a.mp4", "red"), clip(production, "b.mp4", "blue")]
    run = media_assembly._run

    def reject_decode(command, **kwargs):
        if "null" in command:
            raise media_assembly.AssemblyBlocked("CINE_ASSEMBLY_TOOL_FAILED", "corrupt packet")
        return run(command, **kwargs)

    monkeypatch.setattr(media_assembly, "_run", reject_decode)
    result = media_assembly.assemble(production, clips)
    assert result["status"] == "blocked"
    assert not list((production.parent / "outputs" / "cine").glob("*.mp4"))


def test_unreceipted_conflicting_output_is_preserved(production):
    clips = [clip(production, "a.mp4", "red"), clip(production, "b.mp4", "blue")]
    first = media_assembly.assemble(production, clips)
    assert first["status"] == "assembled", first
    Path(first["receipt_path"]).unlink()
    target = Path(first["path"])
    target.write_bytes(b"existing user output")
    result = media_assembly.assemble(production, clips)
    assert result["error_code"] == "CINE_ASSEMBLY_OUTPUT_EXISTS", result
    assert target.read_bytes() == b"existing user output"
