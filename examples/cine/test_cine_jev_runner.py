"""Test runner for Cine Agent + JEV (System One) Integration.

Run with:
    # 模拟模式（无 API Key 验证契约与数据流）:
    omnigent/.venv/Scripts/python.exe omnigent/examples/cine/test_cine_jev_runner.py --mock

    # 真实在线调用 JEV (需要 TYPESAFE_API_KEY):
    omnigent/.venv/Scripts/python.exe omnigent/examples/cine/test_cine_jev_runner.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

try:
    from dotenv import load_dotenv

    load_dotenv()
    load_dotenv(Path(__file__).parent.parent.parent / ".env")
    load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")
except ImportError:
    pass

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Add omnigent/examples/cine to path
sys.path.insert(0, str(Path(__file__).parent))

from cine_jev_adapter import CineJevAdapter


def run_mock_test():
    print("\n=======================================================")
    print("🎬 [Mock 模式] 正在验证 Cine Agent + JEV 逻辑与契约集成...")
    print("=======================================================")

    adapter = CineJevAdapter(api_key="mock_key")

    # Mock response object
    mock_resp = MagicMock()
    mock_resp.scores = {
        "hook_quality": MagicMock(score=2.8, confidence=0.92),
        "visual_dynamism": MagicMock(score=2.9, confidence=0.88),
    }
    mock_resp.nouls = {
        "character_motivation": MagicMock(noul=0.91),
        "ending_payoff": MagicMock(noul=0.86),
        "runtime_plausibility": MagicMock(noul=0.84),
        "is_production_ready": MagicMock(noul=0.95),
        "motion_conflict": MagicMock(noul=0.08),
        "feasible_in_15s": MagicMock(noul=0.92),
        "is_compliant": MagicMock(noul=0.99),
    }
    mock_resp.choices = {
        "framing": MagicMock(choice="close_up", confidence=0.94),
        "camera_motion": MagicMock(choice="push_pull", confidence=0.89),
    }

    mock_client = MagicMock()
    mock_client.__enter__.return_value.system_one.return_value = mock_resp
    adapter._get_client = MagicMock(return_value=mock_client)

    # 1. 剧本验收测试
    print("\n[测试 1] 剧本验收门禁 (evaluate_script):")
    script_title = "深夜洗衣店的袜子奇案"
    script_body = """
    [地点：深夜自助洗衣店。机器轰鸣，白炽灯闪烁]
    00:01 张伟死死盯住3号滚筒，手里只剩一只红袜子。
    00:03 李敏抱着一大盆衣服走进来，脚下竟然穿着另一只一模一样的红袜子！
    00:08 张伟：“同学，你脚上这只……”
    00:12 李敏冷笑：“刚才在烘干机捡的，见者有份。”
    00:20 两人目光在滚筒间拉扯，突然机器“叮”一声，吐出一整包新红袜……
    """
    res_script = adapter.evaluate_script(script_title, script_body)
    print(json.dumps(res_script, indent=2, ensure_ascii=False))
    assert res_script["is_ready_for_storyboard"] is True
    print("  -> 剧本各项指标达标，成功批准推进到分镜阶段 (PASSED)")

    # 2. 分镜镜头分类与打分测试
    print("\n[测试 2] 分镜景别与张力打分 (classify_and_score_shot):")
    shot_desc = "微距推镜头，特写滚筒玻璃后飞速旋转的红色袜子，水珠飞溅，霓虹蓝光与红袜形成鲜明冷暖撞色。"
    res_shot = adapter.classify_and_score_shot(shot_desc)
    print(json.dumps(res_shot, indent=2, ensure_ascii=False))
    assert res_shot["framing"] == "close_up"
    print("  -> 镜头自动标记为特写 (close_up)，运镜为推拉 (push_pull) (PASSED)")

    # 3. Seedance 视频生成提示词合规与冲突检测
    print("\n[测试 3] Seedance 生成提示词前置门禁 (preflight_seedance_prompt):")
    prompt = "电影质感，35mm镜头，深夜雨中洗衣店橱窗，红衣女子推开玻璃门，雨滴顺着伞尖滑落，慢动作48fps。"
    res_preflight = adapter.preflight_seedance_prompt(prompt)
    print(json.dumps(res_preflight, indent=2, ensure_ascii=False))
    assert res_preflight["passed"] is True
    print("  -> 无运镜冲突，15s容量匹配，合规通过，安全放行至生成集群 (PASSED)")

    print("\n" + "=" * 55)
    print(" 所有的 Cine-JEV 接口契约与数据流校验全部通过！")
    print("=" * 55)


def run_live_test():
    api_key = os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        print("\n❌ 错误: 未检测到 TYPESAFE_API_KEY 环境变量。")
        print("请先配置 Key:")
        print("  Windows PowerShell: $env:TYPESAFE_API_KEY = 'sk-...'")
        print("  Git Bash:           export TYPESAFE_API_KEY='sk-...'")
        print("\n若需离线模拟验证，请运行:")
        print("  python test_cine_jev_runner.py --mock\n")
        sys.exit(1)

    print("\n=======================================================")
    print("🚀 [Live 模式] 正在调用真实 TypeSafe JEV 模型 (jev-latest)...")
    print("=======================================================")

    adapter = CineJevAdapter(api_key=api_key)

    print("\n1. 正在提交短剧剧本《深夜洗衣店》进行全维度 JEV 并行评估...")
    script_title = "深夜洗衣店的袜子奇案"
    script_body = (
        "00:01 男主死盯滚筒里的单只红袜。00:04 女主脚踩另一只出现。00:15 两人对峙争夺，机器鸣响喷出一堆红袜。"
    )
    res_script = adapter.evaluate_script(script_title, script_body)
    print("剧本评估结果:")
    print(json.dumps(res_script, indent=2, ensure_ascii=False))

    print("\n2. 正在提交镜头画面评估景别与视觉打分...")
    shot_desc = "极特写聚焦手部动作，颤抖着按下红色紧急停止按钮，警报红光闪烁。"
    res_shot = adapter.classify_and_score_shot(shot_desc)
    print("镜头分类结果:")
    print(json.dumps(res_shot, indent=2, ensure_ascii=False))

    print("\n3. 正在对 Seedance 生成提示词执行冲突与合规前置检查...")
    prompt = "电影质感，35mm镜头，雨夜街景，女孩撑伞走入画面。"
    res_prompt = adapter.preflight_seedance_prompt(prompt)
    print("Prompt 门禁结果:")
    print(json.dumps(res_prompt, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true", help="使用 Mock 模式测试接口与数据流契约")
    args = parser.parse_args()

    if args.mock or not os.environ.get("TYPESAFE_API_KEY"):
        run_mock_test()
    else:
        run_live_test()
