# Cinebot 画布内容问题 - 完整解决方案

## 问题描述
用户报告：Cinebot 创建剧本卡后，卡片显示为空，没有内容。

## 调查过程

### 1. 初步检查
- ✅ `seedance_edit_canvas` 工具已正确实现
- ✅ 工具已在 `ToolManager` 中注册
- ✅ 运行时调度逻辑正确
- ✅ Cinebot 配置文件正确引用工具

### 2. 实际测试
创建测试脚本验证 Seedance V3 API：
```python
# 测试创建 script 节点
result = await create_node(
    node_type="script",
    title="测试剧本卡",
    data={"content": "这是测试内容"}
)
# ✅ 测试通过 - API 工作正常
```

### 3. 检查实际画布数据
查询用户的画布项目（proj_99541a8a1b3d4c94b22bc6d2fe471448）：
```python
nodes = get_snapshot()
for node in nodes:
    if node.type == "script":
        print(f"content: {len(node.data.content)}")  # 0 字符 ❌
        print(f"prompt: {len(node.data.prompt)}")    # 2202 字符 ✅
```

**发现问题：内容存储在错误的字段！**

## 根本原因

### 代码缺陷位置
`omnigent/seedance/bridge.py` 第 478-496 行：

```python
# 错误的实现
def create_node(...):
    node_data = dict(data or {})
    if prompt is not None:
        node_data["prompt"] = prompt  # ❌ 无条件放入 prompt 字段
    # ...
```

### 问题分析
1. **字段使用规范：**
   - `script` / `text` 节点 → 内容应在 `data.content`
   - `video_prompt` / `image_prompt` 节点 → 提示词在 `prompt`

2. **实际情况：**
   - Cinebot 调用时传递 `prompt` 参数（包含剧本内容）
   - Bridge 代码未区分节点类型，直接将 `prompt` 存入 `data.prompt`
   - 导致 script 节点的 `content` 字段为空

## 解决方案

### 修复 1：代码逻辑修正

**文件：** `omnigent/seedance/bridge.py`

```python
# 修复后的实现
if action == "create_node":
    node_data = dict(data or {})
    actual_node_type = node_type or "video_prompt"

    if actual_node_type in ("script", "text"):
        # Script/text 节点：prompt 参数映射到 content
        if prompt is not None and "content" not in node_data:
            node_data["content"] = prompt
        if brief is not None:
            node_data["brief"] = brief
    else:
        # Video/image prompt 节点：使用 prompt 字段
        if prompt is not None:
            node_data["prompt"] = prompt
        if brief is not None:
            node_data["brief"] = brief
    # ...
```

**Commit:** `8dcd2812` - fix(seedance): correct field mapping for script nodes

### 修复 2：数据迁移

创建迁移脚本修复现有节点：

```python
# migrate_script_nodes.py
for node in script_nodes:
    if not node.data.content and node.data.prompt:
        # 将内容从 prompt 移动到 content
        update_node(
            node_id=node.id,
            patch={
                "data": {
                    "content": node.data.prompt,
                    "brief": node.data.brief,
                    "prompt": ""
                }
            }
        )
```

**执行结果：**
```
[OK] 《天道精算师》EP01 - 迁移成功 (2202 字符)
[OK] 《天道精算师》EP02 - 迁移成功 (1959 字符)
[OK] 《天道精算师》EP03 - 迁移成功 (2082 字符)
```

**Commit:** `9dc27011` - chore: add script node migration and test tools

## 验证结果

### 修复前
```
EP01: content=0 chars, prompt=2202 chars ❌
EP02: content=0 chars, prompt=1959 chars ❌
EP03: content=0 chars, prompt=2082 chars ❌
```

### 修复后
```
EP01: content=2202 chars, prompt=0 chars ✅
EP02: content=1959 chars, prompt=0 chars ✅
EP03: content=2082 chars, prompt=0 chars ✅
```

## 影响范围

### 新建节点
- ✅ 新创建的 script 节点自动使用正确字段
- ✅ Cinebot 现在可以正常创建有内容的剧本卡
- ✅ 不影响 video_prompt / image_prompt 节点

### 现有节点
- ✅ 已通过迁移脚本修复
- ✅ 所有内容完整可见
- ✅ 数据一致性已恢复

## 相关文件

### 修改的源代码
- `omnigent/seedance/bridge.py` - 核心修复

### 测试和工具
- `test_seedance_debug.py` - API 测试脚本
- `test_script_fix.py` - 修复验证脚本
- `migrate_script_nodes.py` - 数据迁移脚本

### 文档
- `test_cinebot_canvas.md` - 初步调试文档
- `test_seedance_create.md` - 测试方案文档

## 经验教训

1. **字段语义化很重要**
   - 不同节点类型应使用不同字段存储内容
   - 需要在 bridge 层做正确的字段映射

2. **测试覆盖度**
   - 应该为不同节点类型编写单元测试
   - 避免字段使用错误影响生产数据

3. **数据迁移策略**
   - 修复代码后要考虑现有数据的迁移
   - 提供迁移脚本和验证工具

## 总结

**问题：** Cinebot 创建的 script 节点内容为空  
**原因：** 字段映射错误，内容存入了 `prompt` 而非 `content`  
**解决：** 修复 bridge.py 逻辑 + 迁移现有数据  
**状态：** ✅ 完全解决

---

**调试日期：** 2026-09-08  
**影响项目：** AgentNexus (Omnigent fork)  
**相关组件：** Seedance V3 Bridge, Cinebot
