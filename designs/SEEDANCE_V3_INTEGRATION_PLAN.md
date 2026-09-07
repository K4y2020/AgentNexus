# Cine × Seedance V3 集成计划

> 状态：待实施方案 v1
> 编写日期：2026-09-06
> 目标：把 Cine 的拉片与改编任务，接入本机 `U:\AI\seedance-v3` 的 Agent、Canvas 和生成 Worker，形成可追踪的 Topic 级制作闭环。

## 1. 结论

Seedance V3 不需要替换 DSH Runtime，也不需要把 Provider 代码复制进 AgentNexus。

Seedance V3 已经提供三层能力：

```text
HTTP Agent API → Seedance Agent → Canvas / Command Bus / Generation Worker
```

AgentNexus 应增加一个 **Seedance Connector/Bridge**，把 Seedance V3 作为外部专业 Agent 接入现有 A2A 总线。

最终链路：

```text
Cine Topic
  └─ A2A task.request
       ↓
AgentNexus Seedance Connector
       ↓
Seedance V3 Project + Agent Session
       ↓
Canvas / Storyboard / Prompt Optimizer / generation.submit
       ↓
Seedance Worker + Provider
       ↓ SSE / Job 状态
AgentNexus durable A2A result
       ↓
Cine Topic
```

## 2. 当前系统事实

### AgentNexus / Cine

- Cine 已有 `film-analysis`、`novel-*` Skills。
- Cine C0+C1 产出证据、镜头、剪辑关系、`generation_jobs` 和 `edit_plan`。
- A2A 已支持 durable message、Topic scope、结果回传和状态展示。
- 当前 A2A 目标解析主要面向 AgentNexus 内部 Bot，不直接支持外部 HTTP Agent。

### Seedance V3

主要入口：

| 能力 | Endpoint |
|---|---|
| 创建 Project | `POST /v3/projects` |
| 创建 Agent Session | `POST /v3/projects/{projectId}/agent-sessions` |
| 给 Agent 发消息 | `POST /v3/agent-sessions/{sessionId}/messages` |
| 提交 Canvas/Generation 命令 | `POST /v3/projects/{projectId}/commands` |
| 读取项目快照 | `GET /v3/projects/{projectId}/snapshot` |
| 读取项目图 | `GET /v3/projects/{projectId}/graph` |
| 订阅项目事件 | `GET /v3/projects/{projectId}/events` |
| 查询 Generation Job | `GET /v3/jobs/{jobId}` |
| 读取 Asset | `GET /v3/assets/{assetId}/content` |

Seedance V3 的 Agent Session 使用 DSH SDK，但 DSH 已被包在 `AgentRuntimePort` 后面。AgentNexus 不应绕过 V3 Agent 直接接管 DSH。

## 3. 核心绑定规则

### 3.1 Topic 映射

一个 Cine Topic 对应一个 Seedance Project 和一个 Seedance Agent Session：

```text
AgentNexus channel_scope
  → seedance_project_id
  → seedance_agent_session_id
  → last_event_cursor
```

禁止把同一个 Seedance Project/Agent Session 复用于不同影片 Topic，否则会产生剧本、角色、画布和生成任务污染。

### 3.2 Chat 映射

普通 Chat 可以绑定一个独立的 Seedance Project，但不应自动创建项目。只有用户明确进入制作流程时，才创建 Project。

### 3.3 关联字段

AgentNexus Session labels 建议使用：

```text
seedance.project_id
seedance.agent_session_id
seedance.base_url
seedance.last_event_cursor
seedance.status
```

大对象和生成结果不放进 labels；只保存 ID、状态和游标。

## 4. Bridge 职责

Seedance Connector 负责四件事：

1. 按 Topic 获取或创建 Seedance Project/Agent Session；
2. 将 Cine 的结构化镜头合同转换为 Seedance Agent 消息；
3. 监听 Seedance Project SSE，将 Agent/Canvas/Generation 事件转为 A2A 状态；
4. 将最终 `project_id`、`agent_session_id`、`job_id`、`asset_id` 和结果 URL 回传到原始 Cine Topic。

Bridge 不负责：

- 分析原片；
- 自行改写 Cine 的证据数据；
- 直接实现 Seedance Provider；
- 绕过 Seedance Agent 的确认和安全规则；
- 把完整视频或图片二进制塞进 A2A 消息正文。

## 5. 首期实现方式

### 5.1 AgentNexus 侧

新增一个 runner-dispatched 工具或等价的 Connector 工具：

```text
seedance_agent_message
```

最小输入：

```json
{
  "task": "把已确认的镜头合同写入 Seedance 画布并准备生成",
  "shot_ids": ["S01-03"],
  "generation_allowed": false,
  "model": "seedance-2.0"
}
```

工具从当前 `conversation_id` 获取 Topic scope，不允许模型自行传入另一个 Topic 的 session 映射。

环境配置：

```text
SEEDANCE_BASE_URL=http://127.0.0.1:8893
SEEDANCE_API_KEY=<local secret>
SEEDANCE_CONNECTOR_TIMEOUT_S=900
```

密钥只在 Connector/Runner 侧使用，不写入 Prompt、A2A payload、日志或 Artifact。

### 5.2 Seedance V3 侧

首期不改 V3 核心，只复用现有 HTTP API 和事件流。

如实测发现需要更明确的异步关联，再增加一个可选的 correlation metadata 字段；不复制 AgentNexus 的 A2A 数据库。

### 5.3 A2A 结果

结果 payload 只保存引用：

```json
{
  "outcome": "succeeded",
  "seedance_project_id": "proj_xxx",
  "seedance_agent_session_id": "sess_xxx",
  "seedance_job_ids": ["job_xxx"],
  "seedance_asset_ids": ["asset_xxx"],
  "channel_scope": "topic:...",
  "summary": "已创建 3 个视频 Prompt 卡，生成任务尚未提交"
}
```

## 6. 生成权限分层

Seedance V3 的 Agent Prompt 已明确“只有用户明确要求时才提交图像或视频生成”。AgentNexus 需要继续保留这条边界：

| 操作 | 默认行为 |
|---|---|
| 创建 Project/Session | 允许 |
| 创建角色/场景/道具卡 | 允许 |
| 创建 Storyboard/Video Prompt 卡 | 允许 |
| 提交视频生成 | 必须用户确认 |
| 批量提交全片生成 | 必须明确范围、预算和并发数 |
| 使用付费/外部 Provider | 必须显式告知并确认 |

`generation_allowed=false` 时，Connector 只能让 Seedance Agent 创建卡片和计划，不得提交 Generation Job。

## 7. 首帧图像与视频生成闭环

### 7.1 Keyframe Imagegen

现有 `$imagegen` Skill 可生成 Cine 报告中缺失的首帧 PNG。流程：

```text
Storyboard shot recipe
  → imagegen PNG
  → 写入 Cine project/artifacts
  → 上传/注册为 Seedance Asset
  → 绑定到 Seedance storyboard/video_prompt node
```

验收要求：

- 29 张首帧全部有实际 PNG；
- `manifest.json.missing == 0`；
- 每张图有 sha256、shot_id 和 prompt provenance；
- 失败图片必须保留失败状态，不得用色块占位冒充完成。

### 7.2 I2VA 与 Lipsync

```text
video_prompt + keyframe assets + dialogue/audio
  → Seedance generation.submit
  → Worker/provider
  → MP4 + audio asset
  → job succeeded
  → Cine edit_plan 回写实际 asset in/out
```

“提示词和 TTS 文本已经对齐”不能算 Lipsync PASS。必须有真实视频和音频轨道，并完成：

- 生成任务成功；
- 视频文件可播放；
- 音频轨道存在且时长可读；
- 对白时间轴与镜头时间轴一致；
- 口型抽样检查通过；
- 失败、超时、取消和重试状态可追踪。

## 8. 外部 API 与失败处理

### 8.1 Agent Session 忙

Seedance V3 对同一 Agent Session 的并发消息返回 `AGENT_SESSION_BUSY`。Connector 必须：

- 按 Topic 串行派发；
- 不重复创建 Agent Session；
- 使用 A2A correlation ID 去重；
- 只在上一轮完成或明确失败后发送下一轮。

### 8.2 长任务

Agent message 可能持续较长时间，Connector 不应高频轮询：

- 初始请求等待短窗口；
- 超时后返回 `in_progress`；
- 用 Seedance SSE 事件驱动状态更新；
- A2A durable result 完成后回传原始 Topic。

### 8.3 重启恢复

必须持久化：

- `project_id`；
- `agent_session_id`；
- `last_event_cursor`；
- job/asset 映射；
- 最近一次 A2A correlation ID。

Connector 重启后从 `last_event_cursor` 继续订阅，不从头重复生成。

## 9. UI 计划

首期在 A2A Collaboration Card 增加：

- `Seedance Project` 链接；
- `Seedance Agent` 状态；
- Job ID 与生成状态；
- `打开 Seedance 画布` 操作；
- 结果 Asset 链接。

后续再做 AgentNexus 内嵌画布：读取 `/snapshot`、`/graph`，订阅 `/events`，在 Workspace 右侧用 React Flow 镜像展示。第一期不复制完整 V3 编辑器。

## 10. 分阶段计划

### Phase S0：只读探测

- 配置 Seedance Base URL/API Key；
- 成功读取 `/healthz`、`/v3/projects`；
- 验证认证、超时和错误分类；
- 不创建项目、不调用生成。

退出条件：健康检查、认证失败、服务不可用都能在 AgentNexus 中显示明确状态。

### Phase S1：Topic Project/Agent Session Bridge

- 实现 Topic → Project/Agent Session 映射；
- `seedance_agent_message` 支持文本任务；
- 结果回到原始 A2A Topic；
- 防止同一 Topic 并发消息和重复 Session。

退出条件：同一 Topic 连续两轮对话复用同一 Seedance Agent Session；两个 Topic 互不串上下文；重启后映射仍有效。

### Phase S2：镜头合同与画布

- Cine `SourceShot`/`generation_jobs` 转为 Seedance storyboard/video_prompt 节点；
- 传递角色、场景、道具和首帧 Asset 引用；
- 回传 node/job/asset ID；
- UI 展示打开画布入口。

退出条件：一个已确认镜头可以在 Seedance 画布中找到，节点引用完整，Cine 可追溯到对应节点。

### Phase S3：真实生成

- 用户确认后提交 I2VA；
- 监听 Job 状态和 SSE；
- 回写 MP4/音频 Asset；
- 失败和取消可恢复。

退出条件：至少一条真实视频任务成功，生成结果可播放并回到 Cine 的 `edit_plan`。

### Phase S4：首帧批量与 Lipsync 验收

- 生成 29 张首帧并注册 Asset；
- 批量但有界地提交视频任务；
- 接入 TTS/对白音轨；
- 对口型同步做人工抽检和可重复记录。

退出条件：`missing == 0`，首帧全部可追踪；至少一条完整 I2VA + Lipsync 任务通过真实验收。

## 11. 安全与边界

- 只允许本机或明确配置的 Seedance Base URL，禁止模型任意修改 URL；
- API Key 不进入 Agent Prompt、A2A 消息或浏览器端；
- 外部 Asset 上传前显示文件名、大小和目标 Project；
- 默认不自动提交付费生成；
- 所有跨系统调用带 `request_id`、`channel_scope`、`correlation_id`；
- Seedance 失败不能被 Cine 汇报成“已生成”；
- 只回传 Artifact/Asset 引用，不在 A2A 正文内嵌视频和图片二进制。

## 12. 推荐的最小交付

第一版只做：

1. `SEEDANCE_BASE_URL` / `SEEDANCE_API_KEY` 配置；
2. Topic → Project/Agent Session 映射；
3. 一个 `seedance_agent_message` Connector；
4. A2A durable result 回传；
5. “打开 Seedance 画布”链接；
6. 一个单镜头、用户确认后的真实 Generation Job。

先证明链路可恢复、可追踪、不会串 Topic，再做 29 张首帧批量和全片 Lipsync。
