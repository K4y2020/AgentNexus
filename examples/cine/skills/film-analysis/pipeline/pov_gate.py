"""Review every shot's prompt for a kept camera-holder POV, including an unseen holder.

Only the prompt's subject bindings, shared opening and this shot are evidence. The
outline's intention and the cut's cast list cannot establish what the camera shows.
This checks text, not the resulting video; uncertain or unreadable shots stay visible.
"""

from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

try:
    from .shot_state_gate import _subjects, _valid
except ImportError:  # Direct CLI execution from the pipeline directory.
    from shot_state_gate import _subjects, _valid

FACE_SHOWN = 0.5  # P(face shown) that blocks
OUTSIDE_VIEW = 0.75  # A clear third-person viewpoint blocks even without a face.
REVIEW = 0.3  # P(face shown) that is a review item
WORKERS = 8

QUESTION = (
    "The intended camera holder is `character` (`character_id`). Classify the actual viewpoint "
    "specified by `shot_text`, using only `subject_definitions` to resolve labels and "
    "`shared_context` for instructions applying to every shot in this segment. The intention "
    "to keep POV is not evidence that the text does so. Judge this shot, not other shots. "
    "A prohibition such as 'never show his face' is not an instruction to show it, but hiding "
    "a face alone does not establish first-person POV. Conversely, do not let a negative "
    "instruction cancel an actual conflicting depiction. Spoken words inside <d> tags and "
    "a reference portrait do not themselves describe what appears in the shot. Other people's "
    "faces can be visible in a valid holder POV; distinguish them using the bindings."
)
CRITERIA = {
    "own_eyes": (
        "the text establishes that the camera looks out through the holder's own eyes; the "
        "holder can be entirely unseen, or only their own hands, arms or legs enter the frame"
    ),
    "outside_view": (
        "the text specifies a camera apart from the holder: observing them from behind or in "
        "silhouette, using somebody else's eyes, or an explicitly detached external viewpoint; "
        "merely showing another person's face does not establish an outside view"
    ),
    "face_shown": (
        "the holder's own face is actually depicted, directly or in a reflection, rather than "
        "only mentioned in a prohibition, reference description or spoken dialogue"
    ),
    "unclear": (
        "the text does not establish whose viewpoint the camera takes, or gives conflicting "
        "viewpoint instructions without a clear actual depiction; no face alone is insufficient"
    ),
}
_SECTIONS = re.compile(
    r"^[ \t]*(subject_definitions|summary|retention_analysis|detailed_description|"
    r"integrated_multimodal_description|overall_soundscape|non_diegetic_music|"
    r"整体视听描述|整体音景|非叙事配乐)\s*[:：][ \t]*",
    re.M,
)
_SHOT = re.compile(r"\[(?:Shot|镜头) (\d+)\]")


def _shot_inputs(storyboard: dict, holder: str, holder_name: str | None):
    shots, errors = [], []
    total = 0
    for episode in storyboard.get("episodes") or []:
        for segment in episode.get("segments") or []:
            prompt = str(segment.get("h3Prompt") or "")
            headings = list(_SECTIONS.finditer(prompt))
            sections = {
                match[1]: prompt[
                    match.end() : headings[i + 1].start() if i + 1 < len(headings) else None
                ].strip()
                for i, match in enumerate(headings)
            }
            definitions = sections.get("subject_definitions", "")
            body = next(
                (
                    sections[key]
                    for key in (
                        "detailed_description",
                        "integrated_multimodal_description",
                        "整体视听描述",
                    )
                    if key in sections
                ),
                "",
            )
            markers = list(_SHOT.finditer(body))
            shared = body[: markers[0].start()].strip() if markers else ""
            bindings = _subjects(re.sub(r"(?m)^[ \t]+", "", definitions))
            subject = bindings.get(holder, holder_name or holder)
            for index, _cut in enumerate(segment.get("cuts") or [], 1):
                total += 1
                location = f"{segment.get('id', '?')} [Shot {index}]"
                found = [i for i, marker in enumerate(markers) if int(marker[1]) == index]
                if len(found) != 1:
                    errors.append(f"{location}: missing or repeated shot prompt")
                    continue
                i = found[0]
                end = markers[i + 1].start() if i + 1 < len(markers) else len(body)
                if not body[markers[i].end() : end].strip():
                    errors.append(f"{location}: empty shot prompt")
                    continue
                shots.append(
                    {
                        "location": location,
                        "state": {
                            "character_id": holder,
                            "character": subject,
                            "subject_definitions": definitions,
                            "shared_context": shared,
                            "shot_text": body[markers[i].start() : end].strip(),
                        },
                    }
                )
    return shots, errors, total


def check_pov(
    storyboard: dict,
    holder: str,
    *,
    holder_name: str | None = None,
    api_key: str | None = None,
    model: str = "jev-latest",
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    """``status`` passed/failed/unavailable, with ``blocking`` errors and ``review`` items."""
    shots, errors, total = _shot_inputs(storyboard, holder, holder_name)
    result: dict[str, Any] = {
        "status": "passed",
        "checked": 0,
        "total": total,
        "blocking": [],
        "review": [],
    }
    if not shots:
        return {
            **result,
            "status": "unavailable",
            "error": errors[0]
            if errors
            else "No shot prompts are available to check the kept POV",
        }
    try:
        from .jev_client import _choice, _find_api_key, open_client
    except ImportError:  # Direct CLI execution from the pipeline directory.
        from jev_client import _choice, _find_api_key, open_client

    key = (api_key or _find_api_key()).strip()

    def ask(shot: dict) -> dict[str, float] | None:
        state = shot["state"]
        token = hashlib.sha256(
            json.dumps(
                [model, state, QUESTION, CRITERIA], sort_keys=True, ensure_ascii=False
            ).encode()
        ).hexdigest()
        target = Path(cache_dir) / f"{token}.json" if cache_dir else None
        if target and target.is_file():
            try:
                saved = json.loads(target.read_text(encoding="utf-8"))
                if saved.get("input_sha256") == token and _valid(
                    saved.get("answer"), set(CRITERIA)
                ):
                    return saved["answer"]
            except (OSError, ValueError, AttributeError):
                pass
        if not key:
            errors.append(f"{shot['location']}: TYPESAFE_API_KEY is not configured")
            return None

        try:
            from typesafe_sdk import Choice

            with open_client(key) as client:
                response = client.system_one(
                    state=state,
                    questions={"q": Choice(instructions=QUESTION, criteria=CRITERIA)},
                    model=model,
                )
            _c, _p, probabilities, problem = _choice(
                (getattr(response, "answers", None) or {}).get("q"), set(CRITERIA)
            )
            if problem or not _valid(probabilities, set(CRITERIA)):
                raise ValueError(problem or "malformed_answer")
        except Exception as exc:  # noqa: BLE001 - reported; the cut stays unjudged
            errors.append(f"{shot['location']}: {type(exc).__name__}: {exc}")
            return None
        if target:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    json.dumps(
                        {"schema_version": 1, "input_sha256": token, "answer": probabilities},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            except OSError:
                pass
        return probabilities

    with ThreadPoolExecutor(WORKERS) as pool:
        answers = list(pool.map(ask, shots))
    name = f"{holder_name}（{holder}）" if holder_name else holder
    for shot, probabilities in zip(shots, answers, strict=True):
        if probabilities is None:
            continue
        result["checked"] += 1
        face = probabilities.get("face_shown", 0.0)
        outside = probabilities.get("outside_view", 0.0)
        message = (
            f"{shot['location']}：保留主视角时 {name} 是镜头本身，这一镜提示词有露脸风险"
            f"（概率 {face:.2f}）。从他的眼睛向外拍，只让他的手/手臂等进入画面；"
            "不能改成从背后拍他来代替主视角。"
        )
        if face >= FACE_SHOWN:
            result["blocking"].append(message)
        elif outside >= OUTSIDE_VIEW:
            result["blocking"].append(
                f"{shot['location']}：提示词把机位放在 {name} 的视角之外"
                f"（概率 {outside:.2f}）；背影、剪影或第三人称机位即使不露脸，也没有保留主视角。"
                "写明镜头从他的眼睛向外看，并修正冲突的机位描述。"
            )
        elif face >= REVIEW:
            result["review"].append({"reason": "pov_face_risk", "message": message})
        elif (
            outside >= REVIEW
            or probabilities.get("unclear", 0.0) >= REVIEW
            or probabilities.get("own_eyes", 0.0) < 0.5
        ):
            result["review"].append(
                {
                    "reason": "pov_viewpoint_unclear",
                    "message": (
                        f"{shot['location']}：提示词还不能确认镜头就是 {name} 的眼睛；"
                        "只写不露脸不足以保留主视角，请核对本镜机位和角色绑定。"
                    ),
                }
            )
    if errors and not result["checked"]:
        return {**result, "status": "unavailable", "error": errors[0]}
    if errors:
        result["errors"] = errors[:3]
    if result["blocking"]:
        result["status"] = "failed"
    return result
