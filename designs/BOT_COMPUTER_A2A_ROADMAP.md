# AgentNexus Bot / Computer / A2A 规划

> 状态：提案 v2（双模型评审修订）
> 更新时间：2026-09-04
> 关联文档：`designs/harness-bot-team-proposal.md`

本文保留关联文档的多 Harness 与“Bot 不等于 CLI”原则，并取代其中关于后续
Computer/A2A 阶段的实施顺序。两份文档冲突时，以本文为准。

## 1. 结论

AgentNexus 应在产品语义上向 Rakazo 靠拢，但不迁移到 Rakazo 的 Pi-only
运行时，也不重写现有 Session、Runner、Harness 和 Worktree 体系。

目标是补齐一层稳定的 Bot 与 Computer 资源模型：

```text
Bot
├── Profile / Model Binding / Memory / Routines
├── Inbox / A2A
├── Topics (Sessions)
├── Computer Binding
└── Sub-agents (temporary Sessions)

Computer
├── Local Host
├── Docker
└── Cloud Sandbox (future)
```

核心原则：

1. Bot 是持久身份，不是 CLI、Harness、Terminal 或 Session。
2. Session 是 Bot 的 Topic；一个 Bot 可以有多个 Session。
3. Computer 是独立资源；Bot 绑定 Computer，Session 借用 Bot 的 Computer。
4. Harness 是执行引擎；同一个 Harness 可以服务多个 Bot。
5. Sub-agent 是短命 Session，不自动升级为持久 Bot。
6. A2A 必须投递到目标 Bot 的固定通道，不能投递到“最新会话”。
7. 用户保存的 Bot/子 Agent 模型绑定是权威配置，编排器不能覆盖。

## 2. 为什么现在推进

Teammates 已经从演示层进入可用阶段：

- Debby、Polly 已作为常驻 Teammates 展示；
- Bot 设置已有 Profile、模型、Memory、Routines 和 Skills 页面；
- 每个 Bot 已有 primary conversation；
- A2A 已使用独立 channel，不再复用最新用户会话；
- A2A 已具备 durable delivery、自动唤醒和目标 runner 修复；
- Polly 的子 Agent 模型设置已在派发入口强制生效；
- 子 Agent Session 已与 Bot/Harness 概念分离。

当前最大缺口不是 UI，而是所有权链仍不完整：

```text
当前：Teammate view -> Agent -> primary Session -> 临时选择 Host/Workspace
目标：Bot -> Computer Binding -> Host/Workspace -> Topics
```

目前 Teammate 仍主要是“内置 Agent + 若干 Session 标签”的聚合视图，Workspace
偏好还会落在前端 localStorage 或 Session 上。只要换 Topic、换浏览器或由 Routine/A2A
唤醒，就可能重新走工作区选择逻辑。这会削弱“持久同事”的产品承诺。

## 3. Rakazo 借鉴边界

### 3.1 借鉴

- Bot、Thread、Run、Computer 分离；
- Team Computer 与 Dedicated Computer 两种模式；
- Computer Provider 生命周期抽象；
- 可持久恢复的 Home，而不是依赖某台机器的磁盘快照；
- Bot-to-Bot 异步消息、固定线程、幂等投递和自动回传；
- 用户接管桌面时使用租约与 fencing；
- 持久 Bot 与短命 subagent 的明确区分。

### 3.2 不照搬

- 不改为 Pi-only Runtime；
- 不把所有 Agent 状态写进虚拟机文件系统；
- 不让每个 Session 拥有独立电脑；
- 不在第一阶段引入完整 Linux 图形桌面；
- 不把 Docker 作为唯一部署方式；
- 不在第一阶段实现 Google A2A Protocol 对外互联。

AgentNexus 的差异化继续保留：多 Harness、跨厂商互审、原生 CLI、已有 Host
daemon、Runner tunnel、Git worktree、三层策略与模型网关。

## 4. 目标领域模型

### 4.1 Bot

新增持久 `Bot` 实体，替代“Teammate 只是 Agent 聚合视图”的临时形态。

建议字段：

```text
Bot
  id
  owner_id
  agent_id
  name
  description
  status                 active | archived
  default_model
  behavior_mode
  created_at
  updated_at
```

职责：

- 持久身份和设置入口；
- 持有 Memory、Routines、Topics、A2A channel；
- 由下游 Binding 表关联 Computer，不反向持有外键；
- 为新 Session 提供模型、行为模式、权限和 Workspace 默认值。

`Agent` 继续表示可执行定义和版本化 bundle；`Bot` 表示用户拥有的常驻实例。
多个 Bot 可以绑定同一个 Agent 版本，但拥有不同记忆、模型和电脑。

### 4.2 Topic / Session

不新增重复的 Topic 表，先将 Session 作为 Topic 使用：

```text
Session
  bot_id
  purpose                primary | topic | routine | a2a | subagent | standalone
  singleton_slot         primary | a2a | NULL
  parent_session_id
  project_id
  computer_binding_id    snapshot/reference
```

约束：

- 每个用户可见 Session 必须归属一个 Bot 或明确标记为 standalone；
- `Session.bot_id -> Bot.id` 是唯一实体依赖方向，Bot 表不保存 Session 外键；
- 默认聊天入口通过 `WHERE bot_id=? AND purpose='primary'` 查询；
- 固定机器通信入口通过 `WHERE bot_id=? AND purpose='a2a'` 查询；
- primary/a2a 的 `singleton_slot` 分别写同名值，其他 Session 必须为 NULL；
- 数据库建立可移植唯一索引 `UNIQUE(bot_id, singleton_slot)`，利用三种目标数据库均允许
  多个 NULL 的语义，在不限制普通 Topic 数量的同时保证每个 Bot 只有一个 primary/a2a；
- CHECK/写入校验保证 `singleton_slot` 只能与同名 `purpose` 配对；
- primary/a2a Session 不允许通过普通 Topic 归档入口删除；
- A2A Session 不出现在普通 Topic 列表；
- Routine 可以创建独立 Topic，但必须归属触发它的 Bot；
- Sub-agent Session 归属父 Bot，同时保留 `parent_session_id`。

该结构消除 `Bot.primary_session_id <-> Session.bot_id` 循环外键。迁移顺序固定为：
先建 Bot，再给 Session 回填 `bot_id/purpose/singleton_slot`，最后建立唯一索引；任何阶段都不需要
临时关闭外键或插入悬空行。

### 4.3 Computer

新增独立 `Computer` 与 `BotComputerBinding`：

```text
Computer
  id
  owner_id
  provider_kind          local_host | docker | managed | e2b | daytona
  provider_ref
  state                  stopped | provisioning | running | sleeping | error
  host_id
  home_key
  home_revision
  capabilities
  last_active_at

BotComputerBinding
  id
  bot_id                 UNIQUE, FK -> Bot.id
  computer_id
  home_path
  created_at
  updated_at

BotProjectBinding
  bot_id
  project_id
  checkout_root
  default_branch
  UNIQUE(bot_id, project_id)
```

默认策略：

- Phase 1-3 每个 Teammate Bot 只支持 `dedicated`；
- 普通 standalone Session 维持当前工作区选择行为；
- `team` 模式推迟到 Docker Provider 稳定后的 Phase 4；
- 同一 Bot 的全部 Topics 继承同一个 Bot Home，但不绑定单一代码仓库；
- `Session.project_id` 选择 `BotProjectBinding`，从而支持一个 Bot 同时服务多个仓库；
- 无 Project 的 Topic 使用 `<bot-home>/scratch`，不得从最近 Session 猜路径；
- Session 临时选择 Project 只修改该 Topic，不悄悄回写 Bot 的项目默认值。

目录约定：

```text
<bot-home>/
  scratch/
  projects/<project-id>/checkout/
  worktrees/<project-id>/<run-id>/
```

`Bot Home` 是持久身份目录，`Project checkout` 是仓库基线，`Run worktree` 是并发写隔离。
三者不能合并成一个 `workspace_root` 字段。

### 4.4 Run 与 Lease

现有 turn/task 执行继续沿用 Runner。Computer 层只负责提供稳定执行位置。

第一阶段只新增执行租约：

```text
ComputerExecutionLease
  computer_id
  bot_id
  session_id
  run_id
  fence
  expires_at

```

并发规则：

- 只读 Run 可以共享 Project checkout；
- Git 仓库中的任何写 Run 必须在
  `<bot-home>/worktrees/<project-id>/<run-id>` 独立 worktree 执行；
- 两个写 Run 永远不能共享 Git index 或工作目录；
- 非 Git 目录的写 Run 必须获取排他的 `ComputerExecutionLease`，拿不到即明确等待/失败；
- worktree 创建失败时不得退回主 checkout 裸跑；
- Run 完成后先保留分支和产物，再安全移除 worktree；清理失败进入 quarantine，不能
  阻塞结果回传；
- 图形桌面的 `ComputerControlLease` 到 Phase 5 再设计，前期不建表、不预留死字段。

## 5. Computer Provider 架构

### 5.1 Provider 契约

第一版接口只覆盖 AgentNexus 真正需要的能力：

```python
class ComputerProvider(Protocol):
    async def provision(spec) -> ComputerRef: ...
    async def reconnect(ref) -> ComputerRef: ...
    async def stop(ref) -> None: ...
    async def destroy(ref) -> None: ...
    async def checkpoint(ref) -> HomeRevision: ...
    async def restore(ref, revision) -> None: ...
    async def capabilities(ref) -> ComputerCapabilities: ...
```

Provider 不直接执行 LLM。它最终向现有执行层提供：

```text
host_id + bot_home + capability snapshot
```

实际 Run 的 cwd 不由 Provider 单独决定，而由
`bot_id + session.project_id + run_id + access_mode` 解析为 scratch、Project checkout 或
独立 worktree。

Session 创建、Routine 和 A2A 唤醒继续走现有 Host/Runner/Harness 链路：

```text
Bot -> ComputerProvider -> Host -> Runner -> Harness -> CLI/SDK
```

### 5.2 Local Host Provider

第一阶段把当前机器包装为 `local_host` provider：

- 复用已注册 Host；
- `bot_home` 使用 Bot 独立目录；
- 不创建 VM/容器；
- 立即验证 Bot -> Computer -> Session 继承链；
- 保持 Windows、macOS、Linux 当前开发体验。

建议默认 Bot Home：

```text
~/.agentnexus/bots/<bot-id>/workspace
```

用户选择 Git 项目时创建/更新 `BotProjectBinding`；普通 Topic 使用该项目 checkout，
并发写 Run 使用 Bot 专属 worktree。任何路径解析都不得退回进程当前目录，也不得从
最近打开的 Session 猜测。

### 5.3 Docker Provider

第二个 provider 提供真正隔离：

- 每个 Dedicated Computer 一个容器；
- Team Computer 可共享一个容器，但默认不开启；
- Runner 在容器内启动，继续使用 tunnel 连接服务端；
- 第一版只支持 shell、files、git 和 harness；
- Home 挂载到 AgentNexus 管理的持久卷；
- 容器销毁不丢失 Bot Home；
- 镜像不内置用户模型密钥，凭据通过现有代理/短期令牌注入。

### 5.4 Graphical Desktop Provider Extension

Docker shell/files 稳定后再增加：

- Xvfb；
- 轻量窗口管理器；
- Chromium；
- 截图与鼠标键盘 action API；
- x11vnc/noVNC 或等价流；
- Screen capability proxy；
- Take Control / Release Control；
- control lease 与 execution fence。

图形能力作为 capability extension，不进入基础 Provider 必选接口，避免阻塞本机 Host
和无桌面的云执行环境。

### 5.5 Cloud Provider

E2B、Daytona、Box 等延后到本地 Docker 合约稳定后。云 provider 必须实现同一
checkpoint/restore 语义，`provider_ref` 只是加速恢复的 opaque reference，不是唯一持久
状态。

## 6. A2A 目标架构

### 6.1 内部 A2A

现有 `send_to_teammate`、coordination message 和 dedicated A2A session 继续作为基础。

目标流程：

```text
Sender Bot
  -> create durable A2AMessage
  -> append sender receipt
  -> resolve Session(bot_id=target, purpose=a2a)
  -> ensure target Computer/Runner available
  -> queue target delivery
  -> wake target Bot Run
  -> target completes
  -> append result to sender inbox
```

Phase 1 不新增一套重复的复杂消息表，而是在现有 coordination message/outbox 上补齐
Bot 所有权。最小持久契约只有 7 个字段：

```text
A2AMessage
  message_id
  source_bot_id
  target_bot_id
  intent                 request | question | result | status | fyi
  payload
  state                  pending | delivered | failed
  created_at
```

sender/recipient Session、idempotency、attempt/error 继续由现有 coordination/outbox/delivery
表承担，不复制到新实体。`correlation_id`、`parent_message_id`、`hop` 与自动 result 回传
推迟到 Phase 3，等基本投递语义稳定后再加。

### 6.2 必须保持的约束

- 目标只能由 `target_bot_id` 解析，不能使用最近 Session；
- 默认投递到 `Session(bot_id=target, purpose='a2a')`；
- 一个消息只有一个 durable outbox entry；
- 重试必须幂等；
- A2A 消息正文作为不可信 peer content 注入；
- 保留 source、target、correlation 和 delivery receipt；
- 最大 hop 建议为 6，超过后停止自动转发并通知用户；
- 禁止 self-send；
- 目标 Bot 离线时消息保持 pending，由恢复器继续；
- 请求完成后自动回传 result，除非目标已显式回传；
- 前端同时展示发送方回执和目标 Bot 的接收记录。

### 6.3 内部 A2A 与标准 A2A

第一阶段不实现 Google A2A Protocol。内部消息总线先稳定，再增加 adapter：

```text
Internal A2A Message <-> A2A Gateway <-> Agent Card / Task / Artifact
```

对外 A2A 必须经过显式连接与批准，不能因为知道 Bot 名称就跨用户或跨 Space 投递。

### 6.4 A2A 授权矩阵

Phase 1-3 采用 fail-closed 规则：

| Source 与 Target 关系 | 默认行为 | 额外条件 |
|---|---|---|
| 同 owner、同 Space | 允许 | 两个 Bot 均 active，Source 对 Target 可见 |
| 同 owner、跨 Space | 拒绝 | Target 显式批准 connection 后允许 |
| 跨 owner | 拒绝 | Phase 6 前不可开启 |
| 任意 self-send | 拒绝 | 无例外 |

授权必须在服务端用 `source_bot_id + target_bot_id + actor` 重新校验；不能信任 Runner
提交的 Bot 名称、owner 或 Space。Target 的 connection allowlist 是唯一授权来源，撤销后
新的投递立即失败，已入队但未送达的消息进入 cancelled/dead-letter。附件复制同时要求
Source 文件读权限与 Target A2A Session 写权限，并在同一授权决策下执行。

## 7. 模型与 Harness 所有权

Bot 设置是配置事实来源：

```text
Saved per-worker model binding
  > explicit per-dispatch user override (only when no binding exists)
  > Agent spec default
  > compatible parent-session inheritance
  > orchestrator/router suggestion
  > provider default
```

实现要求：

- Polly 只选择 worker 和任务，不选择已配置 worker 的模型；
- A2A 唤醒不得把发送方模型复制给目标 Bot；
- 新 Topic 继承 Bot 模型，但后续 Topic 内显式切换不回写 Bot；
- 子 Agent 新 Session 使用该 worker 的模型 binding；
- Harness 仍由 Agent/sub-agent spec 决定，不因模型名称自动替换；
- 所有派发回执最终应展示 requested/effective model 与来源。

## 8. UI 信息架构

### 8.1 左侧导航

```text
Bots
  Polly
    Main
    Topic A
    Topic B
  Debby
    Main
    Topic C

Projects
Standalone Sessions
Automations
```

规则：

- Bot 在一级导航中稳定存在；
- Bot 的 Sessions 收纳在 Bot 下，不与 standalone Session 混排；
- A2A channel 默认隐藏，在 Activity/Inbox 中查看；
- Project 可以关联多个 Bot Topic，但不改变 Bot 所有权；
- Sub-agent Session 在父 Topic 的 Runs/Delegation 视图中展示。

### 8.2 Bot 设置

统一设置页签：

1. Profile
2. Model & Execution
3. Computer
4. Memory
5. Routines
6. A2A & Connections
7. Tools & Skills
8. Policies

Computer 页展示：

- Dedicated / Team；
- Provider；
- Workspace；
- Host、Runner、Computer 状态；
- Start / Sleep / Restart；
- Files；
- 图形 provider 可用时展示 Screen 与 Take Control。

### 8.3 A2A 可观察性

必须显示：

- 谁派给谁；
- 任务摘要和 intent；
- queued/running/completed/failed；
- 实际目标 channel；
- attempt 与错误原因；
- 回传结果与 correlation；
- 实际 Harness、模型和 Computer。

## 9. 分阶段交付

### Phase 0：语义收口与基线测试

范围：

- 冻结 Bot、Session、Harness、Behavior Pack、Skill、Computer 术语；
- 将现有 Teammate/A2A 行为写入契约测试；
- 确认 primary 与 A2A channel 唯一；
- 冻结单向外键和 singleton-slot 唯一索引 DDL；
- 冻结 Home/Project/Worktree 路径解析规则；
- 清理仍以 CLI 名称表达 Bot 的文案；
- 派发回执补充 effective model/source。

Exit Criteria：

- 契约测试断言连续创建 100 个 Topic，A2A 的 `recipient_session_id` 始终等于该 Bot
  唯一 `purpose='a2a'` Session，错投数为 0；
- 删除/归档任意 Topic 后，primary/a2a 唯一索引仍成立；
- 路径解析测试覆盖空 localStorage、不同进程 cwd 和最近 Session 指向其他仓库，结果
  仍只由 Bot/Project/Run 决定；
- 派发测试断言用户模型设置与 effective model 100% 一致；
- Bot 与 sub-agent 在 UI/API 中有不同 kind；
- SQLite 与 PostgreSQL migration upgrade/downgrade 测试通过。

### Phase 1：持久 Bot、Topic 归属与 Local Home

范围：

- 新增 Bot store/entity/migration；
- 将内置 Teammates 注册为 Bot 实例；
- Session 增加 `bot_id` 与 `purpose`；
- 在 Session 上建立 primary/A2A singleton-slot 唯一索引，不再仅靠 labels；
- 新增最小 Local Host binding，为每个 Bot 分配固定 `bot_home`；
- 新建 Topic、Routine 和 A2A Session 均从 Bot binding 解析 Home；
- 删除前端 localStorage 的 Bot workspace 事实来源；
- Teammates API 迁移到 Bots API，保留兼容 alias；
- 保留现有 Sidebar 的 Bot/Topic 分组，将归属依据从 `agent_id/agent_name`
  推断迁移为服务端权威的 `Session.bot_id`，并隐藏 A2A channel。

Exit Criteria：

- 新建、刷新、重启服务后 Bot 身份与 Topics 不变；
- 每个 Topic 可追溯到唯一 Bot；
- 删除/归档 Topic 不影响 Bot；
- 归档 Bot 会停止 Routines 与新 A2A，但保留历史；
- 清空浏览器存储并重启 server/runner 后，同一 Bot 的 100 次新 Topic 均落入其固定
  Home，路径漂移数为 0；
- Debby 与 Polly 绑定不同 Home 时，双方 Topic 不交叉；
- migration 回填不生成悬空 `bot_id`，重复执行结果一致。

### Phase 2：多 Project、Worktree 隔离与 Computer Provider

范围：

- 完成 Computer、Binding、Provider registry；
- 将 Phase 1 的 Local Host binding 收口为 Local Host Provider；
- 新增 `BotProjectBinding`，允许同一 Bot 绑定多个 Project；
- Session 使用 `project_id` 选择 checkout；
- Git 写 Run 自动创建 `<bot-home>/worktrees/<project-id>/<run-id>`；
- 非 Git 写 Run 使用排他 execution lease；
- 修复 workspaceless/hostless Bot Session 的创建入口；
- 提供 Computer 状态 API 与设置 UI。

Exit Criteria：

- 一个 Polly 同时绑定前端、后端两个 Project，Topic 分别解析到正确 checkout，100 次
  切换错仓数为 0；
- 并发启动 10 个 Git 写 Run，每个 Run 的 cwd、Git index 和 branch 均不同，
  `index.lock` 冲突数为 0；
- worktree 创建失败时 Run 明确失败，不得在主 checkout 产生任何 diff；
- 两个非 Git 写 Run 不能同时获得同一路径的 execution lease；
- Host 暂时离线后，A2A 保持 pending，恢复后自动运行；
- 切换 Computer 需要显式操作且有审计记录；
- 全部 Run 结束后无活动 worktree 泄漏；清理失败项可在 quarantine 中观测和重试。

### Phase 3：A2A 产品化

范围：

- 固化 A2AMessage/Delivery 状态机；
- 在 Phase 1 的 7 字段消息上增加 correlation、hop、idempotency；
- 自动 result 回传；
- Bot Inbox 与 Activity UI；
- dead-letter/retry/reconcile；
- Bot connection ACL。

验收：

- 重启 server/runner 不丢消息、不重复执行；
- 两个 Bot 连续互派不会超过 hop 上限；
- 失败原因在发送方和目标方都可见；
- 并发派发不串 Topic；
- 未授权 Bot 无法跨 owner/Space 通信。

### Phase 4：Docker Computer Provider

范围：

- Supervisor 管理容器生命周期；
- Runner 在容器内连接现有 server tunnel；
- 持久 Home volume；
- shell/files/git/harness 能力；
- idle sleep、resume、destroy；
- checkpoint/restore 与 provider replacement。
- 在 Dedicated 稳定后增加显式 opt-in 的 Team Computer；
- Team 模式按 Bot/Run 分配执行租约，不承诺 provider 不支持的多屏能力。

验收：

- Dedicated Bot 之间文件不可见；
- 容器销毁重建后文件与 Git 状态可恢复；
- Bot CLI/Harness 进程仍按 Session 启停；
- 密钥不写入镜像和持久 Home；
- Docker 不可用时 Local Host Provider 不受影响。

### Phase 5：图形桌面与人工接管

范围：

- observe/act/screen provider extension；
- noVNC/等价 viewer；
- Take Control 租约；
- 此阶段才新增 `ComputerControlLease` 与 control fence 数据模型；
- screenshot 去重；
- browser profile checkpoint；
- 审批和敏感输入等待状态。

验收：

- Bot 可观察并操作确定性测试页面；
- 用户接管时 Bot 无法同时发送输入；
- 释放控制后 Run 可继续；
- 屏幕 capability URL 不泄露 provider secret；
- 浏览器登录状态可在同一 Computer 重启后恢复。

### Phase 6：Cloud Computer 与标准 A2A Adapter

范围：

- E2B/Daytona 等 provider；
- provider-neutral checkpoint；
- 外部 A2A Agent Card/Task adapter；
- connection approval、rate limit 与审计。

该阶段由真实客户需求触发，不作为 Teammates 可用性的前置条件。

## 10. API 草案

```text
GET    /v1/bots
POST   /v1/bots
GET    /v1/bots/{bot_id}
PATCH  /v1/bots/{bot_id}
POST   /v1/bots/{bot_id}/archive

GET    /v1/bots/{bot_id}/topics
POST   /v1/bots/{bot_id}/topics
GET    /v1/bots/{bot_id}/inbox

GET    /v1/bots/{bot_id}/computer
PATCH  /v1/bots/{bot_id}/computer-binding
POST   /v1/computers/{computer_id}/start
POST   /v1/computers/{computer_id}/sleep
POST   /v1/computers/{computer_id}/restart
GET    /v1/computers/{computer_id}/screen

POST   /v1/a2a/messages
GET    /v1/a2a/messages/{message_id}
POST   /v1/a2a/messages/{message_id}/retry
```

兼容期继续提供：

```text
GET /v1/teammates
send_to_teammate
```

内部将其解析到 Bot/A2A API，避免一次性破坏现有 Debby/Polly。

## 11. 数据迁移

迁移必须可重复、可回滚且不删除用户会话：

1. 创建 Bot 表；此时不引用 Session；
2. 为每个现有内置 Teammate Agent 创建一个 Bot；
3. 给 Session 增加 nullable `bot_id/purpose/singleton_slot`，并建立单向
   `Session.bot_id -> Bot.id` 外键；
4. 优先使用 `agentnexus.teammate.primary=true` 回填 `purpose='primary'`；
5. 使用 `agentnexus.teammate.channel=a2a` 回填 `purpose='a2a'`；
6. 同一 Bot 出现多个 primary/a2a 候选时保留显式标签且最早创建的一条，其余降级为
   `topic`，并记录 migration audit；
7. 其余同 Agent Session 按父子关系与创建时间归入 Bot Topics；
8. 验证无空 Bot、无重复 primary/a2a 后再建立
   `UNIQUE(bot_id, singleton_slot)`；
9. 从 primary session labels 回填模型和 behavior 设置；
10. 从现有 workspace/host 信息创建 Local Host binding 与 Bot Home；
11. 已有关联 `project_id` 的 Session 回填 `BotProjectBinding`，不把任一仓库设为 Bot
    唯一 workspace；
12. localStorage 仅用于一次性导入建议，服务端写入成功后立即停止读取；
13. labels 在一个兼容周期内双写，确认稳定后再停止读取。

## 12. 安全要求

- Computer/Binding/A2A 全部做 owner 与 Space ACL，并执行 6.4 的授权矩阵；
- Dedicated Computer 默认不可被其他 Bot 访问；
- Team Computer 到 Phase 4 才开放，UI 必须明确提示共享文件与登录状态；
- A2A body 必须标记为不可信 peer content；
- delivery、execution 使用幂等键和 fence；control fence 到 Phase 5 才引入；
- 截图与控制 URL 使用短期 capability token；
- Provider secret 不返回前端；
- Runner 使用短期绑定 token；
- unattended Routine 必须继承 Bot cost/policy 限制；
- Docker/云电脑默认限制 egress，并记录外部副作用审批。

## 13. 测试策略

### 单元测试

- Computer 状态机；
- Bot/Topic 归属；
- Workspace 继承优先级；
- Home/Project/Worktree 路径解析；
- A2A hop、幂等与 correlation；
- 模型与 Harness 继承；
- execution lease/fence；Phase 5 再测试 control lease/fence。

### 集成测试

- Bot 创建 -> Topic -> Local Host Runner；
- Routine/A2A 唤醒同一 Computer；
- server/runner 重启恢复；
- A2A durable outbox reconcile；
- Computer 切换与 checkpoint。

### Docker E2E

- provision -> runner connect -> harness turn；
- shell/file/git 操作；
- stop -> restore -> 文件存在；
- 两个 Dedicated Bot 相互隔离；
- 图形阶段增加 observe/click/takeover。

### Canary

- 定时创建测试 Bot；
- A2A 派发到第二 Bot；
- 在目标 Computer 写入文件；
- 返回结果并验证 correlation；
- 清理 Computer 与 Bot。

## 14. 指标与阶段闸门

核心指标：

- Bot 新 Topic 的 Home/Project checkout 命中率：100%；
- A2A 错投 Topic：0；
- A2A 重复执行率：0；
- 离线恢复后消息成功率：>= 99%；
- Computer resume 成功率：>= 99%；
- Dedicated 隔离逃逸：0；
- 用户设置模型与 effective model 不一致：0。

阶段闸门：

- Phase 2 未稳定前不做 Docker；
- Docker shell/files 未稳定前不做图形桌面；
- 内部 A2A 未稳定前不做标准 A2A 对外互联；
- 没有实际登录态/桌面需求时不引入云 Computer 成本。

## 15. 下一迭代建议

建议下一迭代只做 Phase 0 + Phase 1 的最小闭环：

1. 增加 Bot entity/store/migration；
2. 为 Debby、Polly 回填 Bot；
3. Session 增加 `bot_id`、`purpose`；
4. 建立 Session 侧 primary/A2A singleton-slot 唯一索引，Bot 不保存 Session 外键；
5. 增加最小 Local Host binding 和固定 Bot Home；
6. 彻底移除 localStorage 作为 Bot workspace 事实来源；
7. `GET /v1/teammates` 改为读取 Bot store；
8. 创建 Session 时强制写 Bot ownership，并从 Bot Home 解析基础路径；
9. 保留现有 Sidebar 分组，改用 `Session.bot_id` 作为权威归属并隐藏 A2A channel；
10. A2A 在现有 coordination message 上只补 source/target Bot 所有权与授权检查；
11. 增加迁移、路径零漂移、A2A 零串会话测试；
12. 派发回执展示 effective model/source。

本迭代不做 `BotProjectBinding`、并发 Worktree 自动分配、Team Computer、Control Lease、
Docker、VNC、云沙箱或标准 A2A，也不改变任何 Harness 的启动方式。

## 16. 最终验收场景

```text
1. 用户创建 Polly，系统分配固定 Bot Home，并选择 Dedicated Computer。
2. 用户将前端、后端两个 Project 绑定给 Polly；Topic 按 `project_id` 进入正确 checkout。
3. Polly 并发调用 Codex/Claude Code；每个写 Run 使用设置好的模型和独立 worktree。
4. Polly 通过 A2A 给 Debby 发问题；消息固定进入 Debby A2A channel。
5. Debby 离线时消息 pending；Host 恢复后自动运行。
6. Debby 的结果自动回到 Polly Inbox，不污染双方最近的用户 Topic。
7. 用户打开 Bot Computer 页面查看文件、运行状态和审计记录。
8. Docker provider 可用时，两个 Dedicated Bot 之间看不到彼此文件。
9. 图形 provider 可用时，用户可查看桌面并安全接管。
10. 更换机器或 provider 后，Bot 身份、Memory、Topics 和 Home 保持连续。
```

达到以上场景后，AgentNexus 才真正从“多 CLI Session 管理器”演进为“持久 AI
Teammate 平台”，同时保留其多 Harness 和跨厂商编排优势。
