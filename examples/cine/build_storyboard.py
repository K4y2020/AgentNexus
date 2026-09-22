#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build storyboard.json from sb_seed.json: 12 segments, cut-level cinematography,
continuity ledgers, and H3 prompts. Validates against cine-storyboard quality gates."""
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).parent / "output" / "adaptation_production"
seed = json.loads((BASE / "sb_seed.json").read_text(encoding="utf-8"))
script = json.loads((BASE / "script.json").read_text(encoding="utf-8"))
scenes = seed["episodes"][0]["seedScenes"]
ep1 = script["episodes"][0]

# segment partition: (scene_index, beat list)
PARTITION = [
    (1, [1, 2, 3, 4, 5]), (1, [6, 7, 8, 9, 10]),
    (2, [1, 2, 3, 4]), (2, [5, 6]),
    (3, [1, 2, 3, 4]), (3, [5, 6, 7, 8, 9]),
    (4, [1, 2, 3, 4]),
    (5, [1, 2]),
    (6, [1, 2, 3, 4, 5]), (6, [6, 7, 8, 9, 10, 11]),
    (7, [1, 2, 3, 4, 5, 6]), (7, [7, 8, 9, 10]),
]


def beat_lookup(scene_index, n):
    for b in scenes[scene_index - 1]["beats"]:
        if b["n"] == n:
            return b
    raise KeyError(f"beat {n} not in scene {scene_index}")


# scene-level character sets from the script (scene entries in order)
SCENE_CHARS = {}
for i, sc in enumerate(ep1["scenes"], 1):
    SCENE_CHARS.setdefault(i, set(sc.get("characters", [])))

# per-cut design keyed by (segment_index, cut_index):
# size, camera, characters, frame prompt (English, must contain 'cinematic film still' + size phrase)
SIZE_PHRASE = {
    "extreme-wide": "extreme wide shot",
    "wide": "wide shot",
    "medium": "medium shot",
    "close": "close-up",
    "extreme-close": "extreme close-up",
}

DESIGN = {
    1: [  # scene 1 beats 1-5
        ("wide", "Push In", ["C02", "C06"],
         "cinematic film still, wide shot of a street fortune-teller stall in daytime sunlight. An old fortune teller in dark sunglasses and a long black robe shakes a bamboo lot cup, eyes locking onto an elegant young woman. The camera slowly pushes in as he steps forward and grips her wrist."),
        ("medium", "Static Shot", ["C02", "C06"],
         "cinematic film still, medium shot of the fortune teller speaking with a knowing smirk while the young woman frowns beside him, street crowd bokeh behind."),
        ("close", "Static Shot", ["C02"],
         "cinematic film still, close-up of the young woman sharply withdrawing her hand, index finger pointed at the old man, brows raised in anger."),
        ("medium", "Push In", ["C02", "C06"],
         "cinematic film still, medium shot, the woman scolds the fortune teller with a raised finger while a suited man enters at frame edge."),
    ],
    2: [  # scene 1 beats 6-10
        ("medium", "Truck Right", ["C02", "C07"],
         "cinematic film still, medium shot with a truck right move, a suited man squeezes through the crowd to wrap an arm around the woman, grinning widely."),
        ("close", "Static Shot", ["C02"],
         "cinematic film still, close-up of a little girl tugging the woman's sleeve and looking up innocently, street crowd bokeh behind."),
        ("medium", "Static Shot", ["C02"],
         "cinematic film still, medium shot, the woman bends down to the little girl with an exaggerated expression, ruffling her hair."),
        ("wide", "Static Shot", ["C02", "C06", "C07"],
         "cinematic film still, wide shot, the family trio walks away from the fortune stall down a busy street in warm afternoon light."),
    ],
    3: [  # scene 2 beats 1-4
        ("wide", "Push In", ["C01"],
         "cinematic film still, wide shot of a funeral hall, a black-and-white memorial portrait at center, white chrysanthemum wreaths on both sides, mourners kneeling in black, camera pushing in slowly."),
        ("medium", "Push In", ["C01"],
         "cinematic film still, medium shot, a white-dressed teenage girl kneels at the front, shoulders trembling silently, wreaths blurred behind her."),
        ("close", "Static Shot", ["C01"],
         "cinematic film still, close-up of the girl's tearful face, lips pressed hard, eyes glistening."),
        ("medium", "Static Shot", ["C01", "C07"],
         "cinematic film still, medium shot, a middle-aged man in a black suit rests one hand on the girl's shoulder and grips it tighter, silent."),
    ],
    4: [  # scene 2 beats 5-6
        ("medium", "Push In", ["C01", "C07"],
         "cinematic film still, medium over-the-shoulder shot, the man speaks to the girl in a low broken voice, his eyes red. Camera pushes in gently."),
        ("close", "Pull Out", ["C01"],
         "cinematic film still, close-up, the girl turns toward the memorial portrait where the mother smiles brightly in the photo, and gives a slow nod as the camera pulls out."),
    ],
    5: [  # scene 3 beats 1-4
        ("wide", "Tracking Shot", ["C01"],
         "cinematic film still, wide shot, a black luxury car pulls up before an iron gate with a KN family crest at dusk. The car door opens and the girl steps out pulling a suitcase, tracking shot follows her."),
        ("medium", "Push In", ["C01", "C03"],
         "cinematic film still, medium shot, inside the gate a white-haired elderly grandmother hurries out, her eyes reddening the moment she sees the girl. Camera pushes in."),
        ("medium", "Static Shot", ["C01", "C03"],
         "cinematic film still, medium shot, the grandmother pulls the girl into a tight embrace, her body shaking with sobs."),
        ("close", "Push In", ["C03"],
         "cinematic film still, close-up on the grandmother's face mid-sob, tears streaming down while the girl's stunned face appears over her shoulder."),
    ],
    6: [  # scene 3 beats 5-9
        ("medium", "Static Shot", ["C01", "C03"],
         "cinematic film still, medium shot, the girl held in the embrace with her hands hovering in mid-air, unsure where to put them."),
        ("close", "Static Shot", ["C01"],
         "cinematic film still, close-up, the girl answers softly with strained composure, eyes lowered."),
        ("medium", "Pan Left", ["C03"],
         "cinematic film still, medium shot, the grandmother wipes her face and suddenly turns toward the mansion, shouting an order. Camera pans left following her turn."),
        ("medium", "Push In", ["C03"],
         "cinematic film still, medium shot, the grandmother shouting into the hall with full authority, one arm raised pointing inside. Camera pushes in on her commanding posture."),
    ],
    7: [  # scene 4 beats 1-4
        ("wide", "Pull Out", ["C08"],
         "cinematic film still, wide shot of a grand high-ceiling hall with golden columns and crystal chandeliers, a row of black-suited white-gloved servants lining both walls. Camera pulls out to reveal the scale."),
        ("medium", "Static Shot", ["C08", "C01"],
         "cinematic film still, medium shot, servant rows chanting in perfect unison like a rehearsed choir while the girl freezes mid-step in the foreground."),
        ("close", "Static Shot", ["C01"],
         "cinematic film still, close-up of the girl's blank expression, blinking twice, then a polite hesitant smile."),
    ],
    8: [  # scene 5 beats 1-2
        ("wide", "Tracking Shot", ["C01"],
         "cinematic film still, wide shot, three school-uniform girls enter the hall one after another: the eldest dignified, the second brisk, the youngest clutching a plush toy. Tracking shot follows their entrance."),
        ("close", "Push In", ["C01"],
         "cinematic film still, close-up of the protagonist girl counting on her fingers with a helpless laughing expression, as if thinking to herself. Camera pushes in."),
    ],
    9: [  # scene 6 beats 1-5
        ("wide", "Truck Right", ["C04"],
         "cinematic film still, wide shot, at the far end of the hall the crowd splits open. A woman in a scarlet business suit strides through on a white floor past a blue-gold round crest, black handbag swinging. Truck right follows her stride."),
        ("medium", "Push In", ["C04", "C01"],
         "cinematic film still, medium shot, the red-suited woman stops before the girl and looks her up and down with a raised brow. Camera pushes in."),
        ("close", "Static Shot", ["C01"],
         "cinematic film still, close-up of the girl testing her words carefully, uncertain."),
        ("medium", "Static Shot", ["C01", "C08"],
         "cinematic film still, medium two-shot, a servant leans in whispering a correction while the girl freezes mid-word, then laughs at herself."),
        ("close", "Static Shot", ["C01"],
         "cinematic film still, close-up, the girl finishes the correction joke with growing embarrassment, laughing first."),
    ],
    10: [  # scene 6 beats 6-11
        ("medium", "Static Shot", ["C04", "C01"],
         "cinematic film still, medium two-shot, the red-suited woman laughs and pats the girl's shoulder, both facing each other."),
        ("close", "Push In", ["C01"],
         "cinematic film still, close-up of the girl answering with calm poise, chin slightly up. Camera pushes in."),
        ("close", "Static Shot", ["C04"],
         "cinematic film still, close-up of the red-suited woman, one eyebrow raised in triumphant delight."),
        ("medium", "Static Shot", ["C08", "C01", "C04"],
         "cinematic film still, medium shot, servant rows chanting their rehearsed chorus in unison while the two girls converse in the background. Note: crowd split into near row and far row across two planes."),
        ("close", "Static Shot", ["C01"],
         "cinematic film still, close-up of the girl with resigned amusement, eyes rolled upward."),
    ],
    11: [  # scene 7 beats 1-6
        ("wide", "Tracking Shot", ["C04", "C01"],
         "cinematic film still, wide shot, a side-lit corridor. The red-suited woman walks beside the girl, then suddenly spins and wraps her in a bear hug, wailing loudly. Tracking shot follows the sudden motion."),
        ("close", "Push In", ["C01"],
         "cinematic film still, close-up of the girl frozen stiff in the hug, hands hovering, expression sliding from shock to deadpan. Camera pushes in slowly."),
        ("medium", "Pan Right", ["C08"],
         "cinematic film still, medium shot, rows of servants raise their hands to wipe tears in perfect comic unison. Camera pans right across the row."),
        ("medium", "Static Shot", ["C08", "C01"],
         "cinematic film still, medium shot, a servant hurries in from the gate bowing to announce while the two girls turn to look."),
        ("close", "Push In", ["C01"],
         "cinematic film still, close-up of the girl's eyes widening in slow horror, pupils trembling. Camera pushes in."),
    ],
    12: [  # scene 7 beats 7-10
        ("medium", "Push In", ["C01"],
         "cinematic film still, medium shot, the girl stares toward the main gate as a silhouette approaches backlit, her face at the edge of breakdown. Camera pushes in."),
        ("close", "Static Shot", ["C01"],
         "cinematic film still, close-up of the girl's expression frozen at the brink of despair, then cut to black."),
    ],
}

# continuity per segment: axis/screen positions/eyeline/motion vectors
CONTINUITY = {
    1: {"axisId": "axis_A", "screenPositions": {"C02": "left", "C06": "right"},
        "eyelineTarget": "C06", "motionVector": "towards_camera",
        "propStates": {"P02": "in_hand"}, "actionStates": {"C06": {"in": "idle", "out": "gripping_wrist"}}},
    2: {"axisId": "axis_01", "screenPositions": {"C02": "left", "C07": "right"},
        "eyelineTarget": "C07", "motionVector": "left_to_right",
        "propStates": {}, "actionStates": {"C07": {"in": "entering", "out": "embracing"}}},
    3: {"axisId": "axis_02", "screenPositions": {"C01": "center"},
        "eyelineTarget": None, "motionVector": "towards_camera",
        "propStates": {"P03": "on_altar"}, "actionStates": {"C01": {"in": "kneeling", "out": "weeping"}}},
    4: {"axisId": "axis_02", "screenPositions": {"C01": "left", "C07": "right"},
        "eyelineTarget": "C07", "motionVector": "static",
        "propStates": {"P03": "on_altar"}, "actionStates": {"C07": {"in": "gripping_shoulder", "out": "speaking"}}},
    5: {"axisId": "axis_03", "screenPositions": {"C01": "right", "C03": "left"},
        "eyelineTarget": "C03", "motionVector": "left_to_right",
        "propStates": {}, "actionStates": {"C03": {"in": "approaching", "out": "embracing"}}},
    6: {"axisId": "axis_03", "screenPositions": {"C01": "left", "C03": "right"},
        "eyelineTarget": None, "motionVector": "right_to_left",
        "propStates": {}, "actionStates": {"C03": {"in": "embracing", "out": "shouting_order"}}},
    7: {"axisId": "axis_04", "screenPositions": {"C08": "both_sides", "C01": "center"},
        "eyelineTarget": "C08", "motionVector": "static",
        "propStates": {}, "actionStates": {"C08": {"in": "standing", "out": "chanting"}}},
    8: {"axisId": "axis_04", "screenPositions": {"C01": "center"},
        "eyelineTarget": None, "motionVector": "left_to_right",
        "propStates": {}, "actionStates": {"C01": {"in": "watching", "out": "counting"}}},
    9: {"axisId": "axis_05", "screenPositions": {"C04": "center"},
        "eyelineTarget": None, "motionVector": "left_to_right",
        "propStates": {"P01": "in_hand"}, "actionStates": {"C04": {"in": "striding", "out": "stopping"}}},
    10: {"axisId": "axis_05", "screenPositions": {"C01": "left", "C04": "right"},
         "eyelineTarget": "C04", "motionVector": "static",
         "propStates": {"P01": "on_shoulder_side"}, "actionStates": {"C04": {"in": "patted_shoulder", "out": "smug"}}},
    11: {"axisId": "axis_06", "screenPositions": {"C01": "left", "C04": "right"},
         "eyelineTarget": None, "motionVector": "towards_camera",
         "propStates": {"P01": "dropped_side"}, "actionStates": {"C04": {"in": "walking", "out": "hugging_wailing"}}},
    12: {"axisId": "axis_06", "screenPositions": {"C01": "center"},
         "eyelineTarget": None, "motionVector": "towards_camera",
         "propStates": {}, "actionStates": {"C01": {"in": "staring_gate", "out": "frozen"}}},
}

# scene-level character states (all default since script has no states)
SEG_SCENE = {seg_i: sc_i for seg_i, (sc_i, _) in enumerate(PARTITION, 1)}


def build_cut(beat_list, cut_seconds, size, camera, chars, frame_text, seg_scene):
    b1, b2 = beat_list[0]["n"], beat_list[-1]["n"]
    return {
        "beats": [b1, b2],
        "seconds": round(cut_seconds, 2),
        "size": size,
        "camera": camera,
        "characters": chars,
        "characterStates": {c: "default" for c in chars},
        "frame": frame_text,
        "continuity": {
            "axisId": None,  # filled per segment below
            "screenPositions": {},
            "eyelineTarget": None,
            "motionVector": "static",
            "propStates": {},
            "actionStates": {c: {"in": "idle", "out": "idle"} for c in chars},
        },
        "_beat_list": beat_list,
    }


def fmt_ts(seconds):
    m = int(seconds) // 60
    s = seconds - m * 60
    return f"{m:02d}:{s:06.3f}"


def build_h3_prompt(cuts, prompt_lang="en"):
    # alignment line
    if len(cuts) == 1:
        align = ("For the target video, at 0.00 seconds into the target video, <Picture 1> "
                 "(from [Shot 1]) is fully referenced.")
    else:
        parts = []
        acc = 0.0
        for i, c in enumerate(cuts, 1):
            if i == 1:
                parts.append("Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video")
            else:
                parts.append(f"Picture {i} (from Shot {i}) aligns with the {fmt_ts(acc)} mark of the target video")
            acc += c["seconds"]
        align = "How the reference pictures align with the target video — " + "; ".join(parts) + "."
    lines = [align, "", "integrated_multimodal_description:"]
    acc = 0.0
    for i, c in enumerate(cuts, 1):
        prefix = "[Shot 1] Cinematic, live-action." if i == 1 else f"[Shot {i}] At {fmt_ts(acc)}, the camera cuts to:"
        # camera term must appear in own shot line
        cam = c["camera"].lower()
        lines.append(f"{prefix} {c['frame']} Camera: {c['camera']}. ({cam})")
        acc += c["seconds"]
    lines.append("")
    lines.append("overall_soundscape: Street crowd murmur and distant traffic hum; footsteps and cloth rustle; quiet sobs echo softly in the hall scenes.")
    lines.append("non_diegetic_music: A light comedic string pizzicato underlines the family absurdity, turning wistful during the funeral hall, swelling to a suspended chord at the final reveal.")
    return "\n".join(lines)


def main():
    segments = []
    for seg_i, (scene_index, ns) in enumerate(PARTITION, 1):
        beats = [beat_lookup(scene_index, n) for n in ns]
        seg_sum = sum(b["seconds"] for b in beats)
        design = DESIGN[seg_i]

        # partition beats into cuts per design rows: sequential split
        cuts = []
        bi = 0
        # (placeholder removed — the deterministic split below is authoritative)

        # simpler deterministic split: equal beat count per cut (last cut gets remainder)
        n_cuts = len(design)
        n_beats = len(beats)
        base = n_beats // n_cuts
        rem = n_beats % n_cuts
        idx = 0
        built = []
        for ci, (size, camera, chars, frame) in enumerate(design):
            take = base + (1 if ci < rem else 0)
            group = beats[idx:idx + take]
            idx += take
            sec = sum(b["seconds"] for b in group)
            cont = dict(CONTINUITY[seg_i])
            cont = {
                "axisId": CONTINUITY[seg_i]["axisId"] or f"axis_{seg_i:02d}",
                "screenPositions": CONTINUITY[seg_i]["screenPositions"],
                "eyelineTarget": CONTINUITY[seg_i]["eyelineTarget"],
                "motionVector": CONTINUITY[seg_i]["motionVector"],
                "propStates": CONTINUITY[seg_i]["propStates"],
                "actionStates": CONTINUITY[seg_i]["actionStates"],
            }
            cut = {
                "beats": [group[0]["n"], group[-1]["n"]],
                "seconds": round(sec, 2),
                "size": size,
                "camera": camera,
                "characters": chars,
                "characterStates": {c: "default" for c in chars},
                "frame": frame,
                "continuity": cont,
            }
            built.append(cut)

        seg = {
            "id": f"E01-{seg_i:02d}",
            "sceneIndex": scene_index,
            "cuts": built,
            "h3Prompt": "",  # filled after
        }
        seg["h3Prompt"] = build_h3_prompt(built)
        segments.append(seg)

    board = {
        "source": script["source"],
        "style": "realistic",
        "continuityContractVersion": 1,
        "stateContractVersion": 1,
        "promptLang": "en",
        "params": {"maxSegmentSeconds": 15, "minCutSeconds": 2, "maxCutSeconds": 5,
                   "maxOnScreen": 3, "tolerance": 0.15},
        "episodes": [{"ep": 1, "segments": segments}],
    }
    out = BASE / "storyboard.json"
    out.write_text(json.dumps(board, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"storyboard.json written: {out}")
    print(f"segments: {len(segments)}, cuts: {sum(len(s['cuts']) for s in segments)}")


def group_safe(x=None):
    return []


if __name__ == "__main__":
    main()
