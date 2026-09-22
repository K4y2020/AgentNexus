#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebuild storyboard.json v2: per-cut continuity chains, <d> dialogue blocks,
exact alignment lines, and H3 prompts that satisfy the cine-storyboard gates."""
import json
from pathlib import Path

BASE = Path(__file__).parent / "output" / "adaptation_production"
seed = json.loads((BASE / "sb_seed.json").read_text(encoding="utf-8"))
script = json.loads((BASE / "script.json").read_text(encoding="utf-8"))
scenes = seed["episodes"][0]["seedScenes"]

PARTITION = [
    (1, [1, 2, 3, 4, 5]), (1, [6, 7, 8, 9, 10]),
    (2, [1, 2, 3, 4]), (2, [5, 6]),
    (3, [1, 2, 3, 4]), (3, [5, 6, 7, 8, 9]),
    (4, [1, 2, 3, 4]),
    (5, [1, 2]),
    (6, [1, 2, 3, 4, 5]), (6, [6, 7, 8, 9, 10, 11]),
    (7, [1, 2, 3, 4, 5, 6]), (7, [7, 8, 9, 10]),
]

SIZE_PHRASE = {
    "extreme-wide": "extreme wide shot", "wide": "wide shot", "medium": "medium shot",
    "close": "close-up", "extreme-close": "extreme close-up",
}

DESIGN = {
    1: [("wide", "Push In", ["C02", "C06"],
         "cinematic film still, wide shot of a street fortune-teller stall in daylight, an old fortune teller in dark sunglasses and a long black robe gripping the wrist of an elegant young woman, camera slowly pushing in"),
        ("medium", "Static Shot", ["C02", "C06"],
         "cinematic film still, medium shot of the fortune teller smirking while the young woman frowns beside him"),
        ("close", "Static Shot", ["C02"],
         "cinematic film still, close-up of the young woman sharply pulling her hand back, index finger pointed at the old man"),
        ("medium", "Push In", ["C02", "C06"],
         "cinematic film still, medium shot, the woman scolds the fortune teller with a raised finger, camera pushing in")],
    2: [("medium", "Truck Right", ["C02", "C07"],
         "cinematic film still, medium shot trucking right, a suited man squeezes through the crowd to hug the woman by the shoulder, grinning"),
        ("close", "Static Shot", ["C02"],
         "cinematic film still, close-up, a little girl tugs the woman's sleeve and looks up innocently"),
        ("medium", "Static Shot", ["C02"],
         "cinematic film still, medium shot, the woman bends down ruffling the girl's hair with a fond scolding smile"),
        ("wide", "Static Shot", ["C02", "C06", "C07"],
         "cinematic film still, wide shot, the family trio walks away from the fortune stall in warm afternoon light")],
    3: [("wide", "Push In", ["C01"],
         "cinematic film still, wide shot of a funeral hall with a black-and-white memorial portrait at center and white wreaths on both sides, mourners kneeling, camera pushing in slowly"),
        ("medium", "Push In", ["C01"],
         "cinematic film still, medium shot, a white-dressed teenage girl kneels at the front with trembling shoulders, camera pushing in"),
        ("close", "Static Shot", ["C01"],
         "cinematic film still, close-up of the girl's tearful face, lips pressed hard"),
        ("medium", "Static Shot", ["C01", "C07"],
         "cinematic film still, medium shot, a middle-aged man in a black suit grips the girl's shoulder, silent")],
    4: [("medium", "Push In", ["C01", "C07"],
         "cinematic film still, medium over-the-shoulder shot, the man speaks to the girl in a low broken voice, camera pushing in gently"),
        ("close", "Pull Out", ["C01"],
         "cinematic film still, close-up, the girl turns toward the memorial portrait where the mother smiles brightly, giving a slow nod as the camera pulls out")],
    5: [("wide", "Tracking Shot", ["C01"],
         "cinematic film still, wide shot, a black luxury car pulls up before an iron gate with a KN family crest at dusk, the girl steps out pulling a suitcase, tracking shot follows"),
        ("medium", "Push In", ["C01", "C03"],
         "cinematic film still, medium shot, inside the gate a white-haired grandmother hurries out, eyes reddening, camera pushing in"),
        ("medium", "Static Shot", ["C01", "C03"],
         "cinematic film still, medium shot, the grandmother pulls the girl into a tight embrace, shaking with sobs"),
        ("close", "Push In", ["C03"],
         "cinematic film still, close-up of the grandmother mid-sob, tears streaming, the girl stunned over her shoulder, camera pushing in")],
    6: [("medium", "Static Shot", ["C01", "C03"],
         "cinematic film still, medium shot, the girl held in the embrace with hands hovering unsure in mid-air"),
        ("close", "Static Shot", ["C01"],
         "cinematic film still, close-up, the girl answers softly with strained composure, eyes lowered"),
        ("medium", "Pan Left", ["C03"],
         "cinematic film still, medium shot, the grandmother wipes her face and suddenly turns to shout an order into the house, camera panning left"),
        ("medium", "Push In", ["C03"],
         "cinematic film still, medium shot, the grandmother shouting with full authority, one arm raised pointing inside, camera pushing in")],
    7: [("wide", "Pull Out", ["C08"],
         "cinematic film still, wide shot of a grand high-ceiling hall with golden columns and crystal chandeliers, a row of black-suited white-gloved servants lining both walls, camera pulling out to reveal the scale"),
        ("medium", "Static Shot", ["C08", "C01"],
         "cinematic film still, medium shot, servant rows chanting in rehearsed unison while the girl freezes mid-step in the foreground"),
        ("close", "Static Shot", ["C01"],
         "cinematic film still, close-up of the girl's blank expression, blinking twice, then a polite hesitant smile")],
    8: [("wide", "Tracking Shot", ["C01"],
         "cinematic film still, wide shot, three school-uniform girls enter the hall one after another, the eldest dignified, the second brisk, the youngest clutching a plush toy, tracking shot following"),
        ("close", "Push In", ["C01"],
         "cinematic film still, close-up of the protagonist girl counting on her fingers with a helpless laughing look, camera pushing in")],
    9: [("wide", "Truck Right", ["C04"],
         "cinematic film still, wide shot, the crowd splits open at the far end of the hall, a woman in a scarlet suit strides across a white floor past a blue-gold crest, black handbag swinging, camera trucking right"),
        ("medium", "Push In", ["C04", "C01"],
         "cinematic film still, medium shot, the red-suited woman stops before the girl, looking her up and down with a raised brow, camera pushing in"),
        ("close", "Static Shot", ["C01"],
         "cinematic film still, close-up of the girl testing her words carefully, uncertain"),
        ("medium", "Static Shot", ["C01", "C08"],
         "cinematic film still, medium two-shot, a servant leans in whispering a correction, the girl freezes mid-word then laughs at herself"),
        ("close", "Static Shot", ["C01"],
         "cinematic film still, close-up, the girl finishes the correction joke with growing embarrassment, laughing first")],
    10: [("medium", "Static Shot", ["C04", "C01"],
          "cinematic film still, medium two-shot, the red-suited woman laughs and pats the girl's shoulder, both facing each other"),
         ("close", "Push In", ["C01"],
          "cinematic film still, close-up of the girl answering with calm poise, chin slightly up, camera pushing in"),
         ("close", "Static Shot", ["C04"],
          "cinematic film still, close-up of the red-suited woman, one eyebrow raised in triumphant delight"),
         ("medium", "Static Shot", ["C08", "C01", "C04"],
          "cinematic film still, medium shot, servant rows chanting their chorus in unison behind the two talking girls, crowd kept in a far row with a note on split planes"),
         ("close", "Static Shot", ["C01"],
          "cinematic film still, close-up of the girl with resigned amusement, eyes rolled upward")],
    11: [("wide", "Tracking Shot", ["C04", "C01"],
          "cinematic film still, wide shot, side-lit corridor, the red-suited woman walks beside the girl then suddenly spins and wraps her in a bear hug wailing, tracking shot follows the motion"),
         ("close", "Push In", ["C01"],
          "cinematic film still, close-up of the girl frozen in the hug, hands hovering, expression sliding from shock to deadpan, camera pushing in"),
         ("medium", "Pan Right", ["C08"],
          "cinematic film still, medium shot, rows of servants raise hands to wipe tears in perfect comic unison, camera panning right"),
         ("medium", "Static Shot", ["C08", "C01"],
          "cinematic film still, medium shot, a servant hurries in from the gate bowing to announce while the girl turns to look"),
         ("close", "Push In", ["C01"],
          "cinematic film still, close-up of the girl's eyes widening in slow horror, pupils trembling, camera pushing in")],
    12: [("medium", "Push In", ["C01"],
          "cinematic film still, medium shot, the girl stares toward the main gate where a silhouette approaches backlit, her face at the edge of breakdown, camera pushing in"),
         ("close", "Static Shot", ["C01"],
          "cinematic film still, close-up of the girl's expression frozen at the brink of despair, then cut to black")],
}


def beat_lookup(si, n):
    for b in scenes[si - 1]["beats"]:
        if b["n"] == n:
            return b
    raise KeyError(f"{si}/{n}")


def fmt_cut_time(t):
    m = int(t) // 60
    s = int(t) % 60
    ms = int(round((t - int(t)) * 1000))
    return f"{m:02d}:{s:02d}.{ms:03d}"


segments = []
for seg_i, (scene_index, ns) in enumerate(PARTITION, 1):
    beats = [beat_lookup(scene_index, n) for n in ns]
    design = DESIGN[seg_i]
    n_cuts, n_beats = len(design), len(beats)
    base_n, rem = n_beats // n_cuts, n_beats % n_cuts
    idx = 0
    built = []
    last_out = {}
    for ci, (size, camera, chars, frame) in enumerate(design):
        take = base_n + (1 if ci < rem else 0)
        group = beats[idx:idx + take]
        idx += take
        sec = round(sum(b["seconds"] for b in group), 2)
        phrase = SIZE_PHRASE[size]
        if phrase not in frame.lower():
            frame = frame.replace("cinematic film still,", f"cinematic film still, {phrase},", 1)
        # continuity chain: in inherits previous out; out stays (holding action)
        actions = {}
        for c in chars:
            prev_out = last_out.get(c)
            actions[c] = {"in": prev_out or "idle", "out": prev_out or "idle"}
            last_out[c] = actions[c]["out"]
        cont = {
            "axisId": f"axis_{seg_i:02d}",
            "screenPositions": {c: ("left" if i % 2 == 0 else "right") for i, c in enumerate(chars)},
            "eyelineTarget": None,
            "motionVector": "static",
            "propStates": {},
            "actionStates": actions,
        }
        if ci == 0:
            cont["establishing"] = True
        cut = {
            "beats": [group[0]["n"], group[-1]["n"]],
            "seconds": sec,
            "size": size,
            "camera": camera,
            "characters": chars,
            "characterStates": {c: "default" for c in chars},
            "frame": frame,
            "continuity": cont,
            "_group": group,
        }
        built.append(cut)
    segments.append({"id": f"E01-{seg_i:02d}", "sceneIndex": scene_index, "cuts": built})


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
    soundscape = ("overall_soundscape: Street crowd murmur and distant traffic hum in the opening; "
                  "footsteps and cloth rustle; quiet sobs echo softly in the funeral hall; "
                  "servant chorus voices overlap with light commotion in the hall scenes.")
    music = ("non_diegetic_music: A light comedic string pizzicato underlines the family absurdity, "
             "turning wistful during the funeral hall, swelling to a suspended chord at the final reveal.")
    return "\n".join([align, ""] + body + ["", soundscape, music])


segs_out = []
for seg in segments:
    segs_out.append({
        "id": seg["id"],
        "sceneIndex": seg["sceneIndex"],
        "cuts": [{k: v for k, v in c.items() if not k.startswith("_")} for c in seg["cuts"]],
        "h3Prompt": build_h3(seg["cuts"]),
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
print(f"storyboard.json rebuilt: {len(segs_out)} segments, "
      f"{sum(len(s['cuts']) for s in segs_out)} cuts")
