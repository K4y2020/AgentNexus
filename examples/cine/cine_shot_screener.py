"""Cine Agent - JEV text/metadata shot pre-screening & montage grammar engine.

Performs fast (~150ms), calibrated System One evaluations over candidate shot
descriptions. JEV receives text only; actual image/video quality still requires
Cine evidence inspection.

1. Metadata Usability Gate (Noul) -> flag incomplete or unusable descriptions
2. Cinematic & Narrative Intent (Score) -> score described visual dynamism and storytelling value
3. Framing Classification (Choice) -> extreme close-up, close-up, medium, wide, establishing
4. Camera Motion Classification (Choice) -> static, push/pull, pan/tilt, tracking
5. 🎬 剪辑手法与接戏潜质 (Choice: cut_technique) -> match cut, smash cut, j/l cut, jump cut, straight cut
6. 🎬 动势与轴线守卫 (Choice: motion_vector) -> left-to-right, right-to-left, towards, away, static
7. 🎬 视线方向 (Choice: eyeline_vector) -> look_left, look_right, direct, downcast, none
8. 🎬 适剪度与切点余量 (Score: editability_score, Noul: cut_on_action)
9. 🎬 镜头对剪接平滑度校验 (validate_cut_transition) -> 识别动势冲突、跳轴违规与砸切时机
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

try:
    from dotenv import load_dotenv

    load_dotenv()
    load_dotenv(Path(__file__).parent.parent.parent / ".env")
except ImportError:
    pass

from typesafe_sdk import Choice, Noul, Score, TypeSafeClient


def _open_ts_client(api_key: str):
    """Open a TypeSafe client that survives a misconfigured loopback proxy.

    The SDK otherwise inherits the ambient HTTP client, which fails with a bare
    SSL EOF when the machine routes through a plaintext proxy configured with
    an ``https://`` scheme. See ``jev_transport`` for the details.
    """
    import sys as _sys

    _pipeline = Path(__file__).resolve().parent / "skills" / "film-analysis" / "pipeline"
    if str(_pipeline) not in _sys.path:
        _sys.path.insert(0, str(_pipeline))
    from jev_transport import open_client

    return open_client(api_key)


@dataclass
class ShotEvaluation:
    shot_id: str
    description: str
    is_usable_probability: float
    framing: str
    framing_confidence: float
    camera_motion: str
    camera_motion_confidence: float
    cinematic_score: float
    cinematic_confidence: float
    # 剪辑手法与语法属性
    cut_technique: str
    cut_technique_confidence: float
    motion_vector: str
    motion_vector_confidence: float
    eyeline_vector: str
    eyeline_confidence: float
    editability_score: float
    cut_on_action_probability: float
    decision: Literal["ACCEPTED", "REJECTED", "NEEDS_REVIEW"]
    observations: List[str] = field(default_factory=list)


@dataclass
class CutTransitionCheck:
    shot_a_id: str
    shot_b_id: str
    recommended_cut_type: str
    motion_continuity: Literal["MATCHED", "OPPOSITE_CLASH", "PARALLEL", "NEUTRAL"]
    eyeline_continuity: Literal["CONVERGING", "DIVERGING", "PARALLEL", "NOT_APPLICABLE"]
    axis_jump_risk: bool
    transition_score: float
    advice: str


@dataclass
class BatchScreeningReport:
    total_shots: int
    accepted_count: int
    rejected_count: int
    review_needed_count: int
    usable_ratio: float
    average_cinematic_score: float
    average_editability_score: float
    shots: List[ShotEvaluation] = field(default_factory=list)
    by_framing: Dict[str, int] = field(default_factory=dict)
    by_motion: Dict[str, int] = field(default_factory=dict)
    by_cut_technique: Dict[str, int] = field(default_factory=dict)


class CineShotScreener:
    """Shot-description screener leveraging TypeSafe JEV with montage grammar."""

    def __init__(self, api_key: Optional[str] = None, model: str = "jev-latest"):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY")
        self.model = model
        if not self.api_key:
            raise ValueError(
                "TYPESAFE_API_KEY not found. Please provide an API key or set TYPESAFE_API_KEY in .env"
            )

    def _build_questions(self) -> dict:
        return {
            "is_usable": Noul(
                instructions="评估该镜头是否具备清晰的视觉主体与构图，非失焦晃动、纯黑屏、遮挡或技术残次，可作为剪辑成片素材？"
            ),
            "framing": Choice(
                instructions="选择该镜头的最适景别",
                criteria={
                    "extreme_close_up": "极特写（眼部/细微道具/局部微距）",
                    "close_up": "特写（面部表情或核心主体）",
                    "medium_shot": "中景（半身或双人互动）",
                    "wide_shot": "全景/远景（主体与环境关系）",
                    "establishing": "大远景/纯环境空镜头",
                },
            ),
            "camera_motion": Choice(
                instructions="选择主导运镜方式",
                criteria={
                    "static": "固定机位，机位无位移",
                    "pan_tilt": "原地摇移镜头（水平摇或垂直摇）",
                    "push_pull": "推镜头或拉镜头（向前贴近或向后拉开）",
                    "tracking": "移动跟拍/随动镜头",
                },
            ),
            "cinematic_score": Score(
                instructions="评估镜头的电影质感与视觉构图张力",
                criteria=[
                    "构图平庸/缺少焦点/无叙事价值",
                    "常规合格可用记录镜头",
                    "电影级质感/光影出彩/叙事张力极强",
                ],
            ),
            # --- 核心剪辑手法与语法 ---
            "cut_technique": Choice(
                instructions="从影视剪辑语法角度，评估该镜头最核心的剪接手法或转场潜质",
                criteria={
                    "match_cut": "动作匹配剪（Cut on action）或图形匹配，靠动势/形状顺滑衔接",
                    "smash_cut": "冲击砸切（Smash cut），利用极静与爆发、极明与极暗的剧烈声画反差制造冲击",
                    "j_l_cut": "声画交错剪辑（J-Cut/L-Cut），适合画外音先行引入或环境声跨镜头延展",
                    "jump_cut": "跳切/抽帧快切，适合高压紧迫感或时间快速压缩",
                    "straight_cut": "常规连续性硬切，平稳推进故事主线",
                },
            ),
            "motion_vector": Choice(
                instructions="判断画面中核心视觉主体或画面的动势方向（用于动作连续性与动势守卫）",
                criteria={
                    "left_to_right": "自左向右运动",
                    "right_to_left": "自右向左运动",
                    "towards_camera": "纵深迎面向镜头扑来或推进",
                    "away_from_camera": "纵深向画面远处离去或后退",
                    "static": "画面动势基本静止或对称居中",
                },
            ),
            "eyeline_vector": Choice(
                instructions="判断人物视线朝向（用于视线匹配 Eyeline Match 与轴线守卫）",
                criteria={
                    "look_left": "视线朝向画面左侧",
                    "look_right": "视线朝向画面右侧",
                    "direct_to_camera": "正脸直视镜头",
                    "downcast": "低头沉思或闭目垂眸",
                    "no_face": "无人物面部或背对镜头",
                },
            ),
            "cut_on_action": Noul(
                instructions="镜头内是否包含正在进行且未完成的明显肢体/物体动作，适合在动作半途直接下刀（Cut on Action）？"
            ),
            "editability_score": Score(
                instructions="从专业剪辑师视角评估该镜头的适剪度（接戏灵活性、节奏卡点容易度）",
                criteria=[
                    "接戏困难/动势僵硬/难以融入连贯序列",
                    "常规素材/需要精心寻找出入点",
                    "黄金剪切位/动势明确极易卡点/顺滑接戏",
                ],
            ),
        }

    def evaluate_shot(self, shot_id: str, description: str) -> ShotEvaluation:
        """Evaluate a single shot description with JEV including editing syntax."""
        questions = self._build_questions()

        with _open_ts_client(self.api_key) as client:
            resp = client.system_one(
                state={"shot_description": description},
                questions=questions,
                model=self.model,
            )

        is_usable = float(resp.nouls["is_usable"].noul)
        framing = resp.choices["framing"].choice
        framing_conf = float(resp.choices["framing"].confidence)
        motion = resp.choices["camera_motion"].choice
        motion_conf = float(resp.choices["camera_motion"].confidence)
        score = float(resp.scores["cinematic_score"].score)
        score_conf = float(resp.scores["cinematic_score"].confidence)

        # 剪辑手法分析
        cut_tech = resp.choices["cut_technique"].choice
        cut_tech_conf = float(resp.choices["cut_technique"].confidence)
        motion_vec = resp.choices["motion_vector"].choice
        motion_vec_conf = float(resp.choices["motion_vector"].confidence)
        eyeline_vec = resp.choices["eyeline_vector"].choice
        eyeline_conf = float(resp.choices["eyeline_vector"].confidence)
        edit_score = float(resp.scores["editability_score"].score)
        cut_action = float(resp.nouls["cut_on_action"].noul)

        # 综合初筛裁决
        if is_usable < 0.35 or score < 0.5:
            decision: Literal["ACCEPTED", "REJECTED", "NEEDS_REVIEW"] = "REJECTED"
        elif is_usable >= 0.65 and score >= 0.9 and framing_conf >= 0.6:
            decision = "ACCEPTED"
        else:
            decision = "NEEDS_REVIEW"

        observations = [
            f"景别: {framing} ({framing_conf:.2f}) | 运镜: {motion} ({motion_conf:.2f})",
            f"剪辑手法: {cut_tech} ({cut_tech_conf:.2f}) | 动势: {motion_vec} ({motion_vec_conf:.2f})",
            f"视线方向: {eyeline_vec} ({eyeline_conf:.2f}) | 动作卡点性: {cut_action:.2f}",
            f"电影感: {score:.2f}/2.0 | 适剪度: {edit_score:.2f}/2.0 | 可用性: {is_usable:.2f}",
        ]

        return ShotEvaluation(
            shot_id=shot_id,
            description=description,
            is_usable_probability=is_usable,
            framing=framing,
            framing_confidence=framing_conf,
            camera_motion=motion,
            camera_motion_confidence=motion_conf,
            cinematic_score=score,
            cinematic_confidence=score_conf,
            cut_technique=cut_tech,
            cut_technique_confidence=cut_tech_conf,
            motion_vector=motion_vec,
            motion_vector_confidence=motion_vec_conf,
            eyeline_vector=eyeline_vec,
            eyeline_confidence=eyeline_conf,
            editability_score=edit_score,
            cut_on_action_probability=cut_action,
            decision=decision,
            observations=observations,
        )

    def validate_cut_transition(
        self, shot_a: ShotEvaluation, shot_b: ShotEvaluation
    ) -> CutTransitionCheck:
        """Validate if two shots can be spliced together smoothly without montage clashes."""
        # 1. 动势连续性检查 (Motion Vector Continuity)
        opposites = {
            ("left_to_right", "right_to_left"),
            ("right_to_left", "left_to_right"),
            ("towards_camera", "away_from_camera"),
            ("away_from_camera", "towards_camera"),
        }
        if (shot_a.motion_vector, shot_b.motion_vector) in opposites:
            motion_cont: Literal["MATCHED", "OPPOSITE_CLASH", "PARALLEL", "NEUTRAL"] = "OPPOSITE_CLASH"
        elif shot_a.motion_vector == shot_b.motion_vector and shot_a.motion_vector != "static":
            motion_cont = "MATCHED"
        elif "static" in (shot_a.motion_vector, shot_b.motion_vector):
            motion_cont = "NEUTRAL"
        else:
            motion_cont = "PARALLEL"

        # 2. 视线朝向匹配 (Eyeline Continuity & 180-degree rule)
        if shot_a.eyeline_vector in ("look_left", "look_right") and shot_b.eyeline_vector in (
            "look_left",
            "look_right",
        ):
            if shot_a.eyeline_vector != shot_b.eyeline_vector:
                eyeline_cont: Literal["CONVERGING", "DIVERGING", "PARALLEL", "NOT_APPLICABLE"] = "CONVERGING"  # 对视匹配
                axis_jump = False
            else:
                eyeline_cont = "PARALLEL"  # 同向，若是两人对话可能存在跳轴
                axis_jump = True
        else:
            eyeline_cont = "NOT_APPLICABLE"
            axis_jump = False

        # 3. 剪辑手法适配度与建议
        advice_parts = []
        score = 1.0

        if shot_b.cut_technique == "smash_cut" or shot_a.cut_technique == "smash_cut":
            recommended_cut = "smash_cut"
            advice_parts.append("前静后动或声画突变，建议使用 0 帧硬砸切 (Smash Cut) 制造感官冲击。")
            score += 0.5
        elif shot_a.cut_on_action_probability > 0.7:
            recommended_cut = "match_cut"
            advice_parts.append("上一镜头包含强动势未完成动作，建议在动作爆发点做动作匹配切 (Cut on Action)。")
            score += 0.4
        elif shot_a.framing == shot_b.framing and abs(shot_a.cinematic_score - shot_b.cinematic_score) < 0.2:
            recommended_cut = "jump_cut_or_insert"
            advice_parts.append("两镜头景别与角度高度雷同，警惕骑轴跳切 (Jump Cut) 瑕疵，建议中间插入特写或采用溶解。")
            score -= 0.4
        else:
            recommended_cut = "straight_cut"
            advice_parts.append("常规连续性接戏，标准硬切即可。")

        if motion_cont == "OPPOSITE_CLASH":
            advice_parts.append("⚠️ 警惕动势对冲：前镜头主体动势与后镜头相反，除非故意制造对撞感，否则容易打乱观众视觉惯性。")
            score -= 0.5

        if axis_jump:
            advice_parts.append("⚠️ 疑似越轴风险：两人视线同向，若属正反打对话场景请复核轴线关系。")
            score -= 0.3

        return CutTransitionCheck(
            shot_a_id=shot_a.shot_id,
            shot_b_id=shot_b.shot_id,
            recommended_cut_type=recommended_cut,
            motion_continuity=motion_cont,
            eyeline_continuity=eyeline_cont,
            axis_jump_risk=axis_jump,
            transition_score=max(0.0, min(2.0, score)),
            advice=" ".join(advice_parts),
        )

    def screen_batch(self, shots: List[Dict[str, str]]) -> BatchScreeningReport:
        """Screen a batch of candidate shots and aggregate editing statistics."""
        results: List[ShotEvaluation] = []
        by_framing: Dict[str, int] = {}
        by_motion: Dict[str, int] = {}
        by_cut_tech: Dict[str, int] = {}

        for s in shots:
            shot_id = s.get("shot_id") or s.get("id", "unknown")
            desc = s.get("description") or s.get("desc", "")
            eval_res = self.evaluate_shot(shot_id, desc)
            results.append(eval_res)

            by_framing[eval_res.framing] = by_framing.get(eval_res.framing, 0) + 1
            by_motion[eval_res.camera_motion] = by_motion.get(eval_res.camera_motion, 0) + 1
            by_cut_tech[eval_res.cut_technique] = by_cut_tech.get(eval_res.cut_technique, 0) + 1

        total = len(results)
        accepted = sum(1 for r in results if r.decision == "ACCEPTED")
        rejected = sum(1 for r in results if r.decision == "REJECTED")
        review = sum(1 for r in results if r.decision == "NEEDS_REVIEW")
        avg_score = sum(r.cinematic_score for r in results) / total if total > 0 else 0.0
        avg_edit = sum(r.editability_score for r in results) / total if total > 0 else 0.0

        return BatchScreeningReport(
            total_shots=total,
            accepted_count=accepted,
            rejected_count=rejected,
            review_needed_count=review,
            usable_ratio=accepted / total if total > 0 else 0.0,
            average_cinematic_score=avg_score,
            average_editability_score=avg_edit,
            shots=results,
            by_framing=by_framing,
            by_motion=by_motion,
            by_cut_technique=by_cut_tech,
        )

    def export_to_cine_reviews(
        self,
        report: BatchScreeningReport,
        source_id: str,
        revision_id: str,
        output_path: Path,
    ) -> None:
        """Export screening results with montage grammar metadata to reviews/{revision}.json."""
        review_rows = []
        for s in report.shots:
            if s.decision == "ACCEPTED":
                status = "model_reviewed"
            elif s.decision == "REJECTED":
                status = "disputed"
            else:
                status = "unverified"

            review_rows.append(
                {
                    "source_id": source_id,
                    "revision_id": revision_id,
                    "shot_id": s.shot_id,
                    "status": status,
                    "observations": s.observations,
                    "image_receipt_ids": [f"receipt_{s.shot_id}"],
                    "metadata": {
                        "decision": s.decision,
                        "framing": s.framing,
                        "motion": s.camera_motion,
                        "score": s.cinematic_score,
                        "cut_technique": s.cut_technique,
                        "motion_vector": s.motion_vector,
                        "eyeline_vector": s.eyeline_vector,
                        "editability_score": s.editability_score,
                        "cut_on_action": s.cut_on_action_probability,
                    },
                }
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(review_rows, indent=2, ensure_ascii=False), encoding="utf-8")
