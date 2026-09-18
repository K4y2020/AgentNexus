# Seedance Create Node 测试方案

## 问题：建了卡但没有内容

### 测试步骤

#### 1. 检查 cinebot 的实际调用参数

在日志中查找 `seedance_edit_canvas` 的调用，确认传递了哪些参数：

```bash
tail -f ~/.agentnexus/logs/host/host-*.log | grep -A 20 "seedance_edit_canvas"
```

#### 2. 测试最小可复现案例

让 cinebot 执行以下命令：

```
请使用 seedance_edit_canvas 创建一个剧本卡，要求：
- action: create_node
- node_type: script
- title: 测试剧本卡
- data: {"content": "这是一段测试剧本内容"}

创建后，立即使用 seedance_read_canvas 读取画布，验证卡片内容是否正确保存。
```

#### 3. 对比不同 node_type 的行为

测试三种类型的卡片：

**A. Script 卡（剧本）**
```json
{
  "action": "create_node",
  "node_type": "script",
  "title": "测试剧本",
  "data": {
    "content": "场景：室内\n人物：小明\n台词：你好"
  }
}
```

**B. Video Prompt 卡（视频分镜）**
```json
{
  "action": "create_node", 
  "node_type": "video_prompt",
  "title": "S01 开场镜头",
  "prompt": "远景，清晨的城市街道，阳光洒在建筑上",
  "brief": "建立环境，展示城市氛围",
  "duration_seconds": 5,
  "camera": {
    "angle": "high-angle",
    "motion": "static"
  },
  "aspect_ratio": "16:9"
}
```

**C. Image Prompt 卡（角色/场景设定）**
```json
{
  "action": "create_node",
  "node_type": "image_prompt",
  "title": "角色：小明",
  "prompt": "一个年轻的程序员，戴着眼镜，休闲装扮",
  "aspect_ratio": "1:1"
}
```

#### 4. 验证 Seedance V3 服务状态

```bash
# 检查 Seedance 服务是否运行
curl http://127.0.0.1:8893/healthz

# 手动创建卡片测试
curl -X POST http://127.0.0.1:8893/v3/projects/{project_id}/commands \
  -H "Content-Type: application/json" \
  -H "x-api-key: YOUR_KEY" \
  -d '{
    "command": {
      "type": "canvas.create_node",
      "nodeType": "script",
      "title": "手动测试卡",
      "data": {
        "content": "这是手动创建的测试内容"
      },
      "commandId": "test_001"
    }
  }'

# 读取快照验证
curl http://127.0.0.1:8893/v3/projects/{project_id}/snapshot \
  -H "x-api-key: YOUR_KEY"
```

### 预期结果

1. **如果 cinebot 调用时没传 data**
   - 解决方案：修改 cinebot 的提示词或 skill 指导，明确要求传递内容字段

2. **如果 cinebot 传了 data 但 Seedance 没保存**
   - 问题在 Seedance V3 服务端
   - 需要检查 Seedance 的日志和数据库

3. **如果手动 API 调用也无内容**
   - 确认是 Seedance V3 的 bug
   - 需要更新 Seedance 版本或修复服务端逻辑

### 诊断命令集合

```bash
# 1. 查看最近的 seedance_edit_canvas 调用
grep -r "seedance_edit_canvas" ~/.agentnexus/logs/runner/ | tail -20

# 2. 检查创建节点的完整命令
grep -A 30 "canvas.create_node" ~/.agentnexus/logs/runner/*.log | tail -50

# 3. 查看 Seedance 服务日志（如果可访问）
tail -f ~/AI/seedance-v3/logs/*.log

# 4. 验证节点数据结构
# 在 cinebot 会话中执行：
# "使用 seedance_read_canvas 读取画布，设置 detail_level=full，查看所有节点的完整 data 字段"
```

### 临时解决方案

如果确认是参数传递问题，可以在 cinebot 的 config.yaml 中添加明确指导：

```yaml
prompt: |
  创建剧本卡时，必须包含完整的内容字段：
  
  seedance_edit_canvas({
    action: "create_node",
    node_type: "script",
    title: "场景标题",
    data: {
      content: "完整的剧本内容，包括场景描述、人物对话等"
    }
  })
  
  创建分镜卡时，必须包含 prompt 和 brief：
  
  seedance_edit_canvas({
    action: "create_node",
    node_type: "video_prompt",
    title: "镜头标题",
    prompt: "详细的镜头提示词",
    brief: "镜头简要说明",
    duration_seconds: 5
  })
```

### 检查清单

- [ ] 确认 Seedance V3 服务正在运行
- [ ] 验证 cinebot 调用时传递了内容参数
- [ ] 检查 Seedance API 响应是否包含完整数据
- [ ] 测试手动 API 调用是否能正确保存内容
- [ ] 查看 cinebot 和 Seedance 的错误日志
- [ ] 验证数据库中节点的 data 字段是否为空
