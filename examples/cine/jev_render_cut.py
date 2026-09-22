"""Render the JEV rough-cut sequence to an actual video file with ffmpeg.

Reads cut_sequence_jev.json (monotonic A-anchor + B-broll fragment list),
extracts each fragment from the source video, concatenates with hard cuts,
and writes the assembled rough cut MP4.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def find_source(project_dir: Path) -> Path | None:
    topic_dir = project_dir.parent.parent
    inputs = topic_dir / "inputs"
    for name in ("ep01.mp4", "source.mp4", "video.mp4"):
        p = inputs / name
        if p.is_file():
            return p
    for p in inputs.glob("*.mp4"):
        return p
    return None


def main():
    project_dir = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(
            "C:/Users/Kay/.agentnexus/bots/228b85aa41315810985501ea37343d13/topics/"
            "1186da8969fee630b9ef5c95aeb7a48e/projects/hongloumeng-ep01"
        )
    )
    data = json.loads((project_dir / "cut_sequence_jev.json").read_text(encoding="utf-8"))
    seq = data["sequence"]

    order = []
    for a in seq:
        order.append((a["start"], a["end"], a["shot_id"], "A"))
        b = a.get("broll_insert")
        if b:
            order.append((b["t0"], b["t1"], b["shot_id"], "B"))

    source = find_source(project_dir)
    if not source:
        print("No source video found; aborting")
        return

    out_dir = project_dir / "render"
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        # 1) Extract each fragment as ts segment (re-encode for exact cut)
        seg_files = []
        for i, (s, e, sid, tag) in enumerate(order):
            seg = tdp / f"seg_{i:03d}.ts"
            dur = e - s
            r = subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", f"{s:.3f}", "-i", str(source),
                    "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "fast",
                    "-crf", "20", "-c:a", "aac", "-b:a", "128k",
                    "-avoid_negative_ts", "make_zero", str(seg),
                ],
                capture_output=True,
            )
            if seg.is_file():
                seg_files.append(seg)
            else:
                print(f"seg {i} extract failed: {r.stderr.decode()[:200]}")

        # 2) Concat
        concat_list = tdp / "concat.txt"
        concat_list.write_text(
            "".join(f"file '{f.as_posix()}'\n" for f in seg_files), encoding="utf-8"
        )
        out_path = out_dir / "rough_cut_jev.mp4"
        r = subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
                "-c", "copy", str(out_path),
            ],
            capture_output=True,
        )
        if not out_path.is_file():
            # fallback re-encode concat
            r = subprocess.run(
                [
                    "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                    "-c:a", "aac", "-b:a", "128k", str(out_path),
                ],
                capture_output=True,
            )

    if out_path.is_file():
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration,size",
             "-of", "default=noprint_wrappers=1", str(out_path)],
            capture_output=True, text=True,
        )
        print(f"✅ 粗剪成片已渲染: {out_path}")
        print(probe.stdout.strip())
        print(f"片段数: {len(order)}")
    else:
        print(f"❌ 渲染失败: {r.stderr.decode()[:400]}")


if __name__ == "__main__":
    main()
