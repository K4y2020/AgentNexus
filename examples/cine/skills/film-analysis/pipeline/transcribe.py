"""Create a qualified local ASR transcript for Cine source analysis."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

try:
    from .source_identity import cache_key, media_fingerprint
except ImportError:  # loaded as a standalone module, outside the package
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from source_identity import cache_key, media_fingerprint

# Speaker label the refine pass uses for an unknown speaker; not written to SRT.
_UNLABELLED = "说话人未标注"


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
    ms = round((sec - int(sec)) * 1000)
    if ms >= 1000:
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def render_plain(rows: list[dict]) -> str:
    """The human-readable ``.txt`` form of transcript rows."""
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
    return "\n".join(lines)


def render_srt(rows: list[dict]) -> str:
    """The ``.srt`` form of transcript rows (same rows, same speakers)."""
    return "\n".join(
        f"{idx}\n{format_srt_time(row['start'])} --> {format_srt_time(row['end'])}\n"
        + (
            f"[{row['speaker']}] "
            if row.get("speaker") and row.get("speaker") != _UNLABELLED
            else ""
        )
        + f"{row['text']}\n"
        for idx, row in enumerate(rows, 1)
    )


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError):
        return None


def sync_srt_mirror(mirror: Path, previous_generated: str | None, srt_text: str) -> str:
    """Keep ``inputs/source.srt`` in step with the machine SRT, unless it is not ours.

    ``source.srt`` may be a subtitle file a person supplied — possibly the
    trusted one. It is created when absent, and replaced only while it still
    holds exactly what this tool generated last time (``previous_generated``).
    Anything else is preserved. Returns created/updated/unchanged/preserved.
    """
    if not mirror.exists():
        _atomic_text(mirror, srt_text)
        return "created"
    current = _read_text(mirror)
    if current == srt_text:
        return "unchanged"
    if previous_generated is not None and current == previous_generated:
        _atomic_text(mirror, srt_text)
        return "updated"
    return "preserved"


def clean_asr_text(text: str) -> str:
    """Clean Whisper BPE fullwidth artifact characters and normalise punctuation.

    Only story-independent repairs belong here. Which word a homophone should
    have been depends on the source, so that is left to the context-aware
    refine pass instead of a fixed substitution table that rewrites every
    other story's dialogue.
    """
    import re

    # Clean Whisper BPE fullwidth artifacts after English words (e.g. "JakenＢ" -> "Jaken，")
    text = re.sub(r"([a-zA-Z]+)[Ａ-Ｚａ-ｚ]", r"\1，", text)
    # Remove any remaining isolated fullwidth latin letters
    text = re.sub(r"[Ａ-Ｚａ-ｚ]", "", text)
    text = text.replace("、", "，")
    return text.strip().strip("，,。！？!?；; ")


def context_from_cast(raw: str) -> str:
    """Build an attribution context from a cast document.

    Emits relationships, not a name list: the model needs to know who is whose
    parent, spouse or sibling to tell which character a kinship address term
    points at.
    """
    try:
        cast = json.loads(raw)
    except ValueError:
        return raw
    characters = cast.get("characters") if isinstance(cast, dict) else None
    if not isinstance(characters, list):
        return raw
    lines = []
    for character in characters:
        if not isinstance(character, dict):
            continue
        name = character.get("name") or character.get("id")
        if not name:
            continue
        parts = []
        persona = character.get("persona") or {}
        # A group speaks in unison, and its lines otherwise read as one
        # person's thoughts. Say so, or they get labelled as narration.
        if str(persona.get("gender") or "") in {"群体", "群像"} or "群体" in str(name):
            parts.append("群体角色，台词由多人齐声念白，不是旁白也不是某个人的内心话")
        if character.get("aliases"):
            parts.append("别名" + "、".join(str(a) for a in character["aliases"]))
        for field in ("identity", "gender", "ageRange"):
            if persona.get(field):
                parts.append(str(persona[field]))
        if character.get("oneLiner"):
            parts.append(str(character["oneLiner"]))
        relations = [
            f"与{r['name']}：{r['relation']}"
            for r in persona.get("relationships") or []
            if isinstance(r, dict) and r.get("name") and r.get("relation")
        ]
        if relations:
            parts.append("，".join(relations))
        lines.append(f"{name}（{'；'.join(parts)}）" if parts else str(name))
    if not lines:
        return raw
    return (
        "人物设定与关系：\n"
        + "\n".join(f"- {line}" for line in lines)
        + "\n判断说话人时依据这些关系与台词中的称呼词，不要按人物出现顺序指派。"
    )


def _usable_context(context_summary: str | None) -> str:
    """Reject a bare name list as the attribution context.

    A whitespace-separated list of names says which characters may appear but
    not who addresses whom, so the model falls back to list order. Refusing it
    is better than silently producing wrong speaker labels.
    """
    if not context_summary:
        return "剧情短片（未提供人物关系，说话人按台词可确认的称呼命名）"
    text = context_summary.strip()
    if _looks_like_name_list(text):
        return (
            "剧情短片。注意：调用方传入的是人名清单而非人物关系，"
            "无法据此判断谁在跟谁说话——请严格依据台词中的称呼词与交锋逻辑判断，"
            "不要按人名顺序或出现频率指派。"
        )
    return text


def _looks_like_name_list(text: str) -> bool:
    """True when the context is just names separated by spaces or 、,。

    Detection is structural rather than keyword-based: a name may itself contain
    a kinship character (a name ending in 母 or 爸, say), so matching on those
    words alone misclassifies a real name list.
    """
    tokens = [t for t in text.replace("、", " ").replace(",", " ").split() if t]
    if len(tokens) < 3:
        return False
    # A relational sentence uses clause punctuation; a list rarely has any.
    if any(p in text for p in "，。：；("):
        return False
    # Names are short and carry no verbs; a description is longer and does.
    if any(len(t) > 6 for t in tokens):
        return False
    return all(len(t) <= 6 for t in tokens)


def llm_refine_transcript(
    rows: list[dict],
    context_summary: str | None = None,
    *,
    speakers: bool = True,
) -> list[dict]:
    """Refine ASR dialogue segments using an LLM via the AgentNexus gateway.

    ``speakers=False`` restricts the pass to text repair (homophones, BPE
    artifacts, punctuation) and leaves speaker labels untouched, so attribution
    can be done by JEV instead. Attribution is a local judgment with a
    calibrated answer, and JEV does it in about a second without needing a cast
    document; the generative pass is slower and needs relationships supplied.

    ``context_summary`` should state how the characters relate to one another.
    A bare list of names is actively harmful here: it tells the model which
    names may appear without saying who addresses whom, so speaker labels get
    assigned by list order and surface cues instead of by kinship — including
    lines credited to a listed character who never appears in the episode.
    """
    if not rows:
        return rows
    import urllib.request

    config_path = Path.home() / ".agentnexus/config.yaml"
    base_url = os.environ.get("AGENTNEXUS_GATEWAY_URL") or os.environ.get("OPENAI_BASE_URL")
    api_key = os.environ.get("AGENTNEXUS_API_KEY") or os.environ.get("OPENAI_API_KEY")
    model_id = os.environ.get("AGENTNEXUS_TRANSCRIBE_MODEL")

    if config_path.is_file() and (not base_url or not api_key or not model_id):
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
                        models = oa.get("models")
                        if isinstance(models, dict):
                            model_id = model_id or models.get("default")
        except Exception:  # noqa: BLE001 — optional local config
            pass

    if not base_url or not api_key or not model_id:
        return rows

    endpoint = base_url.rstrip("/") + "/chat/completions"
    if speakers:
        attribution_rules = (
            "3. 标注说话人：依据上面给出的人物关系判断谁在说话。称呼词指称的是听者不是说话者"
            "（用某个称呼叫人的，是在跟被这样称呼的人说话）；齐声念白归给群体角色；"
            "无人回应的独白/旁白标为「旁白」。人物关系没有覆盖到的角色，用台词里能确认的称呼命名；"
            "无法确认的标为「说话人未标注」，不要猜。\n"
            "4. 多人抢话与长切片拆分（核心）：凡是一段 ASR 内包含多个人物发言或由多句构成的"
            "（几个人的话被识别进同一长句），必须按语义边界拆解为独立的"
            "单人发言条目，细化分段起止时间（总范围覆盖原区间），严禁多人拼在同一条目中。\n"
            "5. 理顺对话因果：说话人的指派必须符合人物关系与交锋逻辑（每一句回应的是哪一句、"
            "是对谁说的，严禁因果颠倒或角色错位）。\n"
            "6. 必须仅返回标准的 JSON 数组，格式为："
            '[{"start": 0.0, "end": 0.92, "speaker": "角色名", "text": "校对后台词。"}]'
        )
    else:
        # Text repair only. Attribution is left to JEV, which returns a
        # calibrated answer without needing a cast document.
        attribution_rules = (
            "3. 只做文本修复，不要判断也不要改动说话人——说话人字段原样保留。"
            "不要合并或拆分条目，保持条数与起止时间不变，只替换 text 字段。\n"
            "4. 必须仅返回标准的 JSON 数组，格式为："
            '[{"start": 0.0, "end": 0.92, "speaker": "原样保留", "text": "校对后台词。"}]'
        )
    prompt = (
        "你是一位专业影视拉片对白台词校对专家。以下是由语音识别（ASR）初步生成的台词片段与时间轴。\n"
        f"人物关系与剧情线索：{_usable_context(context_summary)}\n\n"
        "任务要求：\n"
        "1. 修复台词中的错别字与同音字误识（人名、称呼与专有名词以上面的人物设定为准；"
        "他/她指代错误）。只改有把握的错字，不要润色或改写原话。\n"
        "2. 清理 BPE 乱码（如英文名后的全角字母）与无效停顿碎片，补全标准汉语标点符号。\n"
        + attribution_rules
    )

    body = {
        "model": model_id,
        "messages": [
            {
                "role": "system",
                "content": "You are a professional film dialogue proofreader and subtitle editor.",
            },
            {
                "role": "user",
                "content": f"{prompt}\n\n输入数据：\n{json.dumps(rows, ensure_ascii=False)}",
            },
        ],
        "temperature": 0.1,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        req = urllib.request.Request(
            endpoint, data=json.dumps(body).encode("utf-8"), headers=headers
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            content = resp_data["choices"][0]["message"]["content"]
            if "```json" in content:
                content = content.split("```json", 1)[1].split("```", 1)[0].strip()
            elif "```" in content:
                content = content.split("```", 1)[1].split("```", 1)[0].strip()
            refined = json.loads(content)
    except Exception:
        return rows
    return _accepted_refinement(rows, refined, speakers=speakers)


def _accepted_refinement(rows: list[dict], refined, *, speakers: bool) -> list[dict]:
    """Return the refined rows only when the reply keeps the contract it was given.

    A reply that is not a list of timed rows with text is dropped. A text-only
    pass must also return the same rows with the same timings; only ``text``
    is taken from it, so speaker fields and anything else stay exactly as they
    were. A reply that merged, split or dropped lines cannot be trusted to have
    kept the dialogue, so the ASR rows are kept instead.
    """
    if not isinstance(refined, list) or not refined:
        return rows
    for row in refined:
        if not isinstance(row, dict) or not str(row.get("text") or "").strip():
            return rows
        try:
            float(row["start"])
            float(row["end"])
        except (KeyError, TypeError, ValueError):
            return rows
    if speakers:
        return refined
    if len(refined) != len(rows):
        return rows
    kept = []
    for original, row in zip(rows, refined, strict=True):
        if (
            abs(float(row["start"]) - float(original["start"])) > 0.01
            or abs(float(row["end"]) - float(original["end"])) > 0.01
        ):
            return rows
        kept.append({**original, "text": str(row["text"]).strip()})
    return kept


def serialize_transcript(
    segments,
    *,
    media: Path,
    model: str,
    language: str | None,
    context_summary: str | None = None,
    llm_refine: bool = False,
    refine_speakers: bool = True,
    source_fingerprint: dict | None = None,
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

    refinement = "not_requested"
    if llm_refine:
        refined = llm_refine_transcript(rows, context_summary, speakers=refine_speakers)
        # A failed, rejected or no-op pass hands back the ASR rows unchanged.
        refinement = "applied" if refined != rows else "not_applied"
        rows = refined

    payload = {
        "schema_version": 1,
        "kind": "qualified_asr_transcript",
        "source_media": media.name,
        "model": model,
        "language": language,
        # Neither raw ASR nor a generative repair pass certifies the words
        # verbatim; only a check against the audio by a person could.
        "verbatim_certified": False,
        "text_refinement": refinement,
        "segments": rows,
    }
    if source_fingerprint is not None:
        # The identity of the file these words came from, in source.json's
        # terms. Readers compare it with the committed source; a file name
        # (often just "source.mp4") cannot tell two videos apart.
        payload["source_fingerprint"] = dict(source_fingerprint)
    return (
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        render_plain(rows),
        render_srt(rows),
    )


def transcribe(
    media: Path,
    output: Path,
    workspace: Path,
    *,
    model: str,
    language: str | None,
    initial_prompt: str | None = None,
    context: str | None = None,
    separate_vocals: bool = False,
    llm_refine: bool = False,
    refine_speakers: bool = True,
) -> dict:
    media = _inside(workspace, media)
    if not media.is_file():
        raise FileNotFoundError(media)
    output = _inside(workspace, output)
    fingerprint = media_fingerprint(media)

    audio_to_transcribe = media
    if separate_vocals:
        # Keyed by the source's identity: a separation cached for another video
        # in this workspace (even one with the same file name) is never reused.
        sep_root = _inside(workspace, workspace / "inputs" / "separated" / cache_key(fingerprint))
        vocals_file = sep_root / "htdemucs" / "audio" / "vocals.wav"
        if not vocals_file.is_file():
            import subprocess

            # Extract audio first
            sep_root.mkdir(parents=True, exist_ok=True)
            tmp_audio = sep_root / "audio.wav"
            subprocess.run(
                [
                    "ffmpeg",
                    "-i",
                    str(media),
                    "-vn",
                    "-ar",
                    "44100",
                    "-ac",
                    "2",
                    str(tmp_audio),
                    "-y",
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "uv", "run", "--with", "demucs", "--with", "torch", "--with", "numpy<2",
                    "demucs", "--two-stems", "vocals", "-n", "htdemucs",
                    "-o", str(sep_root), str(tmp_audio),
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
        context_summary=context,
        llm_refine=llm_refine,
        refine_speakers=refine_speakers,
        source_fingerprint=fingerprint,
    )
    json_path = output.with_suffix(".json")
    text_path = output.with_suffix(".txt")
    srt_path = output.with_suffix(".srt")
    previous_srt = _read_text(srt_path)
    _atomic_text(json_path, json_text)
    _atomic_text(text_path, plain_text)
    _atomic_text(srt_path, srt_text)

    # inputs/source.srt mirrors the machine SRT only while it is the mirror;
    # a subtitle someone put there is left alone.
    mirror = None
    if output.parent.name == "inputs" and output.name != "source":
        mirror = sync_srt_mirror(output.parent / "source.srt", previous_srt, srt_text)

    return {
        "status": "qualified_asr_complete",
        "json_path": str(json_path),
        "text_path": str(text_path),
        "srt_path": str(srt_path),
        "verbatim_certified": False,
        "text_refinement": json.loads(json_text)["text_refinement"],
        "source_fingerprint": fingerprint,
        "srt_mirror": mirror,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--media", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--language", default="zh")
    parser.add_argument(
        "--initial-prompt",
        default=None,
        help="Vocabulary bias for Whisper only (a name list is fine here).",
    )
    parser.add_argument(
        "--context",
        type=Path,
        default=None,
        help="Cast document or text file describing how characters relate; "
        "used for speaker attribution. Do not pass a bare name list.",
    )
    parser.add_argument("--separate-vocals", action="store_true", default=False)
    parser.add_argument("--llm-refine", action="store_true", default=False)
    parser.add_argument(
        "--text-only-refine",
        action="store_true",
        default=False,
        help="Repair text only; leave speaker labels for JEV attribution.",
    )
    args = parser.parse_args()
    context = None
    if args.context is not None:
        raw = args.context.read_text(encoding="utf-8-sig")
        context = context_from_cast(raw) if args.context.suffix == ".json" else raw
    print(
        json.dumps(
            transcribe(
                args.media,
                args.output,
                args.workspace,
                model=args.model,
                language=args.language,
                initial_prompt=args.initial_prompt,
                context=context,
                separate_vocals=args.separate_vocals,
                llm_refine=args.llm_refine,
                refine_speakers=not args.text_only_refine,
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
