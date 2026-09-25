#!/usr/bin/env python3
"""
AgentNexus 模型目录诊断工具

检查各个 SDK 的模型列表是否正确加载，并显示详细的诊断信息。
"""

import sys
from pathlib import Path

# 设置输出编码为 UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent))


def diagnose_mlflow_catalog():
    """诊断 MLflow 在线目录"""
    print("=" * 80)
    print("1. 检查 MLflow 在线目录")
    print("=" * 80)

    try:
        from agentnexus.onboarding.providers import default_chat_model, get_chat_models

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
                    print("\n前 10 个模型:")
                    for i, model in enumerate(models[:10], 1):
                        capabilities = []
                        if model.supports_function_calling:
                            capabilities.append("tools")
                        if model.supports_vision:
                            capabilities.append("vision")
                        if model.supports_reasoning:
                            capabilities.append("reasoning")

                        cap_str = f" [{', '.join(capabilities)}]" if capabilities else ""
                        ctx = (
                            f"ctx={model.max_input_tokens}" if model.max_input_tokens else "ctx=?"
                        )

                        print(f"  {i:2d}. {model.name:<40s} {ctx:>12s}{cap_str}")
                else:
                    print("⚠️  模型列表为空")

            except Exception as e:  # noqa: BLE001 — diagnostic tool reports every error
                print(f"❌ 错误: {e}")

    except ImportError as e:
        print(f"❌ 无法导入模块: {e}")


def diagnose_model_catalog():
    """诊断模型目录缓存"""
    print("\n" + "=" * 80)
    print("2. 检查模型目录缓存")
    print("=" * 80)

    try:
        import os
        from pathlib import Path

        from agentnexus.model_catalog import clear_model_catalog_cache

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

    except Exception as e:  # noqa: BLE001 — diagnostic tool reports every error
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
            ("codex", None, "Codex (无指定模型)"),
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
                    print("前 5 个模型:")
                    for m in listing.models[:5]:
                        print(f"  - {m.id} (family: {m.family})")

            except Exception as e:  # noqa: BLE001 — diagnostic tool reports every error
                print(f"❌ 错误: {e}")

    except ImportError as e:
        print(f"❌ 无法导入模块: {e}")


def main():
    print("""
╔═══════════════════════════════════════════════════════════════════════════╗
║                AgentNexus 模型目录诊断工具                                   ║
╚═══════════════════════════════════════════════════════════════════════════╝
    """)

    diagnose_mlflow_catalog()
    diagnose_model_catalog()
    diagnose_executors()

    print("\n" + "=" * 80)
    print("诊断完成")
    print("=" * 80)
    print("\n💡 建议:")
    print("  1. 如果模型列表为空，检查网络连接到 github.com")
    print("  2. 如果缓存有问题，删除 ~/.agentnexus/cache/model_catalog/")
    print()


if __name__ == "__main__":
    main()
