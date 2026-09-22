"""JEV-driven Cut Sequence Assembly (动势衔接与连续性粗剪).

Consumes shot_mapping_jev.json (anchor shots per dialogue beat) and the
film-analysis breakdown, then:
1. Orders anchor shots chronologically into a cut sequence (deterministic)
2. Uses existing motion_vector data to check motion continuity at each cut
3. JEV validates narrative continuity of each adjacent pair (Noul)
4. Marks failing cuts for review / inserts B-roll suggestions
5. Writes cut_sequence_jev.json with an assembly order ready for ffmpeg
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_script_dir = Path(__file__).parent
sys.path.insert(0, str(_script_dir / "skills" / "film-analysis"))

from pipeline.jev_review import _find_typesafe_key
from typesafe_sdk import Noul, Score, TypeSafeClient

OPPOSITES = {
    ("left_to_right", "right_to_left"),
    ("right_to_left", "left_to_right"),
    ("towards_camera", "away_from_camera"),
    ("away_from_camera", "towards_camera"),
}




def _open_ts_client(api_key: str):
    """Open a TypeSafe client that survives a misconfigured loopback proxy.

    The SDK otherwise inherits the ambient HTTP client, which fails with a bare
    SSL EOF when the machine routes through a plaintext proxy configured with
    an ``https://`` scheme. See ``jev_transport`` for the details.
    """
    try:
        from .jev_transport import open_client
    except ImportError:
        import sys as _sys
        from pathlib import Path as _Path

        _pipeline = _Path(__file__).resolve().parent
        if str(_pipeline) not in _sys.path:
            _sys.path.insert(0, str(_pipeline))
        from jev_transport import open_client
    return open_client(api_key)

def load_data(project_dir: Path, revision_id: str):
    shots = json.loads(
        (project_dir / "revisions" / revision_id / "source_shots.json").read_text(encoding="utf-8")
    )
    tb = shots[0]["interval"]["time_base_num"] / shots[0]["interval"]["time_base_den"]
    shot_meta: Dict[str, dict] = {}
    for s in shots:
        shot_meta[s["shot_id"]] = {
            "shot_id": s["shot_id"],
            "start": s["interval"]["in_pts"] * tb,
            "end": s["interval"]["out_pts"] * tb,
        }
    reviews_rows = json.loads(
        (project_dir / "reviews" / f"{revision_id}.json").read_text(encoding="utf-8")
    )
    review_by_shot = {r["shot_id"]: r for r in reviews_rows}
    for sid, meta in shot_meta.items():
        r = review_by_shot.get(sid, {})
        m = r.get("metadata", {})
        meta["framing"] = m.get("framing", "?")
        meta["camera_motion"] = m.get("camera_motion", "?")
        meta["motion_vector"] = m.get("motion_vector", "static")
        meta["cinematic_score"] = m.get("cinematic_score", 0.0)
        meta["is_usable"] = m.get("is_usable", 0.0)

    mapping = json.loads((project_dir / "shot_mapping_jev.json").read_text(encoding="utf-8"))
    return shot_meta, mapping["rows"]


def build_cut_sequence(rows: List[dict], shot_meta: Dict[str, dict]) -> List[dict]:
    """Deduplicate anchors (consecutive beats in same shot collapse) and order by time."""
    seq: List[dict] = []
    for r in rows:
        sid = r.get("anchor_shot_id")
        if not sid or sid not in shot_meta:
            continue
        if seq and seq[-1]["shot_id"] == sid:
            # Same shot continues; merge dialogue
            seq[-1]["dialogues"].append(
                {"start": r["start"], "text": r["text"], "speaker": r["speaker"],
                 "tension": r.get("emotional_weight")}
            )
            seq[-1]["tension_peak"] = max(
                seq[-1]["tension_peak"], r.get("emotional_weight") or 0.0
            )
        else:
            seq.append(
                {
                    "shot_id": sid,
                    **shot_meta[sid],
                    "dialogues": [
                        {"start": r["start"], "text": r["text"], "speaker": r["speaker"],
                         "tension": r.get("emotional_weight")}
                    ],
                    "tension_peak": r.get("emotional_weight") or 0.0,
                }
            )
    return seq


def check_motion_continuity(seq: List[dict]) -> List[dict]:
    for a, b in zip(seq, seq[1:]):
        va, vb = a["motion_vector"], b["motion_vector"]
        if (va, vb) in OPPOSITES:
            a["motion_continuity"] = "OPPOSITE_CLASH"
        elif va == vb and va != "static":
            a["motion_continuity"] = "MATCHED"
        elif "static" in (va, vb):
            a["motion_continuity"] = "NEUTRAL"
        else:
            a["motion_continuity"] = "PARALLEL"
    if seq:
        seq[-1]["motion_continuity"] = "END"
    return seq


CONTINUITY_INSTRUCTIONS = (
    "两句台词与两个画面共同构成一次镜头切换。判断这个切换在叙事上是否连贯："
    "后一句台词是否能在前一个画面的情境下自然接上，观众是否会因场景/人物突兀跳变而困惑。"
)

CONTINUITY_LEVELS = [
    "连贯：切换后观众能自然跟随叙事",
    "有跳跃：观众需要短暂适应，但可以理解",
    "断裂：人物/场景/时空跳变会让观众困惑或出戏",
]


def jev_continuity_gate(
    seq: List[dict],
    api_key: str,
    max_workers: int = 6,
) -> List[dict]:
    """JEV scores narrative continuity at each cut point."""

    def _eval_pair(i: int) -> None:
        a, b = seq[i], seq[i + 1]
        d_prev = a["dialogues"][-1]
        d_next = b["dialogues"][0]
        qs = {
            "narrative_continuity": Score(
                instructions=CONTINUITY_INSTRUCTIONS, criteria=CONTINUITY_LEVELS
            ),
            "needs_broll": Noul(
                instructions="若直接硬切会产生叙事断裂，是否需要插入一个过渡镜头（B-roll/空镜/反应镜头）来缓冲？"
            ),
        }
        state = {
            "cut_position": f"{a['end']:.1f}s",
            "prev_shot": f"{a['start']:.1f}s-{a['end']:.1f}s 景别={a['framing']} 运镜={a['camera_motion']}",
            "prev_dialogue": f"({d_prev['speaker']}) {d_prev['text']}",
            "next_shot": f"{b['start']:.1f}s-{b['end']:.1f}s 景别={b['framing']} 运镜={b['camera_motion']}",
            "next_dialogue": f"({d_next['speaker']}) {d_next['text']}",
        }
        with _open_ts_client(api_key) as client:
            resp = client.system_one(state=state, questions=qs, model="jev-latest")
        a["continuity_score"] = float(resp.scores["narrative_continuity"].score)
        a["continuity_confidence"] = float(resp.scores["narrative_continuity"].confidence)
        a["needs_broll"] = float(resp.nouls["needs_broll"].noul)

    pairs = list(range(len(seq) - 1))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(_eval_pair, pairs))
    return seq


def write_sequence(project_dir: Path, revision_id: str, seq: List[dict]) -> Path:
    out = project_dir / "cut_sequence_jev.json"
    payload = {
        "schema_version": "1.0.0",
        "generator": "jev_cut_sequence_v1",
        "sequence": seq,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def main():
    project_dir = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(
            "C:/Users/Kay/.agentnexus/bots/228b85aa41315810985501ea37343d13/topics/"
            "1186da8969fee630b9ef5c95aeb7a48e/projects/hongloumeng-ep01"
        )
    )
    revision_id = json.loads((project_dir / "project.json").read_text(encoding="utf-8")).get(
        "current_revision"
    )
    key = _find_typesafe_key()
    if not key:
        print("TYPESAFE_API_KEY not found; aborting")
        return

    shot_meta, rows = load_data(project_dir, revision_id)
    seq = build_cut_sequence(rows, shot_meta)
    print(f"粗剪序列：{len(rows)} 句台词去重合并为 {len(seq)} 个锚点镜头段")

    seq = check_motion_continuity(seq)
    clashes = [a for a in seq if a.get("motion_continuity") == "OPPOSITE_CLASH"]
    print(f"动势对冲警报: {len(clashes) if (clashes := [a for a in seq if a.get('motion_continuity') == 'OPPOSITE_CLASH']) else 0} 处")

    import time
    t0 = time.time()
    seq = jev_continuity_gate(seq, key)
    t1 = time.time()
    print(f"JEV 连续性门禁完成（{t1 - t0:.2f}s, {len(seq) - 1} 个切点）")

    breaks = [a for a in seq[:-1] if a.get("continuity_score", 2.0) < 1.0]
    broll = [a for a in seq[:-1] if a.get("needs_broll", 0) > 0.5]
    print(f"叙事断裂 (<1.0): {len(breaks)} 处 | 建议插过渡镜头: {len(broll)} 处")

    out = write_sequence(project_dir, revision_id, seq)
    print(f"粗剪序列已写入: {out}")

    for i, a in enumerate(seq[:-1]):
        flag = "❌断裂" if a.get("continuity_score", 2) < 1.0 else ("⚠️需过渡" if a.get("needs_broll", 0) > 0.5 else "✅连贯")
        print(
            f"切点{i+1:02d} @{a['end']:6.1f}s: 连贯度={a.get('continuity_score', 0):.2f} "
            f"动势={a.get('motion_continuity','?'):<15} {flag} | 下一镜 {b if False else seq[i+1]['shot_id'][:8]}"
        )


if __name__ == "__main__":
    main()
