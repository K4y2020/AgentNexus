"""Create a qualified local ASR transcript for Cine source analysis."""

import argparse
import json
import os
import tempfile
from pathlib import Path


def _inside(workspace: Path, path: Path) -> Path:
    workspace = workspace.resolve()
    path = path.resolve()
    if not path.is_relative_to(workspace):
        raise ValueError("ASR paths must remain inside the Topic workspace")
    return path


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, newline="\n"
    ) as handle:
        handle.write(value)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def format_srt_time(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def serialize_transcript(
    segments, *, media: Path, model: str, language: str | None
) -> tuple[str, str, str]:
    rows = []
    PUNCT_SPLIT = set("。！？!?；;\n")

    # If segments have word-level timestamps, refine sentences by punctuation and pauses
    all_words = []
    for item in segments:
        words = getattr(item, "words", None)
        if words:
            all_words.extend([{"word": w.word, "start": w.start, "end": w.end} for w in words])

    if all_words:
        current = []
        for i, w in enumerate(all_words):
            current.append(w)
            word_text = w["word"].strip()
            is_punct = any(p in word_text for p in PUNCT_SPLIT)
            is_pause = False
            if i + 1 < len(all_words):
                gap = all_words[i + 1]["start"] - w["end"]
                if gap > 0.65:
                    is_pause = True
            if is_punct or is_pause:
                st = current[0]["start"]
                et = current[-1]["end"]
                txt = "".join(x["word"] for x in current).strip().strip("，,。！？!?；; ")
                if txt:
                    rows.append({"start": round(st, 3), "end": round(et, 3), "text": txt})
                current = []
        if current:
            st = current[0]["start"]
            et = current[-1]["end"]
            txt = "".join(x["word"] for x in current).strip().strip("，,。！？!?；; ")
            if txt:
                rows.append({"start": round(st, 3), "end": round(et, 3), "text": txt})

    if not rows:
        rows = [
            {
                "start": round(float(item.start), 3),
                "end": round(float(item.end), 3),
                "text": item.text.strip(),
            }
            for item in segments
            if item.text.strip()
        ]

    payload = {
        "schema_version": 1,
        "kind": "qualified_asr_transcript",
        "source_media": media.name,
        "model": model,
        "language": language,
        "verbatim_certified": False,
        "segments": rows,
    }
    lines = [
        "# ASR transcript (qualified, not manually verified verbatim)",
        "",
        *[f"[{row['start']:08.3f}-{row['end']:08.3f}] {row['text']}" for row in rows],
        "",
    ]
    srt_blocks = [
        f"{idx}\n{format_srt_time(row['start'])} --> {format_srt_time(row['end'])}\n{row['text']}\n"
        for idx, row in enumerate(rows, 1)
    ]
    return (
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        "\n".join(lines),
        "\n".join(srt_blocks),
    )


def transcribe(
    media: Path,
    output: Path,
    workspace: Path,
    *,
    model: str,
    language: str | None,
    initial_prompt: str | None = None,
    separate_vocals: bool = False,
) -> dict:
    media = _inside(workspace, media)
    if not media.is_file():
        raise FileNotFoundError(media)
    output = _inside(workspace, output)

    audio_to_transcribe = media
    if separate_vocals:
        sep_dir = workspace / "inputs/separated/htdemucs/audio"
        vocals_file = sep_dir / "vocals.wav"
        if not vocals_file.is_file():
            import subprocess

            # Extract audio first
            tmp_audio = workspace / "inputs/audio.wav"
            subprocess.run(
                ["ffmpeg", "-i", str(media), "-vn", "-ar", "44100", "-ac", "2", str(tmp_audio), "-y"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "uv", "run", "--with", "demucs", "--with", "torch", "--with", "numpy<2",
                    "demucs", "--two-stems", "vocals", "-n", "htdemucs",
                    "-o", str(workspace / "inputs/separated"), str(tmp_audio),
                ],
                check=True,
                capture_output=True,
            )
        if vocals_file.is_file():
            audio_to_transcribe = vocals_file

    from faster_whisper import WhisperModel

    engine = WhisperModel(model, device="cpu", compute_type="int8")
    transcribe_kwargs = {
        "language": language,
        "vad_filter": True,
        "word_timestamps": True,
    }
    if initial_prompt:
        transcribe_kwargs["initial_prompt"] = initial_prompt

    segments, info = engine.transcribe(str(audio_to_transcribe), **transcribe_kwargs)
    json_text, plain_text, srt_text = serialize_transcript(
        list(segments), media=media, model=model, language=getattr(info, "language", language)
    )
    json_path = output.with_suffix(".json")
    text_path = output.with_suffix(".txt")
    srt_path = output.with_suffix(".srt")
    _atomic_text(json_path, json_text)
    _atomic_text(text_path, plain_text)
    _atomic_text(srt_path, srt_text)

    # Also make sure inputs/source.srt is mirrored if output is in inputs
    if output.parent.name == "inputs" and output.name != "source":
        source_srt = output.parent / "source.srt"
        if not source_srt.exists():
            _atomic_text(source_srt, srt_text)

    return {
        "status": "qualified_asr_complete",
        "json_path": str(json_path),
        "text_path": str(text_path),
        "srt_path": str(srt_path),
        "verbatim_certified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--media", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--initial-prompt", default=None)
    parser.add_argument("--separate-vocals", action="store_true", default=False)
    args = parser.parse_args()
    print(
        json.dumps(
            transcribe(
                args.media,
                args.output,
                args.workspace,
                model=args.model,
                language=args.language,
                initial_prompt=args.initial_prompt,
                separate_vocals=args.separate_vocals,
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
