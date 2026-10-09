"""The source shots behind each outline scene, written into the script and storyboard seeds.

Writers read the seeded file first and rarely open the source material, so the shots
of a scene's source locations (what is seen, how it is shot, what is said) go there.
"""

from __future__ import annotations

import re

PERSON_ID = re.compile(r"(?<![A-Za-z0-9])P[1-9][0-9]*(?![A-Za-z0-9])")
SIZE_ZH = {
    "extreme-wide": "大远景",
    "wide": "全景",
    "medium": "中景",
    "close": "特写",
    "extreme-close": "大特写",
}
CAMERA_ZH = {
    "Static Shot": "固定",
    "Push In": "推",
    "Pull Out": "拉",
    "Zoom In": "变焦推",
    "Zoom Out": "变焦拉",
    "Pan Left": "左摇",
    "Pan Right": "右摇",
    "Truck Left": "左移",
    "Truck Right": "右移",
    "Tilt Up": "仰摇",
    "Tilt Down": "俯摇",
    "Pedestal Up": "升",
    "Pedestal Down": "降",
    "Arc Shot": "环绕",
    "Tracking Shot": "跟拍",
    "Shake Slightly": "轻微晃动",
    "Shake Strongly": "强烈晃动",
    "POV": "主观视角",
}
# A long film keeps its seed readable: evenly spaced shots stand for the rest.
MAX_SHOTS = 60


VIEWPOINT_ZH = {"pov": "主视角", "to_camera": "对镜头说话"}
VIEWPOINT_EN = {"pov": "first-person POV", "to_camera": "addresses the camera"}


def _body_shots(material: dict) -> list[dict]:
    notes = (material or {}).get("source_locations") or {}
    return sorted(
        (
            shot
            for shot in notes.get("shots") or []
            if isinstance(shot, dict)
            and not shot.get("teaser")
            and str(shot.get("what") or "").strip()
            and isinstance(shot.get("start"), int | float)
            and isinstance(shot.get("end"), int | float)
        ),
        key=lambda shot: shot["start"],
    )


def _people(material: dict) -> dict[str, str]:
    """Readable stand-ins for person ids: the burned-in name, else the first words of the look."""
    labels = {}
    for person in ((material or {}).get("source_locations") or {}).get("people") or []:
        if not isinstance(person, dict) or not isinstance(person.get("id"), str):
            continue
        name = person.get("name_caption")
        look = re.split(r"[，,；;。]", str(person.get("look") or ""), maxsplit=1)[0].strip()
        labels[person["id"]] = (
            name.strip() if isinstance(name, str) and name.strip() else look
        ) or "某人"
    return labels


def _what(shot: dict, labels: dict[str, str]) -> str:
    return PERSON_ID.sub(lambda m: labels.get(m.group(0), "某人"), str(shot["what"]).strip())


def scene_shots(material: dict, outline: dict) -> dict[str, list[dict]]:
    """Body shots filmed at each outline scene's source locations, in film order."""
    shots = _body_shots(material)
    found = {}
    for scene in (outline or {}).get("scenes") or []:
        places = {p for p in scene.get("from") or [] if isinstance(p, str)}
        chosen = [s for s in shots if s.get("place") in places]
        if len(chosen) > MAX_SHOTS:
            step = len(chosen) / MAX_SHOTS
            chosen = [chosen[int(i * step)] for i in range(MAX_SHOTS)]
        if chosen and isinstance(scene.get("id"), str):
            found[scene["id"]] = chosen
    return found


def lines_by_shot(rows, shots: list[dict]) -> dict[int, list[dict]]:
    """Each transcript row goes to the shot it overlaps most."""
    placed: dict[int, list[dict]] = {}
    for row in rows or []:
        if not isinstance(row, dict) or not str(row.get("text") or "").strip():
            continue
        start, end = row.get("start"), row.get("end")
        if not isinstance(start, int | float):
            continue
        end = end if isinstance(end, int | float) and end > start else start + 0.01
        best, overlap = None, 0.0
        for shot in shots:
            shared = min(end, shot["end"]) - max(start, shot["start"])
            if shared > overlap:
                best, overlap = shot, shared
        if best is not None:
            placed.setdefault(id(best), []).append(row)
    return placed


def _speaker(row: dict) -> str:
    if row.get("speaker") == "extra":
        name = "群演"
    else:
        name = row.get("speaker_name")
        if not isinstance(name, str) or not name.strip() or name == "unclear":
            return ""
    if row.get("delivery") == "voice_over":
        return f"{name}（心声）"
    performance = row.get("performance") if isinstance(row.get("performance"), dict) else {}
    return f"{name}（画外）" if performance.get("speaker") == "offscreen" else name


def script_line(shot: dict, said: list[dict], labels: dict[str, str] | None = None) -> str:
    """One source shot for the screenwriter: time, framing, action and who says what."""
    size = SIZE_ZH.get(shot.get("size"), "")
    camera = CAMERA_ZH.get(shot.get("camera"), shot.get("camera") or "")
    viewpoint = VIEWPOINT_ZH.get(shot.get("viewpoint"), "")
    framing = "·".join(part for part in (size, camera, viewpoint) if part)
    head = f"{shot['start']:.1f}s" + (f" {framing}" if framing else "")
    if shot.get("transition") == "fade":
        head += "·渐黑入"
    lines = [f"{_speaker(row)}「{row['text'].strip()}」" for row in said]
    return f"{head}：{_what(shot, labels or {})}" + (" ｜ " + "；".join(lines) if lines else "")


def storyboard_line(shot: dict, said: list[dict], labels: dict[str, str] | None = None) -> str:
    """One source shot in the storyboard's own vocabulary, with the lines it carries."""
    parts = [f"{shot['start']:.1f}–{shot['end']:.1f}s"]
    parts.extend(str(shot[key]) for key in ("size", "camera") if shot.get(key))
    if shot.get("viewpoint") in VIEWPOINT_EN:
        parts.append(VIEWPOINT_EN[shot["viewpoint"]])
    if shot.get("transition") == "fade":
        parts.append("fade in")
    lines = "".join(f"「{row['text'].strip()}」" for row in said)
    what = _what(shot, labels or {})
    return " · ".join(parts) + f"：{what}" + (f" ｜ {lines}" if lines else "")


def add_source_shots(stage: str, document: dict, material: dict, outline: dict, rows) -> int:
    """Attach each scene's source shots to a fresh seed; returns how many were added."""
    by_scene = scene_shots(material, outline)
    if not by_scene:
        return 0
    placed = lines_by_shot(rows, _body_shots(material))
    labels = _people(material)
    added = 0
    if stage == "script":
        scenes_of = {
            episode.get("ep"): episode.get("sceneIds") or []
            for episode in (outline or {}).get("episodes") or []
            if isinstance(episode, dict)
        }
        names = {s.get("id"): s.get("name") for s in (outline or {}).get("scenes") or []}
        for episode in document.get("episodes") or []:
            listing = {}
            for scene_id in scenes_of.get(episode.get("ep"), []):
                if scene_id in by_scene:
                    key = f"{scene_id} {names.get(scene_id) or ''}".strip()
                    listing[key] = [
                        script_line(shot, placed.get(id(shot), []), labels)
                        for shot in by_scene[scene_id]
                    ]
                    added += len(listing[key])
            if listing:
                episode["sourceShots"] = listing
    elif stage == "storyboard":
        for episode in document.get("episodes") or []:
            for scene in episode.get("seedScenes") or []:
                shots = by_scene.get(scene.get("sceneId"))
                if shots:
                    scene["sourceShots"] = [
                        storyboard_line(shot, placed.get(id(shot), []), labels) for shot in shots
                    ]
                    added += len(shots)
    return added
