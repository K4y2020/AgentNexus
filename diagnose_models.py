#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AgentNexus 模型目录诊断工具

检查各个 SDK 的模型列表是否正确加载，并显示详细的诊断信息。
"""

import sys
import os
from pathlib import Path

# 设置输出编码为 UTF-8
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent))

def diagnose_mlflow_catalog():
    """诊断 MLflow 在线目录"""
    print("=" * 80)
    print("1. 检查 MLflow 在线目录")
    print("=" * 80)

    try:
        from agentnexus.onboarding.providers import get_chat_models, default_chat_model

        providers = ["anthropic", "openai", "databricks"]

        for provider in providers:
            print(f"\n📦 Provider: {provider}")
            print("-" * 40)

            try:
                models = get_chat_models(provider)
                default = default_chat_model(provider)

                print(f"✅ 可用模型数量: {len(models)}")
                print(f"✅ 默认模型: {default or 'None'}")

                if models:
                    print(f"\n前 10 个模型:")
                    for i, model in enumerate(models[:10], 1):
                        capabilities = []
                        if model.supports_function_calling:
                            capabilities.append("tools")
                        if model.supports_vision:
                            capabilities.append("vision")
                        if model.supports_reasoning:
                            capabilities.append("reasoning")

                        cap_str = f" [{', '.join(capabilities)}]" if capabilities else ""
                        ctx = f"ctx={model.max_input_tokens}" if model.max_input_tokens else "ctx=?"

                        print(f"  {i:2d}. {model.name:<40s} {ctx:>12s}{cap_str}")
                else:
                    print("⚠️  模型列表为空")

            except Exception as e:
                print(f"❌ 错误: {e}")

    except ImportError as e:
        print(f"❌ 无法导入模块: {e}")


def diagnose_model_catalog():
    """诊断模型目录缓存"""
    print("\n" + "=" * 80)
    print("2. 检查模型目录缓存")
    print("=" * 80)

    try:
        from agentnexus.model_catalog import clear_model_catalog_cache
        import os
        from pathlib import Path

        config_home = Path(os.environ.get("AGENTNEXUS_CONFIG_HOME", Path.home() / ".agentnexus"))
        cache_dir = config_home / "cache" / "model_catalog"

        print(f"\n📁 缓存目录: {cache_dir}")

        if cache_dir.exists():
            files = list(cache_dir.iterdir())
            print(f"✅ 缓存文件数量: {len(files)}")

            if files:
                print("\n缓存文件:")
                for f in files[:10]:
                    size_kb = f.stat().st_size / 1024
                    print(f"  - {f.name:<40s} {size_kb:>8.1f} KB")
            else:
                print("⚠️  缓存目录为空")
        else:
            print("⚠️  缓存目录不存在")

        # 测试清除缓存
        print("\n🗑️  测试清除内存缓存...")
        clear_model_catalog_cache()
        print("✅ 内存缓存已清除")

    except Exception as e:
        print(f"❌ 错误: {e}")


def diagnose_executors():
    """诊断各个执行器的模型解析"""
    print("\n" + "=" * 80)
    print("3. 检查执行器模型解析")
    print("=" * 80)

    try:
        from agentnexus.model_catalog import (
            list_models_for_worker,
            resolve_model_provider,
        )
        from agentnexus.spec.types import AgentSpec, ExecutorSpec

        # 测试不同的执行器
        test_cases = [
            ("claude-sdk", None, "Claude SDK (无指定模型)"),
            ("claude-sdk", "claude-opus-4.8", "Claude SDK (指定 Opus)"),
            ("codex", None, "Codex (无指定模型)"),
            ("codex", "gpt-4o", "Codex (指定 GPT-4o)"),
            ("cursor", None, "Cursor (无指定模型)"),
            ("pi", None, "Pi (无指定模型)"),
        ]

        for harness, model, description in test_cases:
            print(f"\n🔧 {description}")
            print("-" * 40)

            try:
                spec = AgentSpec(
                    name="test",
                    executor=ExecutorSpec(type=harness, model=model),
                )

                provider = resolve_model_provider(spec, harness)
                print(f"Provider: {provider.kind} / {provider.family or 'N/A'}")
                print(f"Detail: {provider.detail}")

                listing = list_models_for_worker(spec, harness)
                print(f"Source: {listing.source} (verified: {listing.verified})")
                print(f"Models: {len(listing.models)} 个")

                if listing.note:
                    print(f"Note: {listing.note}")

                if listing.models:
                    print(f"前 5 个模型:")
                    for m in listing.models[:5]:
                        print(f"  - {m.id} (family: {m.family})")

            except Exception as e:
                print(f"❌ 错误: {e}")

    except ImportError as e:
        print(f"❌ 无法导入模块: {e}")


def diagnose_code_references():
    """检查代码中的模型引用"""
    print("\n" + "=" * 80)
    print("4. 检查代码中的过时模型引用")
    print("=" * 80)

    import re
    from pathlib import Path

    # 过时的模型引用模式
    old_patterns = [
        (r'gpt-5[.\d-]*', "GPT-5 系列 (不存在)"),
        (r'claude-opus-4-8', "Claude Opus 4-8 (应为 4.8)"),
        (r'claude-sonnet-4-6', "Claude Sonnet 4-6 (应为 4.6)"),
        (r'databricks-gpt-5', "Databricks GPT-5 (不存在)"),
    ]

    agentnexus_dir = Path(__file__).parent / "agentnexus"

    if not agentnexus_dir.exists():
        print("⚠️  agentnexus 目录不存在")
        return

    findings = []

    for pattern, description in old_patterns:
        regex = re.compile(pattern)

        for py_file in agentnexus_dir.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
                matches = regex.findall(content)

                if matches:
                    unique_matches = set(matches)
                    findings.append((py_file, description, unique_matches))

            except Exception:
                continue

    if findings:
        print(f"\n⚠️  发现 {len(findings)} 个文件包含过时的模型引用:\n")

        for file_path, description, matches in findings[:20]:  # 只显示前 20 个
            rel_path = file_path.relative_to(Path(__file__).parent)
            print(f"  📄 {rel_path}")
            print(f"     {description}: {', '.join(sorted(matches))}")
    else:
        print("✅ 未发现过时的模型引用")


def main():
    print("""
╔═══════════════════════════════════════════════════════════════════════════╗
║                AgentNexus 模型目录诊断工具                                   ║
╚═══════════════════════════════════════════════════════════════════════════╝
    """)

    diagnose_mlflow_catalog()
    diagnose_model_catalog()
    diagnose_executors()
    diagnose_code_references()

    print("\n" + "=" * 80)
    print("诊断完成")
    print("=" * 80)
    print("\n💡 建议:")
    print("  1. 如果模型列表为空，检查网络连接到 github.com")
    print("  2. 如果看到过时的模型引用，运行批量替换脚本")
    print("  3. 如果缓存有问题，删除 ~/.agentnexus/cache/model_catalog/")
    print()


if __name__ == "__main__":
    main()
