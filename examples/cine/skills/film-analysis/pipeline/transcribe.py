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


def serialize_transcript(
    segments, *, media: Path, model: str, language: str | None
) -> tuple[str, str]:
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
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "\n".join(lines)


def transcribe(
    media: Path, output: Path, workspace: Path, *, model: str, language: str | None
) -> dict:
    media = _inside(workspace, media)
    if not media.is_file():
        raise FileNotFoundError(media)
    output = _inside(workspace, output)
    from faster_whisper import WhisperModel

    engine = WhisperModel(model, device="cpu", compute_type="int8")
    segments, info = engine.transcribe(str(media), language=language, vad_filter=True)
    json_text, plain_text = serialize_transcript(
        list(segments), media=media, model=model, language=getattr(info, "language", language)
    )
    json_path = output.with_suffix(".json")
    text_path = output.with_suffix(".txt")
    _atomic_text(json_path, json_text)
    _atomic_text(text_path, plain_text)
    return {
        "status": "qualified_asr_complete",
        "json_path": str(json_path),
        "text_path": str(text_path),
        "verbatim_certified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--media", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--model", default="small")
    parser.add_argument("--language")
    args = parser.parse_args()
    print(
        json.dumps(
            transcribe(
                args.media, args.output, args.workspace, model=args.model, language=args.language
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
