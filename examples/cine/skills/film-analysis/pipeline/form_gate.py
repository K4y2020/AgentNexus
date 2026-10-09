"""Shooting forms: what the source uses and what the outline decides about each.

A form such as first-person POV shapes every shot, so an adaptation that drops it changes
the film's kind, not one shot. The story draft names the source's forms and the shot notes
record each shot's viewpoint; the outline must keep or drop every form with a reason, so a
form is never lost without a decision.

Standard library only.
"""

from __future__ import annotations

from typing import Any

FORMS = {
    "pov": "第一人称主视角：镜头就是片中某个人的眼睛，这个人不露脸",
    "direct_address": "对镜头说话：人物直接对着镜头/观众说",
    "mockumentary": "伪纪录片/采访体",
    "one_take": "全片一镜到底",
    "scene_one_take": "逐场连续镜头：每场各一个连续镜头，只在场与场之间切镜",
    "screen_life": "屏幕叙事：故事发生在手机或电脑屏幕上",
    "found_footage": "设备画面：监控、行车记录仪、手持 DV 拍到的",
    "split_screen": "分屏",
    "other": "其他贯穿全片的特殊拍摄形式",
}
DECISIONS = ("keep", "drop")
# Shot-note viewpoints that make a form when this share of the body shots shows them.
VIEWPOINT_FORMS = {"pov": "pov", "to_camera": "direct_address"}
VIEWPOINT_SHARE = 1 / 3


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _body_shots(material: dict) -> list[dict]:
    shots = ((material or {}).get("source_locations") or {}).get("shots") or []
    return [s for s in shots if isinstance(s, dict) and not s.get("teaser")]


def source_forms(material: dict | None) -> list[dict[str, str]]:
    """The forms the story draft names, plus viewpoints the shot notes show often."""
    story = ((material or {}).get("adaptation_story") or {}).get("forms") or []
    forms: list[dict[str, str]] = []
    for item in story:
        if isinstance(item, dict) and item.get("form") in FORMS and _text(item.get("how")):
            if all(f["form"] != item["form"] for f in forms):
                forms.append({"form": item["form"], "how": item["how"].strip()})
    shots = _body_shots(material or {})
    for viewpoint, form in VIEWPOINT_FORMS.items():
        count = sum(s.get("viewpoint") == viewpoint for s in shots)
        if (
            shots
            and count / len(shots) >= VIEWPOINT_SHARE
            and all(f["form"] != form for f in forms)
        ):
            forms.append(
                {"form": form, "how": f"拉片：{count}/{len(shots)} 个原片镜头是{FORMS[form]}"}
            )
    return forms


def seed_forms(material: dict | None) -> list[dict[str, str]]:
    """Undecided outline entries for every source form."""
    return [
        {**form, "decision": "", "why": "", **({"holder": ""} if form["form"] == "pov" else {})}
        for form in source_forms(material)
    ]


def outline_form_errors(outline: dict, material: dict | None) -> list[str]:
    """Every source form the outline does not keep or drop with a reason."""
    entries = (outline.get("adaptation") or {}).get("forms")
    entries = [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []
    characters = {c.get("id") for c in outline.get("characters") or [] if isinstance(c, dict)}
    errors = []
    scene_ids = {
        scene.get("id") for scene in outline.get("scenes") or [] if isinstance(scene, dict)
    }
    for entry in entries:
        if entry.get("form") != "scene_one_take" or entry.get("decision") != "keep":
            continue
        selected = entry.get("sceneIds")
        if "sceneIds" in entry and (
            not isinstance(selected, list)
            or not selected
            or any(not isinstance(scene, str) or scene not in scene_ids for scene in selected)
        ):
            errors.append(
                "逐场连续镜头：sceneIds 必须是非空的 outline.scenes ID 列表；"
                "省略表示逐场连续拍摄"
            )
    for form in source_forms(material):
        name = f"原片拍摄形式「{FORMS[form['form']]}」（{form['how']}）"
        entry = next((e for e in entries if e.get("form") == form["form"]), None)
        if entry is None:
            errors.append(f"{name}：adaptation.forms 缺这一项；写明 keep 或 drop 和理由")
            continue
        if entry.get("decision") not in DECISIONS:
            errors.append(f"{name}：decision 必须是 keep 或 drop")
        if not _text(entry.get("why")):
            errors.append(f"{name}：why 要写保留或放弃的理由")
        if form["form"] == "pov" and entry.get("decision") == "keep":
            if entry.get("holder") not in characters:
                errors.append(
                    f"{name}：保留主视角时 holder 要填镜头所代表的那个人"
                    "在 outline.characters 里的角色 id"
                )
    return errors


def kept_forms(outline: dict | None) -> list[dict]:
    entries = ((outline or {}).get("adaptation") or {}).get("forms")
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict) and e.get("decision") == "keep"]


def kept_pov_holder(outline: dict | None) -> str | None:
    """The character whose eyes the camera is, when the outline keeps a POV form."""
    for entry in kept_forms(outline):
        if entry.get("form") == "pov" and _text(entry.get("holder")):
            return entry["holder"].strip()
    return None
