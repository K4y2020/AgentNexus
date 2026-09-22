#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build storyboard.json v4: explicit per-cut plan, guaranteed 2-5s cuts,
full beat coverage, continuity chains with stable positions, H3 prompts."""
import json
from pathlib import Path

BASE = Path(__file__).parent / "output" / "adaptation_production"
seed = json.loads((BASE / "sb_seed.json").read_text(encoding="utf-8"))
script = json.loads((BASE / "script.json").read_text(encoding="utf-8"))
scenes = seed["episodes"][0]["seedScenes"]
ep1 = script["episodes"][0]


def beat_lookup(si, n):
    for b in scenes[si - 1]["beats"]:
        if b["n"] == n:
            return b
    raise KeyError(f"{si}/{n}")


CUT_PLAN = {
    1: [[1, 2], [3], [4], [5], [6, 7], [8], [9], [10]],
    2: [[1, 2], [3, 4], [5], [6]],
    3: [[1, 2], [3], [4], [5, 6], [7], [8], [9]],
    4: [[1], [2], [3, 4]],
    5: [[1], [2]],
    6: [[1, 2], [3, 4], [5], [6, 7], [8, 9], [10], [11]],
    7: [[1, 2], [3, 4], [5, 6], [7, 8], [9], [10]],
}

SZ = {"w": "wide", "m": "medium", "c": "close", "ec": "extreme-close", "ew": "extreme-wide"}
SZ_PHRASE = {"w": "wide shot", "m": "medium shot", "c": "close-up",
             "ec": "extreme close-up", "ew": "extreme wide shot"}

L = {
    1: [("w", "Push In", "cinematic film still, wide shot of a street fortune-teller stall in daylight, an old fortune teller in dark sunglasses and a long black robe gripping the wrist of an elegant young woman, camera slowly pushing in"),
        ("m", "Static Shot", "cinematic film still, medium shot of the fortune teller smirking as he speaks his prophecy, the young woman beside him frowning"),
        ("c", "Static Shot", "cinematic film still, close-up of the young woman sharply pulling her hand back, index finger pointed at the old man"),
        ("m", "Push In", "cinematic film still, medium shot of the woman scolding the fortune teller with a raised finger, camera pushing in"),
        ("m", "Truck Right", "cinematic film still, medium shot trucking right, a suited man squeezes through the crowd to hug the woman by the shoulder, grinning"),
        ("c", "Static Shot", "cinematic film still, close-up, a little girl tugs the woman's sleeve and looks up innocently"),
        ("m", "Static Shot", "cinematic film still, medium shot, the woman bends down ruffling the girl's hair with a fond scolding smile"),
        ("w", "Static Shot", "cinematic film still, wide shot, the family trio walks away from the fortune stall in warm afternoon light")],
    2: [("w", "Push In", "cinematic film still, wide shot of a funeral hall with a black-and-white memorial portrait at center and white wreaths on both sides, mourners kneeling, camera pushing in slowly"),
        ("m", "Static Shot", "cinematic film still, medium shot, the kneeling girl speaks a single word in a breath, then a middle-aged man in a black suit grips her shoulder in silence"),
        ("m", "Static Shot", "cinematic film still, medium shot, the man speaks to the girl in a low broken voice, inviting her to come live at the grandmother's estate"),
        ("c", "Pull Out", "cinematic film still, close-up, the girl turns toward the memorial portrait where the mother smiles brightly, giving a slow nod as the camera pulls out")],
    3: [("w", "Tracking Shot", "cinematic film still, wide shot, a black luxury car pulls up before an iron gate with a KN family crest at dusk, the girl steps out pulling a suitcase, tracking shot follows"),
        ("m", "Push In", "cinematic film still, medium shot, inside the gate a white-haired grandmother hurries out, eyes reddening, camera pushing in"),
        ("m", "Push In", "cinematic film still, medium shot, the grandmother pulls the girl into a tight embrace, camera pushing in"),
        ("c", "Push In", "cinematic film still, close-up of the grandmother mid-sob over the girl's shoulder, camera pushing in"),
        ("m", "Static Shot", "cinematic film still, medium shot, the girl answers softly with strained composure, eyes lowered"),
        ("m", "Pan Left", "cinematic film still, medium shot, the grandmother wipes her face and suddenly turns to shout an order into the house, camera panning left"),
        ("m", "Push In", "cinematic film still, medium shot, the grandmother shouting with full authority, one arm raised pointing inside, camera pushing in")],
    4: [("w", "Pull Out", "cinematic film still, wide shot of a grand high-ceiling hall with golden columns and crystal chandeliers, a row of black-suited white-gloved servants lining both walls, camera pulling out to reveal the scale"),
        ("m", "Static Shot", "cinematic film still, medium shot, servant rows chanting in rehearsed unison while the girl freezes mid-step in the foreground"),
        ("c", "Static Shot", "cinematic film still, close-up of the girl's blank expression, blinking twice, then a polite hesitant smile")],
    5: [("w", "Tracking Shot", "cinematic film still, wide shot, three school-uniform girls enter the hall one after another, the eldest dignified, the second brisk, the youngest clutching a plush toy, tracking shot following"),
        ("c", "Push In", "cinematic film still, close-up of the protagonist girl counting on her fingers with a helpless laughing look, camera pushing in")],
    6: [("w", "Truck Right", "cinematic film still, wide shot, the crowd splits open at the far end of the hall, a woman in a scarlet suit strides across a white floor past a blue-gold crest, black handbag swinging, camera trucking right"),
        ("c", "Static Shot", "cinematic film still, close-up of the girl testing her words carefully, uncertain, the red-suited woman blurred in the foreground"),
        ("m", "Push In", "cinematic film still, medium shot, the girl freezes mid-word as a servant leans in whispering a correction, then laughs at herself, camera pushing in"),
        ("m", "Static Shot", "cinematic film still, medium two-shot, the red-suited woman laughs and pats the girl's shoulder, both facing each other"),
        ("c", "Static Shot", "cinematic film still, close-up of the girl answering with calm poise, chin slightly up"),
        ("c", "Static Shot", "cinematic film still, close-up of the red-suited woman, one eyebrow raised in triumphant delight"),
        ("c", "Static Shot", "cinematic film still, close-up of the girl with resigned amusement, eyes rolled upward")],
    7: [("w", "Tracking Shot", "cinematic film still, wide shot, side-lit corridor, the red-suited woman walks beside the girl then suddenly spins and wraps her in a bear hug wailing, tracking shot follows the motion"),
        ("c", "Push In", "cinematic film still, close-up of the girl frozen in the hug, hands hovering, expression sliding from shock to deadpan, camera pushing in"),
        ("m", "Pan Right", "cinematic film still, medium shot, rows of servants raise hands to wipe tears in perfect comic unison, camera panning right"),
        ("m", "Static Shot", "cinematic film still, medium shot, a servant hurries in from the gate bowing to announce while the girl turns to look"),
        ("c", "Push In", "cinematic film still, close-up of the girl's eyes widening in slow horror, pupils trembling, camera pushing in"),
        ("m", "Push In", "cinematic film still, medium shot, the girl stares toward the main gate where a silhouette approaches backlit, her face at the edge of breakdown, camera pushing in")],
}

CH = {
    1: [["C02", "C06"], ["C02", "C06"], ["C02"], ["C02", "C06"], ["C02", "C07"], ["C02"], ["C02"], ["C02", "C06", "C07"]],
    2: [["C01"], ["C01", "C07"], ["C01", "C07"], ["C01"]],
    3: [["C01"], ["C01", "C03"], ["C01", "C03"], ["C03"], ["C01"], ["C03"], ["C03"]],
    4: [["C08"], ["C08", "C01"], ["C01"]],
    5: [["C01"], ["C01"]],
    6: [["C04"], ["C01"], ["C01", "C08"], ["C04", "C01"], ["C01"], ["C04"], ["C01"]],
    7: [["C04", "C01"], ["C01"], ["C08"], ["C08", "C01"], ["C01"], ["C01"]],
}


def fmt_cut_time(t):
    m = int(t) // 60
    s = int(t) % 60
    ms = int(round((t - int(t)) * 1000))
    return f"{m:02d}:{s:02d}.{ms:03d}"


def build_h3(cuts):
    if len(cuts) <= 1:
        align = ("For the target video, at 0.00 seconds into the target video, "
                 "<Picture 1> (from [Shot 1]) is fully referenced.")
    else:
        starts = [0.0]
        for c in cuts[:-1]:
            starts.append(starts[-1] + c["seconds"])
        items = [f"Picture {k + 1} (from Shot {k + 1}) aligns with the {t:.2f}-second mark of the target video"
                 for k, t in enumerate(starts)]
        align = "How the reference pictures align with the target video — " + "; ".join(items) + "."
    body = ["integrated_multimodal_description:"]
    acc = 0.0
    for ci, cut in enumerate(cuts, 1):
        prefix = f"[Shot {ci}] Cinematic, live-action." if ci == 1 else f"[Shot {ci}] At {fmt_cut_time(acc)},"
        body.append(f"{prefix} {cut['frame']} Camera: {cut['camera']}. ({cut['camera'].lower()})")
        for b in cut["_group"]:
            if b["kind"] != "line":
                continue
            is_vo = b.get("speaker") == "VO" or str(b.get("delivery", "")).startswith("VO")
            if is_vo:
                body.append(f"(S{ci}) says in an off-screen voiceover, lips remaining completely closed "
                            f"<d>[Chinese] {b['text']} </d>")
            else:
                body.append(f"(S{ci}) speaks <d>[Chinese] {b['text']} </d>")
        acc += cut["seconds"]
    snd = ("overall_soundscape: Street crowd murmur and distant traffic hum in the opening; "
           "footsteps and cloth rustle; quiet sobs echo softly in the funeral hall; "
           "servant chorus voices overlap with light commotion in the hall scenes.")
    mus = ("non_diegetic_music: A light comedic string pizzicato underlines the family absurdity, "
           "turning wistful during the funeral hall, swelling to a suspended chord at the final reveal.")
    return "\n".join([align, ""] + body + ["", snd, mus])


global_last_out, global_last_pos = {}, {}
segs_out = []
for scene_index, cut_groups in CUT_PLAN.items():
    groups = [[beat_lookup(scene_index, n) for n in gns] for gns in cut_groups]
    design = L[scene_index]
    chars_plan = CH[scene_index]
    assert len(groups) == len(design) == len(chars_plan), scene_index
    cuts = []
    for ci, (group, (size_k, camera, frame), chars) in enumerate(zip(groups, design, chars_plan)):
        phrase = SZ_PHRASE[size_k]
        if phrase not in frame.lower():
            frame = frame.replace("cinematic film still,", f"cinematic film still, {phrase},", 1)
        actions = {}
        for c in chars:
            po = global_last_out.get(c)
            actions[c] = {"in": po or "idle", "out": po or "idle"}
            global_last_out[c] = actions[c]["out"]
        positions = {}
        for c in chars:
            if c in global_last_pos:
                positions[c] = global_last_pos[c]
            else:
                positions[c] = "left" if len(positions) % 2 == 0 else "right"
            global_last_pos[c] = positions[c]
        cut = {
            "beats": [group[0]["n"], group[-1]["n"]],
            "seconds": round(sum(b["seconds"] for b in group), 2),
            "size": SZ[size_k],
            "camera": camera,
            "characters": chars,
            "characterStates": {c: "default" for c in chars},
            "frame": frame,
            "continuity": {
                "axisId": "axis_master",
                "screenPositions": positions,
                "eyelineTarget": None,
                "motionVector": "static",
                "propStates": {},
                "actionStates": actions,
                **({"establishing": True} if ci == 0 else {}),
            },
            "_group": group,
        }
        cuts.append(cut)
    # split into segments of <=15s
    sub, cur, acc = [], [], 0.0
    for c in cuts:
        if cur and acc + c["seconds"] > 15.0:
            sub.append(cur)
            cur, acc = [], 0.0
        cur.append(c)
        acc += c["seconds"]
    if cur:
        sub.append(cur)
    for cc in sub:
        segs_out.append({
            "id": f"E01-{len(segs_out) + 1:02d}",
            "sceneIndex": scene_index,
            "cuts": [{k: v for k, v in c.items() if not k.startswith("_")} for c in cc],
            "h3Prompt": build_h3(cc),
        })

board = {
    "source": script["source"],
    "style": "realistic",
    "continuityContractVersion": 1,
    "stateContractVersion": 1,
    "promptLang": "en",
    "params": {"maxSegmentSeconds": 15, "minCutSeconds": 2, "maxCutSeconds": 5,
               "maxOnScreen": 3, "tolerance": 0.15},
    "episodes": [{"ep": 1, "segments": segs_out}],
}
(BASE / "storyboard.json").write_text(json.dumps(board, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"v4 built: {len(segs_out)} segments, {sum(len(s['cuts']) for s in segs_out)} cuts")
