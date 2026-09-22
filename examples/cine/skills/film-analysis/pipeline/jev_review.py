"""Text-only JEV metadata review for Cine's film-analysis index.

Jev cannot receive images, audio, or video.  Results from this module therefore
live in ``jev_reviews/`` and never masquerade as visual source-shot review.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv

    load_dotenv()
    load_dotenv(Path(__file__).parent.parent.parent.parent.parent / ".env")
    load_dotenv(Path(__file__).parent.parent.parent.parent.parent.parent / ".env")
except ImportError:
    pass

from typesafe_sdk import Choice, Noul, Score, TypeSafeClient




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

def _find_typesafe_key() -> str:
    """Find TYPESAFE_API_KEY from environment or standard .env files without dependencies."""
    key = os.environ.get("TYPESAFE_API_KEY")
    if key:
        return key.strip()
    candidates = [
        Path.home() / ".agentnexus" / ".env",
        Path.home() / ".env",
        Path("U:/AI/MultiAgent/.env"),
        Path("U:/AI/MultiAgent/omnigent/.env"),
    ]
    for c in candidates:
        if c.is_file():
            try:
                for line in c.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("TYPESAFE_API_KEY="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            return val
            except Exception:
                pass
    return ""


def has_jev_configured() -> bool:
    """Check if TypeSafe JEV is configured via environment."""
    return bool(_find_typesafe_key())


def _find_video_file(project_dir: Path, source_data: dict) -> Optional[Path]:
    """Find the source video file for frame motion extraction."""
    declared = Path(source_data.get("path", ""))
    if declared.is_file():
        return declared
    topic_dir = project_dir.parent.parent
    inputs_dir = topic_dir / "inputs"
    if inputs_dir.is_dir():
        for name in ("source.mp4", "ep01.mp4", "video.mp4"):
            p = inputs_dir / name
            if p.is_file():
                return p
        for p in inputs_dir.glob("*.mp4"):
            if p.is_file():
                return p
    return None


def _calc_shot_optical_flow(video_path: Optional[Path], start_s: float, end_s: float) -> str:
    """Calculate real dense optical flow (Farneback) between head and tail frames."""
    if not video_path or not video_path.is_file():
        return "无视频源文件，无法提取光流"

    try:
        import cv2
        import numpy as np
    except ImportError:
        return "无 OpenCV 模块"

    with tempfile.TemporaryDirectory() as td:
        f1 = Path(td) / "f1.jpg"
        f2 = Path(td) / "f2.jpg"
        tail_s = max(start_s, end_s - 0.08)

        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{start_s:.3f}", "-i", str(video_path), "-vframes", "1", "-q:v", "3", str(f1)],
            capture_output=True,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{tail_s:.3f}", "-i", str(video_path), "-vframes", "1", "-q:v", "3", str(f2)],
            capture_output=True,
        )
        if not f1.is_file() or not f2.is_file():
            return "帧抽取失败"

        im1 = cv2.imread(str(f1), cv2.IMREAD_GRAYSCALE)
        im2 = cv2.imread(str(f2), cv2.IMREAD_GRAYSCALE)
        if im1 is None or im2 is None:
            return "帧解码失败"

        im1 = cv2.resize(im1, (320, 240))
        im2 = cv2.resize(im2, (320, 240))
        flow = cv2.calcOpticalFlowFarneback(im1, im2, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        fx, fy = flow[..., 0], flow[..., 1]
        mag = float(np.mean(np.sqrt(fx**2 + fy**2)))
        dx = float(np.mean(fx))
        dy = float(np.mean(fy))
        div = float(np.mean(fx[:, 2 * fx.shape[1] // 3 :]) - np.mean(fx[:, : fx.shape[1] // 3]))

        flow_info = f"真实帧间光流：位移均值 mag={mag:.2f}px, 水平dx={dx:.2f}px, 垂直dy={dy:.2f}px, 缩放散度 div={div:.2f}。"
        if div > 2.0:
            flow_info += " 画面中心向外强烈放射扩张，具有明确的向前推镜头(Push-in)特征。"
        elif div < -2.0:
            flow_info += " 画面四周向中心强烈收敛汇聚，视野扩大，具有明确的向后拉镜头(Pull-out)特征。"
        elif abs(dx) > 1.2:
            flow_info += f" 画面呈现显著的水平横摇/移镜位移({'自左向右横摇' if dx < 0 else '自右向左横摇'})。"
        elif abs(dy) > 1.2:
            flow_info += f" 画面呈现显著的垂直俯仰位移({'俯拍下摇' if dy < 0 else '仰拍上仰'})。"
        elif mag < 1.0:
            flow_info += " 画面各特征点无明显宏观位移，摄像机机位极其平稳固定。"
        else:
            flow_info += " 画面呈现连续跟拍或轻度手持随动动势。"

        return flow_info


def review_shots_with_jev(
    project_dir: Path,
    revision_id: str,
    max_shots: Optional[int] = None,
    api_key: Optional[str] = None,
    max_workers: int = 8,
) -> Dict[str, Any]:
    """Run a text/metadata review on shots of a Cine project revision.

    Optical-flow facts are supplied as text context.  They are not a substitute
    for inspecting the corresponding evidence image, so this function never
    writes the visual ``reviews/`` sidecar or image receipt IDs.
    """
    key = api_key or _find_typesafe_key()
    if not key:
        return {"status": "skipped", "reason": "no_api_key", "reviewed_count": 0}

    project_dir = Path(project_dir).resolve()
    rev_dir = project_dir / "revisions" / revision_id
    shots_path = rev_dir / "source_shots.json"
    source_path = project_dir / "source.json"

    if not shots_path.is_file() or not source_path.is_file():
        return {"status": "skipped", "reason": "missing_project_files", "reviewed_count": 0}

    source_data = json.loads(source_path.read_text(encoding="utf-8"))
    source_id = source_data.get("source_id", "")
    shots_data = json.loads(shots_path.read_text(encoding="utf-8"))

    if not shots_data:
        return {"status": "ok", "reviewed_count": 0, "shots": []}

    video_path = _find_video_file(project_dir, source_data)

    # Questions for System One
    questions = {
        "is_usable": Noul(
            instructions="评估该镜头是否具备清晰的视觉主体与构图，非失焦晃动、纯黑屏或技术残次，可作为剪辑素材？"
        ),
        "framing": Choice(
            instructions="选择该镜头的最适景别",
            criteria={
                "extreme_close_up": "极特写（眼部/细微道具/局部微距）",
                "close_up": "特写（面部表情或核心主体）",
                "medium_shot": "中景（半身或双人互动）",
                "wide_shot": "全景/远景（主体与环境关系）",
                "establishing": "大远景/纯环境空镜头",
            },
        ),
        "camera_motion": Choice(
            instructions="结合物理光流测量与镜头特征，判断该镜头的主导运镜方式",
            criteria={
                "static": "固定机位（机位保持静止稳定）",
                "pan_tilt": "摇移镜头（原地水平横摇或垂直俯仰）",
                "push_pull": "推镜头或拉镜头（向前贴近推进，或向后拉出扩大视野）",
                "tracking": "移动跟拍（机位随主体平移跟动）",
            },
        ),
        "cinematic_score": Score(
            instructions="评估镜头的电影质感与视觉构图张力",
            criteria=[
                "构图平庸/缺少焦点/无叙事价值",
                "常规合格可用记录镜头",
                "电影级质感/光影出彩/叙事张力极强",
            ],
        ),
        "cut_technique": Choice(
            instructions="从剪辑手法角度，评估该镜头最核心的剪接手法或接戏潜质",
            criteria={
                "match_cut": "动作匹配剪（Cut on action）或图形匹配，靠动势/形状顺滑衔接",
                "smash_cut": "冲击砸切（Smash cut），利用极静与爆发反差制造感官冲击",
                "j_l_cut": "声画交错剪辑，适合声音跨镜头延展",
                "straight_cut": "常规连续性硬切，平稳推进叙事",
            },
        ),
        "motion_vector": Choice(
            instructions="判断画面主体的运动动势方向（用于轴线与动势守卫）",
            criteria={
                "left_to_right": "自左向右运动",
                "right_to_left": "自右向左运动",
                "towards_camera": "纵深向前（推进）",
                "away_from_camera": "纵深向后（拉远）",
                "static": "画面动势基本静止或对称居中",
            },
        ),
    }

    # Load evidence to assist description
    evidence_path = rev_dir / "evidence_index.json"
    evidence_map = {}
    if evidence_path.is_file():
        ev_list = json.loads(evidence_path.read_text(encoding="utf-8"))
        for ev in ev_list:
            evidence_map[ev.get("evidence_id")] = ev

    target_shots = shots_data if max_shots is None else shots_data[:max_shots]

    def _eval_single_shot(idx_shot):
        idx, shot = idx_shot
        shot_id = shot["shot_id"]
        interval = shot.get("interval", {})
        tb = (
            interval.get("time_base_num", 1) / interval.get("time_base_den", 1)
            if interval.get("time_base_den")
            else 1.0
        )
        start_s = interval.get("in_pts", 0) * tb
        end_s = interval.get("out_pts", 0) * tb
        duration = end_s - start_s

        shot_ev_ids = shot.get("evidence_ids", [])
        ev_desc = ""
        for eid in shot_ev_ids:
            if eid in evidence_map:
                ev_desc += f" 证据文件: {evidence_map[eid].get('relative_path', '')}"

        # Real optical flow calculation
        motion_analysis = _calc_shot_optical_flow(video_path, start_s, end_s)

        state_desc = (
            f"镜头 {idx+1}/{len(target_shots)} [ID: {shot_id}], 时长: {duration:.2f}秒。\n"
            f"{ev_desc}\n"
            f"物理测定: {motion_analysis}\n"
            "注意：JEV 只收到这些文字和元数据，不会看到证据图片、视频或音频。"
        )

        with _open_ts_client(key) as client:
            resp = client.system_one(
                state={"shot_description": state_desc},
                questions=questions,
                model="jev-latest",
            )

        is_usable = float(resp.nouls["is_usable"].noul)
        framing = resp.choices["framing"].choice
        framing_conf = float(resp.choices["framing"].confidence)
        motion = resp.choices["camera_motion"].choice
        motion_conf = float(resp.choices["camera_motion"].confidence)
        cut_tech = resp.choices["cut_technique"].choice
        cut_tech_conf = float(resp.choices["cut_technique"].confidence)
        motion_vec = resp.choices["motion_vector"].choice
        cinematic = float(resp.scores["cinematic_score"].score)

        status = "semantic_model_reviewed" if is_usable >= 0.5 else "semantic_disputed"

        obs = [
            f"JEV文字/元数据推断景别: {framing} ({framing_conf:.2f})",
            f"JEV文字/元数据推断运镜: {motion} ({motion_conf:.2f})",
            f"JEV文字/元数据推断剪辑手法: {cut_tech} ({cut_tech_conf:.2f})",
            f"文字/元数据动势推断: {motion_vec} | 设计稿电影感分数: {cinematic:.2f}/2.0",
            f"物理光流: {motion_analysis[:35]}...",
            "视觉画面仍需 Cine 图像回执单独验收",
        ]

        return {
            "source_id": source_id,
            "revision_id": revision_id,
            "shot_id": shot_id,
            "status": status,
            "observations": obs,
            "image_receipt_ids": [],
            "metadata": {
                "framing": framing,
                "camera_motion": motion,
                "cut_technique": cut_tech,
                "motion_vector": motion_vec,
                "cinematic_score": cinematic,
                "is_usable": is_usable,
                "optical_flow": motion_analysis,
            },
        }

    # Concurrently evaluate shots with ThreadPoolExecutor
    workers = min(max_workers, len(target_shots)) if target_shots else 1
    with ThreadPoolExecutor(max_workers=workers) as executor:
        review_rows = list(executor.map(_eval_single_shot, enumerate(target_shots)))

    reviews_out = project_dir / "jev_reviews" / f"{revision_id}.json"
    reviews_out.parent.mkdir(parents=True, exist_ok=True)
    reviews_out.write_text(
        json.dumps(review_rows, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return {
        "status": "ok",
        "reviewed_count": len(review_rows),
        "review_path": str(reviews_out),
        "reviews": review_rows,
    }
