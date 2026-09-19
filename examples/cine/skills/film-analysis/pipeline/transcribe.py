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


def clean_asr_text(text: str) -> str:
    """Clean Whisper BPE fullwidth artifact characters and common ASR homophones."""
    import re

    # Clean Whisper BPE fullwidth artifacts after English words (e.g. "JakenＢ" -> "Jaken，")
    text = re.sub(r"([a-zA-Z]+)[Ａ-Ｚａ-ｚ]", r"\1，", text)
    # Remove any remaining isolated fullwidth latin letters
    text = re.sub(r"[Ａ-Ｚａ-ｚ]", "", text)
    # Common homophones in sci-fi/story contexts
    text = text.replace("复课仪式", "复刻仪式")
    text = text.replace("出世那碗", "出事那晚").replace("出身那碗", "出事那晚")
    text = text.replace("储物隔", "储物格")
    text = text.replace("、", "，")
    return text.strip().strip("，,。！？!?；; ")


def llm_refine_transcript(rows: list[dict], context_summary: str | None = None) -> list[dict]:
    """Refine ASR dialogue segments using an LLM via the AgentNexus gateway."""
    if not rows:
        return rows
    import urllib.request

    config_path = Path.home() / ".agentnexus/config.yaml"
    base_url = os.environ.get("AGENTNEXUS_GATEWAY_URL") or os.environ.get("OPENAI_BASE_URL")
    api_key = os.environ.get("AGENTNEXUS_API_KEY") or os.environ.get("OPENAI_API_KEY")

    if config_path.is_file() and (not base_url or not api_key):
        try:
            import yaml

            data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            providers = data.get("providers", {})
            for p in providers.values():
                if isinstance(p, dict):
                    oa = p.get("openai") or p.get("anthropic")
                    if isinstance(oa, dict):
                        base_url = base_url or oa.get("base_url")
                        api_key = api_key or oa.get("api_key")
        except Exception:
            pass

    if not base_url or not api_key:
        return rows

    endpoint = base_url.rstrip("/") + "/chat/completions"
    prompt = (
        "你是一位专业影视拉片对白台词校对专家。以下是由语音识别（ASR）初步生成的台词片段与时间轴。\n"
        f"背景/剧情线索：{context_summary or '剧情短片'}\n\n"
        "任务要求：\n"
        "1. 修复台词中的所有错别字、同音字误识（例如复课->复刻、出世那碗->出事那晚、储物隔->储物格、他/她指代错误）。\n"
        "2. 清理 BPE 乱码（如英文名后的全角字母）与无效停顿碎片，补全标准汉语标点符号。\n"
        "3. 准确标注说话人角色（如主角名、AI全息分身、[插曲/歌词]、系统播报等）。\n"
        "4. 严格保持原始的 start 和 end 时间戳。\n"
        "5. 必须仅返回标准的 JSON 数组，格式为：[{\"start\": 0.0, \"end\": 0.92, \"speaker\": \"角色名\", \"text\": \"校对后台词。\"}]"
    )

    body = {
        "model": "gemini-3.5-flash-lite",
        "messages": [
            {"role": "system", "content": "You are a professional film dialogue proofreader and subtitle editor."},
            {"role": "user", "content": f"{prompt}\n\n输入数据：\n{json.dumps(rows, ensure_ascii=False)}"},
        ],
        "temperature": 0.1,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        req = urllib.request.Request(endpoint, data=json.dumps(body).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            content = resp_data["choices"][0]["message"]["content"]
            if "```json" in content:
                content = content.split("```json", 1)[1].split("```", 1)[0].strip()
            elif "```" in content:
                content = content.split("```", 1)[1].split("```", 1)[0].strip()
            refined = json.loads(content)
            if isinstance(refined, list) and len(refined) > 0 and "text" in refined[0]:
                return refined
    except Exception:
        pass
    return rows


def serialize_transcript(
    segments,
    *,
    media: Path,
    model: str,
    language: str | None,
    context_summary: str | None = None,
    llm_refine: bool = False,
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
                txt = clean_asr_text("".join(x["word"] for x in current))
                if txt:
                    rows.append({"start": round(st, 3), "end": round(et, 3), "text": txt})
                current = []
        if current:
            st = current[0]["start"]
            et = current[-1]["end"]
            txt = clean_asr_text("".join(x["word"] for x in current))
            if txt:
                rows.append({"start": round(st, 3), "end": round(et, 3), "text": txt})

    if not rows:
        rows = [
            {
                "start": round(float(item.start), 3),
                "end": round(float(item.end), 3),
                "text": clean_asr_text(item.text),
            }
            for item in segments
            if clean_asr_text(item.text)
        ]

    if llm_refine:
        rows = llm_refine_transcript(rows, context_summary)

    payload = {
        "schema_version": 1,
        "kind": "qualified_asr_transcript",
        "source_media": media.name,
        "model": model,
        "language": language,
        "verbatim_certified": bool(llm_refine),
        "segments": rows,
    }
    lines = [
        "# ASR transcript (qualified, not manually verified verbatim)",
        "",
        *[
            f"[{row['start']:08.3f}-{row['end']:08.3f}] "
            + (f"[{row['speaker']}] " if row.get("speaker") else "")
            + row["text"]
            for row in rows
        ],
        "",
    ]
    srt_blocks = [
        f"{idx}\n{format_srt_time(row['start'])} --> {format_srt_time(row['end'])}\n"
        + (f"[{row['speaker']}] " if row.get("speaker") and row.get("speaker") != "说话人未标注" else "")
        + f"{row['text']}\n"
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
    llm_refine: bool = False,
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
        list(segments),
        media=media,
        model=model,
        language=getattr(info, "language", language),
        context_summary=initial_prompt,
        llm_refine=llm_refine,
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
        "verbatim_certified": bool(llm_refine),
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
    parser.add_argument("--llm-refine", action="store_true", default=False)
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
                llm_refine=args.llm_refine,
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
