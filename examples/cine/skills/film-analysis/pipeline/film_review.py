"""Film analysis review and scoring using TypeSafe JEV System One.

Evaluates an existing film analysis project revision across dialogue fidelity,
character coherence, dramatic causality, and overall adaptation readiness.
Produces a typed scorecard and actionable findings before handoff to production.

The questions describe defects in general terms. They never quote a particular
story's lines: an example taken from one production reads, to the judge, like a
claim about whatever production is being reviewed.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

try:
    from .attribute_speakers import _find_api_key, open_client
    from .draft_check import story_draft_gaps
    from .source_identity import fingerprint_of, transcript_matches_source
except ImportError:
    from attribute_speakers import _find_api_key, open_client
    from draft_check import story_draft_gaps
    from source_identity import fingerprint_of, transcript_matches_source
from typesafe_sdk import Noul, Score

SCORE_NAMES = (
    "dialogue_fidelity",
    "character_coherence",
    "story_causality",
    "adaptation_readiness",
)
# A score JEV is unsure of cannot support a "ready" verdict.
MIN_SCORE_CONFIDENCE = 0.5
# Every Score question below has three criteria levels, scored 0 to 2.
MAX_SCORE = 2.0
_MAX_TRANSCRIPT_LINES = 25
_MAX_CHARACTERS = 8
_MAX_LISTED_GAPS = 6
_REVISION_ID = re.compile(r"[A-Za-z0-9_-]+")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _bounded(value: Any, upper: float) -> float | None:
    """``value`` if it is a finite number in [0, upper], otherwise None.

    NaN compares false with every threshold, so letting it through would skip
    the checks that keep a draft from being called ready.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and 0.0 <= number <= upper else None


def _character_summary(character: Any) -> str:
    if isinstance(character, str):
        return character[:100]
    return json.dumps(character, ensure_ascii=False)[:200]


def _transcript_line(segment: dict[str, Any]) -> str:
    speaker = segment.get("speaker") or "?"
    # An attribution the pipeline flagged is not settled; say so, or the judge
    # treats a guess as the source's own labelling.
    if segment.get("needs_review"):
        speaker = f"{speaker}（待复核）"
    return f"[{speaker}] {segment.get('text', '')}"


def review_film_analysis(
    project_dir: Path,
    workspace: Path | None = None,
    *,
    api_key: str | None = None,
    model: str = "jev-latest",
) -> dict[str, Any]:
    """Score a film analysis project and report critical defects.

    Args:
        project_dir: Root directory of the analysis project.
        workspace: Workspace root (containing inputs/); inferred if omitted.
        api_key: Optional TYPESAFE_API_KEY.
        model: JEV model ID.
    """
    project_dir = project_dir.resolve()
    manifest_path = project_dir / "project.json"
    if not manifest_path.is_file():
        return {"status": "missing_project", "path": str(project_dir)}

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    revision_id = manifest.get("current_revision")
    if not revision_id:
        return {"status": "no_revision", "path": str(project_dir)}
    if not isinstance(revision_id, str) or not _REVISION_ID.fullmatch(revision_id):
        return {"status": "invalid_revision", "path": str(project_dir)}

    rev_dir = project_dir / "revisions" / revision_id
    story_path = project_dir / "story" / f"{revision_id}.json"
    if not story_path.is_file():
        return {"status": "missing_story_draft", "revision": revision_id}

    story = json.loads(story_path.read_text(encoding="utf-8"))
    source = _read_json(project_dir / "source.json")

    # Deterministic structure first: a JEV score, however high, cannot make an
    # unfilled draft ready. This does not check images or claim visual review.
    story_gaps = story_draft_gaps(
        story,
        _read_json(rev_dir / "story_plan.json"),
        revision_id=revision_id,
        source_id=source.get("source_id") if isinstance(source, dict) else None,
    )

    # Locate transcript (check inputs up 1 or 2 levels)
    transcript_candidates = [
        project_dir / "inputs" / "source-transcript.json",
        project_dir.parent / "inputs" / "source-transcript.json",
        project_dir.parent.parent / "inputs" / "source-transcript.json",
    ]
    if workspace:
        transcript_candidates.insert(0, Path(workspace) / "inputs" / "source-transcript.json")

    transcript_path = next((p for p in transcript_candidates if p.is_file()), None)
    transcript: Any = {}
    transcript_status = "absent"
    if transcript_path:
        transcript = _read_json(transcript_path)
        if not isinstance(transcript, dict):
            transcript_status = "unreadable"
        elif transcript_matches_source(transcript, source):
            transcript_status = "verified"
        elif fingerprint_of(transcript.get("source_fingerprint")) is None:
            transcript_status = "unverified_source"
        else:
            transcript_status = "source_mismatch"
    # Only a transcript of the committed source is scored as its dialogue.
    if transcript_status != "verified":
        transcript = {}

    segments = (transcript.get("segments") or []) if isinstance(transcript, dict) else []
    lines_sample = [
        _transcript_line(s) for s in segments if isinstance(s, dict) and s.get("text")
    ]
    unsettled = sum(1 for s in segments if isinstance(s, dict) and s.get("needs_review"))

    state: dict[str, Any] = {
        "summary": story.get("summary", {}),
        "characters": [
            _character_summary(c) for c in (story.get("characters") or [])[:_MAX_CHARACTERS]
        ],
        "sections": [
            {
                "batch_id": s.get("batch_id"),
                "events": s.get("events", []),
                "uncertainties": s.get("uncertainties", []),
            }
            for s in story.get("sections", [])
        ],
        "transcript_lines": lines_sample[:_MAX_TRANSCRIPT_LINES],
    }

    questions = {
        "dialogue_fidelity": Score(
            instructions=(
                "评估该拉片产物中的台词与故事节拍因果。对白归属是否存在角色错位"
                "（例如把对方说的台词归给主角自己，导致自己跟自己对戏、因果倒置）？"
                "标注「待复核」的说话人是未确定的归属，不要当作已确认的事实。"
            ),
            criteria=[
                "对白归属严重颠倒错位，因果逻辑混乱（例如角色用称呼叫自己、回答自己刚说的话）",
                "对白基本可用，但存在局部角色混乱或画外音混淆",
                "对白归属与交锋因果完全准确自洽",
            ],
        ),
        "character_coherence": Score(
            instructions="评估提取出的角色实体与人物关系的自洽度。主要角色与称谓是否闭环，有无未解释的幽灵角色？",
            criteria=[
                "角色设定混乱，存在割裂或幽灵角色",
                "角色大体清楚，但部分次要角色或亲属关系交代不完整",
                "角色实体清晰，亲属关系闭环，戏剧职能明确",
            ],
        ),
        "story_causality": Score(
            instructions="评估故事梗概与节拍因果承接。起因、冲突、转折点与结局是否形成严密的戏剧因果链？",
            criteria=[
                "故事因果断裂，节拍散乱不成链",
                "因果基本连续，但局部转折突兀或动机交代薄弱",
                "戏剧因果紧密，矛盾推进与转折连贯自然",
            ],
        ),
        "adaptation_readiness": Score(
            instructions="综合评估该拉片底稿对下游剧本和分镜改编的整体就绪度。",
            criteria=[
                "未就绪（Blocked）：存在关键对白错位或因果断层，不可直接用于改编",
                "带风险可用（Usable with risks）：核心主线成立，但存在需要修复的关键疑点",
                "完全就绪（Ready for Adaptation）：高质量拉片底稿，可直接进入剧本改编",
            ],
        ),
        "dialogue_inversion_detected": Noul(
            instructions=(
                "在给定的台词或节拍中，是否存在明显的对白归属倒错：一句台词被归给了它所称呼、"
                "回应或谈论的那个人，导致角色自己称呼自己、自己回答自己，或一段交锋的因果前后颠倒？"
            )
        ),
    }

    key = (api_key or _find_api_key()).strip()
    if not key:
        return {"status": "skipped", "reason": "no_typesafe_key"}

    with open_client(key) as client:
        resp = client.system_one(state=state, questions=questions, model=model)

    answers = getattr(resp, "answers", None) or {}
    scores: dict[str, dict[str, float | None]] = {}
    for name in SCORE_NAMES:
        ans = answers.get(name)
        raw_score = getattr(ans, "score", None) if ans is not None else None
        if raw_score is None:
            raise ValueError(f"JEV response is missing score: {name}")
        score_val = _bounded(raw_score, MAX_SCORE)
        if score_val is None:
            # NaN slips past every threshold and an above-scale score inflates
            # the total; neither can be weighted into a verdict.
            raise ValueError(f"JEV response has a malformed score for {name}: {raw_score!r}")
        raw_confidence = getattr(ans, "confidence", None)
        # An unusable confidence is reported as unknown (null) and counts as
        # uncertain below; it is never clamped into a confident one.
        conf_val = 0.0 if raw_confidence is None else _bounded(raw_confidence, 1.0)
        scores[name] = {"score": score_val, "confidence": conf_val}

    inversion = answers.get("dialogue_inversion_detected")
    raw_inversion = getattr(inversion, "noul", None) if inversion is not None else None
    if raw_inversion is None:
        raise ValueError("JEV response is missing dialogue_inversion_detected")
    inversion_noul = _bounded(raw_inversion, 1.0)
    if inversion_noul is None:
        raise ValueError(
            f"JEV response has a malformed dialogue_inversion_detected: {raw_inversion!r}"
        )

    # 100-point composite calculation
    # Weights: dialogue (35%), causality (25%), characters (20%), readiness (20%)
    raw_100 = (
        (scores["dialogue_fidelity"]["score"] / 2.0) * 35.0
        + (scores["story_causality"]["score"] / 2.0) * 25.0
        + (scores["character_coherence"]["score"] / 2.0) * 20.0
        + (scores["adaptation_readiness"]["score"] / 2.0) * 20.0
    )
    # Penalty for detected inversion
    if inversion_noul > 0.60:
        raw_100 = min(raw_100, 65.0)

    total_score = round(raw_100, 1)

    findings: list[dict[str, str]] = []

    def finding(severity: str, category: str, message: str) -> None:
        if not any(f["category"] == category and f["message"] == message for f in findings):
            findings.append({"severity": severity, "category": category, "message": message})

    dialogue_message = "台词对答因果评分较低，建议重跑 JEV 归属或结合 cast.json 进行二次复核。"
    if inversion_noul > 0.40:
        finding(
            "high",
            "dialogue_inversion",
            "检测到对白角色归属倒错（有台词被归给了它所称呼或回应的人，造成自说自话或因果颠倒）。",
        )
    if scores["dialogue_fidelity"]["score"] < 1.2:
        finding(
            "high" if scores["dialogue_fidelity"]["score"] < 0.5 else "medium",
            "dialogue_fidelity",
            dialogue_message,
        )
    if scores["character_coherence"]["score"] < 1.2:
        finding(
            "medium",
            "character_coherence",
            "角色实体与称谓闭环度不足，部分次要角色称谓未与实体绑定。",
        )
    uncertain = [
        name
        for name in SCORE_NAMES
        if scores[name]["confidence"] is None or scores[name]["confidence"] < MIN_SCORE_CONFIDENCE
    ]
    if uncertain:
        finding(
            "medium",
            "low_confidence",
            "JEV 对以下评分把握不足，结论需人工复核："
            + "、".join(
                f"{name}（置信度无效）" if scores[name]["confidence"] is None else name
                for name in uncertain
            ),
        )
    if unsettled:
        finding(
            "medium",
            "attribution_unsettled",
            f"源转写有 {unsettled} 句说话人归属待复核，对白相关评分以此为前提。",
        )
    if story_gaps:
        listed = "、".join(story_gaps[:_MAX_LISTED_GAPS])
        more = f" 等 {len(story_gaps)} 项" if len(story_gaps) > _MAX_LISTED_GAPS else ""
        finding(
            "high",
            "story_incomplete",
            f"拉片底稿尚未填完（{listed}{more}），评分只作诊断，不能据此判定就绪。",
        )
    if transcript_status in {"source_mismatch", "unverified_source", "unreadable"}:
        finding(
            "medium",
            "transcript_source_mismatch",
            "找到的源转写无法确认属于当前源视频（指纹不符、未记录或文件不可读），"
            "未参与评分；请为当前视频重新转写。",
        )

    has_high_defect = any(f["severity"] == "high" for f in findings)
    # An unresolved speaker attribution is an open question about the source
    # itself; the draft cannot be ready for adaptation until it is settled.
    # Neither can an unfilled draft, or one whose only transcript is of
    # another video.
    if (
        total_score >= 75.0
        and not has_high_defect
        and not uncertain
        and not unsettled
        and not story_gaps
        and transcript_status in {"absent", "verified"}
    ):
        verdict = "ready"
    elif total_score >= 60.0:
        verdict = "needs_repair" if has_high_defect else "usable_with_risks"
    else:
        verdict = "blocked"
        finding("medium", "dialogue_fidelity", dialogue_message)

    report = {
        "status": "reviewed",
        "project": str(project_dir.name),
        "revision_id": revision_id,
        "total_score": total_score,
        "verdict": verdict,
        "scores": scores,
        "dialogue_inversion_probability": inversion_noul,
        "findings": findings,
        # Structure only: fields and plan batches filled in. Image receipts and
        # adaptation readiness are cine_verify_report's to check.
        "story_completeness": {"complete": not story_gaps, "gaps": story_gaps},
        "transcript_status": transcript_status,
        "model": getattr(resp, "model", model),
    }

    # Save to disk. Strict JSON: a NaN reaching the report is a bug, not a value.
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    review_file = rev_dir / "film_review.json"
    review_file.write_text(text, encoding="utf-8")
    top_file = project_dir / "film_review.json"
    top_file.write_text(text, encoding="utf-8")

    return report
