"""JEV-driven Adaptation Shot Mapping (改编选镜映射).

Consumes the film-analysis breakdown (source_shots.json + reviews) and the
ASR dialogue transcript, then uses TypeSafe JEV to make active adaptation
decisions:
1. Time-align dialogue lines to shots (deterministic, code-owned)
2. JEV selects the anchor shot for each dialogue beat (Choice)
3. JEV scores the dramatic weight of each beat (Score)
4. Writes shot_mapping_jev.json draft for the downstream adaptation pipeline
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_script_dir = Path(__file__).parent
sys.path.insert(0, str(_script_dir / "skills" / "film-analysis"))

from pipeline.jev_review import _find_typesafe_key

from typesafe_sdk import Choice, Score, TypeSafeClient




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

def load_project(project_dir: Path, revision_id: str):
    shots = json.loads(
        (project_dir / "revisions" / revision_id / "source_shots.json").read_text(encoding="utf-8")
    )
    reviews_rows = json.loads(
        (project_dir / "reviews" / f"{revision_id}.json").read_text(encoding="utf-8")
    )
    review_by_shot = {r["shot_id"]: r for r in reviews_rows}
    tb = shots[0]["interval"]["time_base_num"] / shots[0]["interval"]["time_base_den"]
    for s in shots:
        s["_start"] = s["interval"]["in_pts"] * tb
        s["_end"] = s["interval"]["out_pts"] * tb
        r = review_by_shot.get(s["shot_id"], {})
        s["_review"] = r.get("metadata", {})
    return shots, review_by_shot


def load_dialogue(project_dir: Path) -> List[dict]:
    candidates = [
        project_dir.parent.parent / "inputs" / "source-transcript.json",
    ]
    for p in candidates:
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            rows = []
            for seg in data.get("segments", []):
                text = (seg.get("text") or "").strip()
                if not text:
                    continue
                rows.append(
                    {
                        "start": float(seg.get("start", seg.get("start_seconds", 0.0)) or 0.0),
                        "end": float(seg.get("end", seg.get("end_seconds", 0.0)) or 0.0),
                        "text": text,
                        "speaker": seg.get("speaker", ""),
                    }
                )
            return rows
    return []


def dialogue_shot_alignment(shots: List[dict], dialogue: List[dict]) -> List[dict]:
    rows = []
    for d in dialogue:
        covered = [s for s in shots if s["_start"] < d["end"] and s["_end"] > d["start"]]
        rows.append({**d, "covered_shot_ids": [s["shot_id"] for s in covered]})
    return rows


def _beat_criteria(covered_ids: List[str], shots_by_id: Dict[str, dict]) -> Dict[str, str]:
    return {
        sid: (
            f"镜头 {k + 1} ({shots_by_id[sid]['_start']:.1f}s-{shots_by_id[sid]['_end']:.1f}s, "
            f"景别={shots_by_id[sid]['_review'].get('framing', '?')}, "
            f"运镜={shots_by_id[sid]['_review'].get('camera_motion', '?')})"
        )
        for k, sid in enumerate(covered_ids)
    }


ANCHOR_INSTRUCTIONS = (
    "根据台词内容与情绪，从覆盖该台词的候选镜头中选出最具叙事锚定价值的一个画面"
    "（将成为改编分镜中该台词的首选画面）。"
    "优先选择：说话人在画、景别与台词情绪匹配、画面主体明确的镜头。"
)

WEIGHT_INSTRUCTIONS = "这句台词与所选镜头配合下的戏剧张力与情绪强度"
WEIGHT_LEVELS = [
    "平淡过渡，无情绪重心",
    "有情绪起伏，具备一定叙事价值",
    "强张力节点，钩子或反转级台词",
]


def _evaluate(d: dict, shots_by_id: Dict[str, dict], api_key: str) -> dict:
    covered_ids = d["covered_shot_ids"]
    if not covered_ids:
        return {**d, "anchor_shot_id": None, "jev_status": "no_cover"}

    qs = {
        "anchor_shot": Choice(
            instructions=(
                "候选镜头按时间顺序排列，每个镜头的说明含其出现的时间区间。"
                "请选出台词发出时刻（dialogue_start）所对应的、说话人正在画面中的那个镜头"
                "——即台词应该出现在哪个画面里。用台词时间与镜头时间区间做匹配，"
                "台词起始时刻落在哪个镜头的时间区间内，就优先选那个镜头。"
            ),
            criteria=_beat_criteria(covered_ids, shots_by_id),
        ),
        "emotional_weight": Score(
            instructions=WEIGHT_INSTRUCTIONS, criteria=WEIGHT_LEVELS
        ),
    }
    state = {
        "dialogue_text": d["text"],
        "speaker": d["speaker"],
        "dialogue_start": f"{d['start']:.2f}s",
    }
    with _open_ts_client(api_key) as client:
        resp = client.system_one(state=state, questions=qs, model="jev-latest")

    # Time-overlap fallback: the shot whose interval contains the dialogue start
    # is the deterministic anchor. JEV choice is kept as a cross-check.
    time_anchor = next(
        (sid for sid in covered_ids if shots_by_id[sid]["_start"] <= d["start"] < shots_by_id[sid]["_end"]),
        covered_ids[0],
    )
    jev_pick = resp.choices["anchor_shot"].choice
    anchor = jev_pick if jev_pick in covered_ids else time_anchor

    return {
        **d,
        "anchor_shot_id": anchor,
        "jev_choice": jev_pick,
        "jev_agrees_with_time": jev_pick == time_anchor,
        "anchor_confidence": float(resp.choices["anchor_shot"].confidence),
        "emotional_weight": float(resp.scores["emotional_weight"].score),
        "jev_status": "ok",
    }


def write_mapping(project_dir: Path, revision_id: str, rows: List[dict]) -> Path:
    out = project_dir / "shot_mapping_jev.json"
    payload = {
        "schema_version": "1.0.0",
        "generator": "jev_shot_mapping_v1",
        "rows": rows,
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
    if not revision_id:
        print("No committed revision; aborting")
        return

    key = _find_typesafe_key()
    if not key:
        print("TYPESAFE_API_KEY not found; aborting")
        return

    shots, _ = load_project(project_dir, revision_id)
    shots_by_id = {s["shot_id"]: s for s in shots}
    dialogue = load_dialogue(project_dir)
    if not dialogue:
        print("No dialogue found; aborting")
        return

    aligned = dialogue_shot_alignment(shots, dialogue)
    covered_count = sum(1 for a in aligned if a["covered_shot_ids"])
    print(f"对齐完成：{len(aligned)} 句台词，其中 {covered_count} 句有覆盖镜头")

    import time

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as ex:
        evaluated = list(ex.map(lambda d: _evaluate(d, shots_by_id, key), aligned))
    t1 = time.time()
    print(f"JEV 选镜决策完成（{t1 - t0:.2f}s, {len(evaluated)} 句）")

    out = write_mapping(project_dir, revision_id, evaluated)
    print(f"映射文件已写入: {out}")

    for r in evaluated:
        anchor = r.get("anchor_shot_id")
        if anchor:
            label = f"{shots_by_id[anchor]['_start']:.1f}s#{anchor[:8]}"
        else:
            label = "(无覆盖)"
        print(
            f"[{r['start']:6.2f}s] ({r['speaker'] or '?'}): {r['text'][:22]:<24} "
            f"→ {label} (conf={r.get('anchor_confidence', 0):.2f}, 张力={r.get('emotional_weight', 0):.2f})"
        )


if __name__ == "__main__":
    main()
