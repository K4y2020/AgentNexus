# AgentNexus 模型引用更新指南

## 问题分析

各个 SDK 执行器中的模型列表示例使用了不正确或过时的模型名称：

### 当前问题

1. **Claude 模型命名不一致**
   - 代码中: `claude-opus-4-8`
   - 应该是: `claude-opus-4.8` 或 `claude-5-opus-20250514`

2. **OpenAI/GPT 模型不存在**
   - 代码中: `gpt-5.4-mini`, `gpt-5-4`, `gpt-5.3-codex`
   - 应该是: `gpt-4o`, `gpt-4o-mini`, `o1-preview`, `o1-mini`

3. **Databricks 模型前缀混乱**
   - 代码中: `databricks-gpt-5-4`, `databricks-claude-opus-4-8`
   - 应该遵循: `databricks-<vendor>-<model>` 格式

## 正确的模型名称参考

### Claude 模型 (Anthropic)
```python
CLAUDE_MODELS = [
    # Claude 5 系列 (最新)
    "claude-5-opus-20250514",      # Opus 5
    "claude-5-sonnet-20250514",    # Sonnet 5
    "claude-5-haiku-20250514",     # Haiku 5
    
    # Claude 4 系列
    "claude-opus-4.8",
    "claude-sonnet-4.6",
    "claude-haiku-4.5",
    
    # 别名
    "claude-opus-latest",
    "claude-sonnet-latest",
    "claude-haiku-latest",
]
```

### OpenAI 模型
```python
OPENAI_MODELS = [
    # GPT-4o 系列
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4o-2024-11-20",
    
    # O1 系列 (推理模型)
    "o1",
    "o1-mini",
    "o1-preview",
    
    # GPT-4 系列
    "gpt-4-turbo",
    "gpt-4",
    
    # GPT-3.5
    "gpt-3.5-turbo",
]
```

### Databricks 模型
```python
DATABRICKS_MODELS = [
    # Claude 系列
    "databricks-claude-opus-4.8",
    "databricks-claude-sonnet-4.6",
    "databricks-claude-haiku-4.5",
    
    # OpenAI 系列
    "databricks-gpt-4o",
    "databricks-gpt-4o-mini",
    
    # Unity Catalog 系列
    "system.ai.claude-opus-4.8",
    "system.ai.gpt-4o",
]
```

### Cursor 模型
```python
CURSOR_MODELS = [
    "auto-smart",      # Cursor 自动选择
    "composer-2.5",    # Cursor Composer
    "gpt-4o",
    "claude-sonnet-4.6",
]
```

### Copilot 模型
```python
COPILOT_MODELS = [
    "auto",
    "claude-haiku-4.5",
    "gpt-4o-mini",
]
```

## 需要修改的文件

### 1. 核心执行器
- `agentnexus/inner/claude_sdk_executor.py`
- `agentnexus/inner/codex_executor.py`
- `agentnexus/inner/cursor_executor.py`
- `agentnexus/inner/copilot_executor.py`
- `agentnexus/inner/openai_agents_sdk_executor.py`
- `agentnexus/inner/pi_executor.py`

### 2. 模型目录和元数据
- `agentnexus/model_catalog.py`
- `agentnexus/model_metadata.py`
- `agentnexus/claude_model_vocabulary.py`

### 3. 文档和示例
- `agentnexus/inner/claude_sdk_harness.py` (docstring)
- `agentnexus/inner/codex_harness.py` (docstring)
- `agentnexus/inner/cursor_harness.py` (docstring)
- 所有 README 和示例代码

## 修复策略

### 短期（立即）
1. 更新所有文档字符串和注释中的示例模型名称
2. 修正硬编码的默认模型
3. 更新测试用例中的模型引用

### 中期（1-2 周）
1. 实现动态模型发现（已有 `model_catalog.py`）
2. 添加模型别名映射（处理历史兼容性）
3. 增强模型验证逻辑

### 长期（1-3 月）
1. 定期同步上游模型目录
2. 添加模型生命周期管理（废弃警告）
3. 实现智能模型推荐

## 批量替换命令

```bash
# 1. 替换 Claude 模型引用
find agentnexus -name "*.py" -exec sed -i 's/claude-opus-4-8/claude-opus-4.8/g' {} +
find agentnexus -name "*.py" -exec sed -i 's/claude-sonnet-4-6/claude-sonnet-4.6/g' {} +

# 2. 替换 GPT 不存在的模型
find agentnexus -name "*.py" -exec sed -i 's/gpt-5\.4-mini/gpt-4o-mini/g' {} +
find agentnexus -name "*.py" -exec sed -i 's/gpt-5-4/gpt-4o/g' {} +
find agentnexus -name "*.py" -exec sed -i 's/gpt-5\.3-codex/gpt-4o/g' {} +

# 3. 替换 Databricks 模型
find agentnexus -name "*.py" -exec sed -i 's/databricks-gpt-5-4/databricks-gpt-4o/g' {} +
find agentnexus -name "*.py" -exec sed -i 's/databricks-claude-opus-4-8/databricks-claude-opus-4.8/g' {} +
```

## 实施建议

1. **不要破坏现有配置**: 添加别名映射而不是直接删除旧名称
2. **渐进式迁移**: 先标记废弃，再逐步移除
3. **用户通知**: 在日志中显示模型迁移提示
4. **自动修复**: 提供配置迁移工具

## 测试清单

- [ ] Claude SDK 执行器可以使用正确的模型名称
- [ ] Codex 执行器可以使用正确的模型名称
- [ ] 模型验证逻辑正确拒绝不存在的模型
- [ ] 文档中的示例都使用真实模型
- [ ] 别名映射正常工作
- [ ] 模型发现 API 返回正确的列表
