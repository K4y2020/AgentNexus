"""Film analysis review and scoring using TypeSafe JEV System One.

Evaluates an existing film analysis project revision across dialogue fidelity,
character coherence, dramatic causality, and overall adaptation readiness.
Produces a typed scorecard and actionable findings before handoff to production.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from .attribute_speakers import _find_api_key, open_client
except ImportError:
    from attribute_speakers import _find_api_key, open_client
from typesafe_sdk import Noul, Score


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

    rev_dir = project_dir / "revisions" / revision_id
    story_path = project_dir / "story" / f"{revision_id}.json"
    if not story_path.is_file():
        return {"status": "missing_story_draft", "revision": revision_id}

    story = json.loads(story_path.read_text(encoding="utf-8"))

    # Locate transcript (check inputs up 1 or 2 levels)
    transcript_candidates = [
        project_dir / "inputs" / "source-transcript.json",
        project_dir.parent / "inputs" / "source-transcript.json",
        project_dir.parent.parent / "inputs" / "source-transcript.json",
    ]
    if workspace:
        transcript_candidates.insert(0, Path(workspace) / "inputs" / "source-transcript.json")

    transcript_path = next((p for p in transcript_candidates if p.is_file()), None)
    transcript = {}
    if transcript_path:
        try:
            transcript = json.loads(transcript_path.read_text(encoding="utf-8-sig"))
        except Exception:
            pass

    segments = transcript.get("segments") or []
    lines_sample = [
        f"[{s.get('speaker', '?')}] {s.get('text', '')}"
        for s in segments
        if s.get("text")
    ]

    state: dict[str, Any] = {
        "summary": story.get("summary", {}),
        "characters": [c[:100] for c in story.get("characters", [])[:8]],
        "sections": [
            {
                "batch_id": s.get("batch_id"),
                "events": s.get("events", []),
                "uncertainties": s.get("uncertainties", []),
            }
            for s in story.get("sections", [])
        ],
        "transcript_lines": lines_sample[:25],
    }

    questions = {
        "dialogue_fidelity": Score(
            instructions=(
                "评估该拉片产物中的台词与故事节拍因果。对白归属是否存在角色错位"
                "（例如把对方说的台词归给主角自己，导致自己跟自己对戏、因果倒置）？"
            ),
            criteria=[
                "对白归属严重颠倒错位，因果逻辑混乱（例如自己向自己自称嫂子然后回敬我爸姓林）",
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
                "在给定的台词或节拍中，是否存在明显的对白归属倒错（例如王熙凤说的「妹妹好这是嫂子」"
                "被误划归给了林黛玉，导致黛玉自说自话「我爸姓林」）？"
            )
        ),
    }

    key = (api_key or _find_api_key()).strip()
    if not key:
        return {"status": "skipped", "reason": "no_typesafe_key"}

    with open_client(key) as client:
        resp = client.system_one(state=state, questions=questions, model=model)

    scores = {}
    for name in ("dialogue_fidelity", "character_coherence", "story_causality", "adaptation_readiness"):
        ans = resp.answers.get(name)
        score_val = float(getattr(ans, "score", 1.0) or 1.0)
        conf_val = float(getattr(ans, "confidence", 0.0) or 0.0)
        scores[name] = {"score": score_val, "confidence": conf_val}

    inversion_noul = float(getattr(resp.answers.get("dialogue_inversion_detected"), "noul", 0.0) or 0.0)

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

    findings = []
    if inversion_noul > 0.40:
        findings.append({
            "severity": "high",
            "category": "dialogue_inversion",
            "message": "检测到对白角色归属倒错（存在将对手戏台词错判给主角自己的因果矛盾，如'妹妹好这是嫂子'与'我爸姓林'对接异常）。",
        })
    if scores["dialogue_fidelity"]["score"] < 1.2:
        findings.append({
            "severity": "medium",
            "category": "dialogue_fidelity",
            "message": "台词对答因果评分较低，建议重跑 JEV 归属或结合 cast.json 进行二次复核。",
        })
    if scores["character_coherence"]["score"] < 1.2:
        findings.append({
            "severity": "medium",
            "category": "character_coherence",
            "message": "角色实体与称谓闭环度不足，部分次要角色称谓未与实体绑定。",
        })

    has_high_defect = any(f["severity"] == "high" for f in findings)
    if total_score >= 75.0 and not has_high_defect:
        verdict = "ready"
    elif total_score >= 60.0:
        verdict = "needs_repair" if has_high_defect else "usable_with_risks"
    else:
        verdict = "blocked"
        findings.append({
            "severity": "medium",
            "category": "dialogue_fidelity",
            "message": "台词对答因果评分较低，建议重跑 JEV 归属或结合 cast.json 进行二次复核。",
        })
    if scores["character_coherence"]["score"] < 1.2:
        findings.append({
            "severity": "medium",
            "category": "character_coherence",
            "message": "角色实体与称谓闭环度不足，部分次要角色称谓未与实体绑定。",
        })

    report = {
        "status": "reviewed",
        "project": str(project_dir.name),
        "revision_id": revision_id,
        "total_score": total_score,
        "verdict": verdict,
        "scores": scores,
        "dialogue_inversion_probability": inversion_noul,
        "findings": findings,
        "model": getattr(resp, "model", model),
    }

    # Save to disk
    review_file = rev_dir / "film_review.json"
    review_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    top_file = project_dir / "film_review.json"
    top_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return report
