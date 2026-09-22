"""Cine Agent JEV (System One) Integration Adapter.

Provides fast, typed semantic evaluations and guardrails for the Cine pipeline:
- Script readiness & narrative criteria (Noul / Score)
- Shot framing & camera movement classification (Choice)
- Video generation (Seedance) prompt pre-flight checks (Noul)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    # Load from local dir, omnigent dir, and parent dir
    load_dotenv()
    load_dotenv(Path(__file__).parent.parent.parent / ".env")
    load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")
except ImportError:
    pass

from typesafe_sdk import Choice, Noul, Score, TypeSafeClient


class CineJevAdapter:
    """Adapter wrapping TypeSafe JEV System One model for Cine Agent."""

    def __init__(self, api_key: str | None = None, model: str = "jev-latest"):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY")
        self.model = model

    def _get_client(self) -> TypeSafeClient:
        if not self.api_key:
            raise ValueError(
                "TYPESAFE_API_KEY is not set. Please set the environment variable "
                "or pass api_key to CineJevAdapter."
            )
        return TypeSafeClient(api_key=self.api_key)

    def evaluate_script(self, title: str, script_body: str) -> dict[str, Any]:
        """Evaluate a short film script before storyboarding.

        Checks opening hook, character motivation, payoff, and runtime fit.
        All checks evaluate in parallel in ~150ms.
        """
        state = {
            "title": title,
            "script": script_body,
        }

        questions = {
            "hook_quality": Score(
                instructions="评估开场前三秒的吸引力与情境悬念设置",
                criteria=[
                    "平淡无奇，缺乏开场钩子",
                    "有基本情境，能引起初步注意",
                    "悬念充分，冲突强烈，极具吸引力",
                ],
            ),
            "character_motivation": Noul(
                instructions="人物的行为选择是否源于清晰可见的目标与阻碍，且无需外部未建立的事实设定？"
            ),
            "ending_payoff": Noul(
                instructions="结尾是否通过动作或台词自然收拢前文铺垫，而不是生硬说教或突兀反转？"
            ),
            "runtime_plausibility": Noul(
                instructions="台词与动作密度是否适合短片时长（30~40秒），留有充分的反应和节奏停顿空间？"
            ),
        }

        with self._get_client() as client:
            response = client.system_one(state=state, questions=questions, model=self.model)

        return {
            "hook_quality": {
                "score": response.scores["hook_quality"].score,
                "confidence": response.scores["hook_quality"].confidence,
            },
            "character_motivation": {
                "probability": response.nouls["character_motivation"].noul,
            },
            "ending_payoff": {
                "probability": response.nouls["ending_payoff"].noul,
            },
            "runtime_plausibility": {
                "probability": response.nouls["runtime_plausibility"].noul,
            },
            "is_ready_for_storyboard": (
                response.nouls["character_motivation"].noul > 0.7
                and response.nouls["ending_payoff"].noul > 0.7
                and response.nouls["runtime_plausibility"].noul > 0.6
            ),
        }

    def classify_and_score_shot(self, shot_description: str) -> dict[str, Any]:
        """Classify framing, camera motion, and visual dynamism for a candidate shot."""
        state = {"shot_description": shot_description}

        questions = {
            "framing": Choice(
                instructions="根据镜头描述选择最符合的景别",
                criteria={
                    "extreme_close_up": "极特写，聚焦眼睛、手部微动作或关键道具微距",
                    "close_up": "特写，人物头部或面部表情为主",
                    "medium_shot": "中景，半身或双人肢体互动",
                    "wide_shot": "全景/远景，展示主体在环境中的空间关系",
                    "establishing": "大远景/空镜头，纯环境氛围渲染",
                },
            ),
            "camera_motion": Choice(
                instructions="根据描述判断主导的运镜方式",
                criteria={
                    "static": "固定机位，画面平稳无位移",
                    "pan_tilt": "原地摇移镜头（水平摇或垂直摇）",
                    "push_pull": "推镜头（向前贴近）或拉镜头（向后拉开）",
                    "tracking": "跟拍/移镜头，随主体移动",
                },
            ),
            "visual_dynamism": Score(
                instructions="评估该镜头的视觉张力与电影感",
                criteria=[
                    "构图平庸，缺乏视觉焦点",
                    "常规叙事镜头，合格可用",
                    "视觉张力强，光影层次丰富，电影感突出",
                ],
            ),
            "is_production_ready": Noul(
                instructions="该镜头描述是否清晰具体、具备直接送入渲染或实拍的可执行性？"
            ),
        }

        with self._get_client() as client:
            response = client.system_one(state=state, questions=questions, model=self.model)

        return {
            "framing": response.choices["framing"].choice,
            "framing_confidence": response.choices["framing"].confidence,
            "camera_motion": response.choices["camera_motion"].choice,
            "visual_dynamism": response.scores["visual_dynamism"].score,
            "is_production_ready": response.nouls["is_production_ready"].noul,
        }

    def preflight_seedance_prompt(self, prompt: str) -> dict[str, Any]:
        """Pre-flight check before submitting prompt to Seedance AI video generation."""
        state = {"prompt": prompt}

        questions = {
            "motion_conflict": Noul(
                instructions="提示词中是否存在相互矛盾的运镜或动作描述（如同时要求静止全景又要求急速推近）？"
            ),
            "feasible_in_15s": Noul(
                instructions="提示词描述的情境与连续动作幅度，能否在15秒生成单元内自然平滑地完成？"
            ),
            "is_compliant": Noul(
                instructions="内容是否健康合规，不包含暴恐、血腥或侵权违规要素？"
            ),
        }

        with self._get_client() as client:
            response = client.system_one(state=state, questions=questions, model=self.model)

        conflict_prob = response.nouls["motion_conflict"].noul
        feasible_prob = response.nouls["feasible_in_15s"].noul
        compliant_prob = response.nouls["is_compliant"].noul

        passed = (conflict_prob < 0.3) and (feasible_prob > 0.7) and (compliant_prob > 0.9)

        return {
            "passed": passed,
            "motion_conflict_probability": conflict_prob,
            "feasible_in_15s_probability": feasible_prob,
            "compliant_probability": compliant_prob,
        }
