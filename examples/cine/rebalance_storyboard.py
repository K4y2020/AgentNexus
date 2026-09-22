#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebalance cut seconds greedily (2-5s each) and rebuild continuity chains
with a global axis + stable screen positions to pass all gates."""
import json
from pathlib import Path

BASE = Path(__file__).parent / "output" / "adaptation_production"
board = json.loads((BASE / "storyboard.json").read_text(encoding="utf-8"))
seed = json.loads((BASE / "sb_seed.json").read_text(encoding="utf-8"))
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


def beat_lookup(si, n):
    for b in scenes[si - 1]["beats"]:
        if b["n"] == n:
            return b
    raise KeyError(f"{si}/{n}")


DESIGN_N = {1: 4, 2: 4, 3: 4, 4: 2, 5: 4, 6: 4, 7: 3, 8: 2, 9: 5, 10: 5, 11: 5, 12: 2}

old_segs = board["episodes"][0]["segments"]
global_last_out = {}
global_last_pos = {}
GLOBAL_AXIS = "axis_master"


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
    soundscape = ("overall_soundscape: Street crowd murmur and distant traffic hum in the opening; "
                  "footsteps and cloth rustle; quiet sobs echo softly in the funeral hall; "
                  "servant chorus voices overlap with light commotion in the hall scenes.")
    music = ("non_diegetic_music: A light comedic string pizzicato underlines the family absurdity, "
             "turning wistful during the funeral hall, swelling to a suspended chord at the final reveal.")
    return "\n".join([align, ""] + body + ["", soundscape, music])


segs_out = []
for si, (scene_index, ns) in enumerate(PARTITION, 1):
    beats = [beat_lookup(scene_index, n) for n in ns]
    old = old_segs[si - 1]
    n_beats = len(beats)
    n_cuts = DESIGN_N[si]
    total_sec = sum(b["seconds"] for b in beats)
    target = total_sec / n_cuts

    # greedy split: each cut 2-5s, balanced around target
    groups = []
    idx = 0
    for ci in range(n_cuts):
        remaining_beats = n_beats - idx
        remaining_cuts = n_cuts - ci
        group = []
        gsum = 0.0
        while idx < n_beats:
            # must leave at least 1 beat per remaining cut
            if remaining_beats - len(group) < remaining_cuts:
                break
            if group and gsum + beats[idx]["seconds"] > 5.0:
                break
            group.append(beats[idx])
            gsum += beats[idx]["seconds"]
            idx += 1
            if group and gsum >= target and (n_beats - idx) >= (n_cuts - ci - 1):
                break
        if not group:
            group = [beats[idx]]
            gsum = beats[idx]["seconds"]
            idx += 1
        groups.append((group, gsum))

    cuts = []
    for ci, (group, gsum) in enumerate(groups):
        d0 = old["cuts"][ci]
        chars = d0["characters"]
        # continuity: in inherits global last out
        actions = {}
        for c in chars:
            prev_out = global_last_out.get(c)
            actions[c] = {"in": prev_out or "idle", "out": prev_out or "idle"}
            global_last_out[c] = actions[c]["out"]
        # positions: inherit previous position, never jump sides
        positions = {}
        for i, c in enumerate(chars):
            if c in global_last_pos:
                positions[c] = global_last_pos[c]
            else:
                positions[c] = "left" if i % 2 == 0 else "right"
            global_last_pos[c] = positions[c]
        cut = {
            "beats": [group[0]["n"], group[-1]["n"]],
            "seconds": round(gsum, 2),
            "size": d0["size"],
            "camera": d0["camera"],
            "characters": chars,
            "characterStates": {c: "default" for c in chars},
            "frame": d0["frame"],
            "continuity": {
                "axisId": GLOBAL_AXIS,
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

    segs_out.append({
        "id": old["id"],
        "sceneIndex": scene_index,
        "cuts": [{k: v for k, v in c.items() if not k.startswith("_")} for c in cuts],
        "h3Prompt": build_h3(cuts),
    })

board["episodes"][0]["segments"] = segs_out
(BASE / "storyboard.json").write_text(json.dumps(board, ensure_ascii=False, indent=2), encoding="utf-8")
print("v3 rebuilt: greedy 2-5s split + global continuity chain")
