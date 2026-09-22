"""Interactive Demonstration of Cine JEV Shot Pre-Screening & Montage Grammar Engine.

Runs live against TypeSafe JEV model to evaluate:
- Shot usability & framing
- Editing cut techniques (Match Cut, Smash Cut, etc.)
- Motion vector & Eyeline continuity
- Inter-shot cut transition checks (动势对冲、视线匹配、砸切时机)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))

from cine_shot_screener import CineShotScreener


def main():
    print("=================================================================")
    print("🎬 Cine Agent - JEV 镜头初筛与蒙太奇剪辑手法引擎 (Live Pipeline)")
    print("=================================================================")

    candidate_shots = [
        {
            "shot_id": "shot_01_hook",
            "desc": "极远景大空镜，大雨倾盆的黑色死寂城市，仅远处闪电撕裂乌云，全景无运动，交代压抑开场。",
        },
        {
            "shot_id": "shot_02_punch",
            "desc": "特写镜头，拳击手右臂如雷霆般全力向右出拳，拳套掠过镜头右侧带起汗珠，动作极快未完成出画。",
        },
        {
            "shot_id": "shot_03_hit",
            "desc": "中景，对手面部被重击向右猛烈扭曲倒地，身体砸在擂台围绳上，动势持续向右延伸。",
        },
        {
            "shot_id": "shot_04_glance_left",
            "desc": "近景特写，裁判神情凝重看向画面左侧倒地的选手，右侧给面部留出空间，眼神聚焦紧张。",
        },
        {
            "shot_id": "shot_05_motion_clash",
            "desc": "全景跟拍，观众席另一侧黑衣人突然由右向左急速逆向穿行，与之前的向右动势产生剧烈反向对冲。",
        },
        {
            "shot_id": "shot_06_garbage",
            "desc": "摄像机摔在地上，纯黑失焦地面摩擦持续0.4秒，无任何可用构图。",
        },
    ]

    screener = CineShotScreener()

    print(f"\n📥 正在将 {len(candidate_shots)} 个镜头提交给 JEV 进行【可用性+景别+剪辑手法+动势语法】全维度评估...\n")

    report = screener.screen_batch(candidate_shots)

    print("-----------------------------------------------------------------")
    print("📊 镜头初筛与剪辑手法全景报告:")
    print("-----------------------------------------------------------------")
    print(f"• 候选镜头总数:        {report.total_shots}")
    print(f"• ✅ 优质保留 (ACCEPTED):        {report.accepted_count} 个")
    print(f"• ❌ 自动剔除 (REJECTED):        {report.rejected_count} 个")
    print(f"• ⚠️  存疑待查 (NEEDS_REVIEW):   {report.review_needed_count} 个")
    print(f"• 剪辑可用率:                   {report.usable_ratio * 100:.1f}%")
    print(f"• 平均电影感得分:              {report.average_cinematic_score:.2f} / 2.0")
    print(f"• 平均适剪度得分:              {report.average_editability_score:.2f} / 2.0")

    print("\n[剪辑手法分布]:", report.by_cut_technique)
    print("[动势方向分布]:", report.by_motion)
    print("[景别分布]:    ", report.by_framing)

    print("\n-----------------------------------------------------------------")
    print("📋 各镜头剪辑语法判定明细 (Detailed Montage Attributes):")
    print("-----------------------------------------------------------------")
    shot_map = {}
    for s in report.shots:
        shot_map[s.shot_id] = s
        icon = "✅" if s.decision == "ACCEPTED" else ("❌" if s.decision == "REJECTED" else "⚠️")
        print(f"\n{icon} [{s.shot_id}] 决策: {s.decision}")
        print(f"   描述: {s.description[:42]}...")
        print(f"   🎬 剪辑手法: {s.cut_technique} (置信度: {s.cut_technique_confidence:.2f}) | 动作卡点性: {s.cut_on_action_probability:.2f}")
        print(f"   ⚡ 动势方向: {s.motion_vector} (置信度: {s.motion_vector_confidence:.2f}) | 视线: {s.eyeline_vector}")
        print(f"   🎯 适剪度: {s.editability_score:.2f}/2.0 | 电影感: {s.cinematic_score:.2f}/2.0 | 可用概率: {s.is_usable_probability:.2f}")

    print("\n-----------------------------------------------------------------")
    print("✂️  连续镜头接戏与语法连续性实机校验 (Cut Pair Transitions):")
    print("-----------------------------------------------------------------")

    # 校验 1: 出拳 -> 挨打倒地 (经典的动作匹配切 Match Cut)
    t1 = screener.validate_cut_transition(shot_map["shot_02_punch"], shot_map["shot_03_hit"])
    print(f"\n[转场 1: 出拳 ➔ 倒地] ({t1.shot_a_id} -> {t1.shot_b_id})")
    print(f"  • 建议剪辑手法: {t1.recommended_cut_type}")
    print(f"  • 动势连续性:   {t1.motion_continuity} (顺动势向右)")
    print(f"  • 接戏顺滑评分: {t1.transition_score:.2f} / 2.0")
    print(f"  • 剪辑指导建议: {t1.advice}")

    # 校验 2: 顺向动作 -> 突兀逆向运动 (动势对冲警报)
    t2 = screener.validate_cut_transition(shot_map["shot_03_hit"], shot_map["shot_05_motion_clash"])
    print(f"\n[转场 2: 倒地 ➔ 逆向反跑] ({t2.shot_a_id} -> {t2.shot_b_id})")
    print(f"  • 建议剪辑手法: {t2.recommended_cut_type}")
    print(f"  • 动势连续性:   {t2.motion_continuity} (对冲警报)")
    print(f"  • 接戏顺滑评分: {t2.transition_score:.2f} / 2.0")
    print(f"  • 剪辑指导建议: {t2.advice}")

    # 导出到 Cine 规范
    export_path = Path("omnigent/examples/cine/output/reviews_montage_demo.json")
    screener.export_to_cine_reviews(
        report=report,
        source_id="src_montage_video",
        revision_id="rev_montage_001",
        output_path=export_path,
    )
    print(f"\n💾 成功将包含蒙太奇剪辑元数据的初筛文件导出至: {export_path}")
    print("=================================================================")


if __name__ == "__main__":
    main()
