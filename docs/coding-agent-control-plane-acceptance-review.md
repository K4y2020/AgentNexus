# 可视化 Coding Agent 协作控制台：验收审查报告

> 审查对象：`docs/coding-agent-control-plane-plan.md`
>
> 审查基线：`main` @ `15c523dd`（工作树干净，无未提交改动）
>
> 审查日期：2026-09-02
>
> 审查方法：不采信文档自述，逐条回源码取证；关键结论由审查者本人复核源码，非转述子代理判断；关键测试实际运行。

## 0. 修复记录（2026-09-02，紧随审查之后）

P1 四项已全部修复，工作树未提交（基线仍为 `15c523dd`）。修复均按「先证明缺陷、再改代码、再证明测试能捕获缺陷」的顺序执行。

| 项 | 修复内容 | 关键落点 |
| -- | -------- | -------- |
| P1-1 | 新增陈旧 lease 回收：区分**可证明未送达**（重投）与**不可证明**（置 `unknown`，永不静默重放） | `store.reclaim_stale_outbox_leases` / `resolve_stale_lease`；`dispatcher` 启动时 sweep + 周期回收；错误分类常量在 `types.py` |
| P1-2 | `delivery_state="unknown"` 由死代码变为真实写入点，兜底分支可触发 | `store._escalate_unknown` |
| P1-3 | 新增 `omnigent/coordination/probe.py`：三态探测协议 + runner 探测器；对账候选集纳入 `queued` | `probe.ProbeVerdict`；`store.list_effect_unknown_candidates`；`reconciliation.reason_for_unknown` |
| P1-4 | `GET /runs` 无过滤时逐条鉴权并过滤，单用户模式不受影响 | `routes/coordination._authorized_runs` |

**取证过程中的两处订正（与初版报告不同，以本表为准）：**

1. 悬挂态是 **`queued` + unconsumed**，不是初版报告写的 `active`。消息仅在投递成功后才转 `active`（全仓唯一调用点 `dispatcher.py:258`），因此崩溃在 POST 前的消息永远停在 `queued`——而原候选集只筛 `active`，**连候选集合都进不去**。这是比初版所述更靠前的一道失效防线。
2. 崩溃在 POST 前 vs POST 后从 DB 视角**不可区分**，故不能简单回退重投。实现改为：dispatcher 记录可机读错误类别（unreachable / rejected / unproven），恢复时仅在可证明未送达时重投，其余一律进 `unknown`——同时满足 §10.4「禁止盲目自动重放」。

**回收路径的三态判定（含一处语义订正）：** `resolve_stale_lease` 分 `requeued` / `unknown` / `abandoned` 三种结果。`abandoned` 指「已证明请求从未到达 runner，但重试预算耗尽」——此时**没有副作用可惧**，但阶段也永远不会被投递，仍需上报告警。初版实现把它的理由文本写成 "the effect of the last attempt is unproven"，与已证明的前提自相矛盾，会误导运维；已改为如实陈述「无副作用但需要重新派发」，并补了该分支的测试（此前无覆盖）。

**P1-4 修复的完备性已核验：** 审查原判断是「19 个写端点都做了鉴权，唯独这个读端点漏了」，修复后逐个复核了全部 11 个 GET 端点——其余 10 个（`/messages`、`/behavior/{session_id}`、`/runs/{run_id}`、`/runs/{run_id}/summary`、`/tasks`、`/workspaces/merge-previews[/{id}]`、`/events`、`/artifacts[/{id}]`）均把 `root_session_id` 设为**必填** Query 并调用 `_require_coordination_tree`。`GET /runs` 是唯一一个过滤参数可选、因而能跨 workspace 无鉴权的端点，不存在第二个同类漏口。

**回归测试：** 新增 `tests/test_coordination_outbox_recovery.py`（18 项）+ ACL 用例 3 项（其中 1 项为路由级）。已通过 `git stash` 回退证明新增用例在修复前**失败**（悬挂缺陷另以独立脚本复现：认领后重认领返回空、候选集查不到、无告警；P1-4 用「临时改回旧路由写法」证明路由级用例失败）。

**实测：** 单元 + server 路由 106 passed；集成（可靠性 RUNS=2 + A2A live）4 passed；最终 coordination 三文件 88 passed（`test_coordination.py` 61 + `test_coordination_outbox_recovery.py` 18 + `test_coordination_reconciliation.py` 9）。`ruff check` 全绿。

**一项未完成的验证（如实记录）：** 曾启动 `tests/server/` 全量回归（3752 项）以排查 dispatcher 常驻改动对无关 server 测试的连带影响，运行 16m48s 后由使用者指示终止，**未取得任何结果**——命令误用了 `| tail -40`，tail 缓冲全部输出直至管道关闭，进程被杀后日志为 0 字节。两个已知事实：收集阶段 12 个错误 12/12 均为 `ModuleNotFoundError: No module named 'asgiref'`（本地 venv 缺 host tunnel 可选依赖，与本次改动零交集）；终止后复跑上述三文件仍 88 全绿，证明中断无残留影响。

> 教训：长时程后台任务不要接 `| tail`，应重定向到日志文件以便增量观察进度与卡死。该全量回归若仍需执行，建议改用 `> log 2>&1` 并配合 `--timeout`。

### 100 样本可靠性基线：出现 1 次失败，未定性

按文档 Gate 要求跑满 100 样本（`scripts/run_control_plane_reliability.sh`，`--maxfail=1`），**在第 34 个样本失败后中止**：`AssertionError: workflow did not succeed before timeout; last=None`——即该 run 在 180s 内未达 `succeeded`，是真实的卡死，不是此前记录过的 `httpx.ConnectTimeout` 抖动。

随后做的取证：

| 实验 | 结果 |
| ---- | ---- |
| 复跑样本 34/35/36 | 3/3 通过，**该索引不可复现** |
| 修复后代码再跑 40 样本 | **40/40 通过**，17.8s/样本 |
| HEAD（回退修复）跑 35 样本 | 35/35 通过，18.5s/样本 |
| **3 路并发 × 20 样本（人为制造竞争）** | **60/60 通过**，22.3s/样本，全部 Exit 0 |

**已排除的回归机制（有硬数据）：** 最可疑的是「周期回收把慢投递误判为崩溃」。实测该投递路径的耗时上界是 relay 准备 5.0s（`_sessions/common.py:432`）+ 投递 POST 10.0s（`dispatcher.py:294`）= **约 15s**，而陈旧 lease 阈值是 120s（8 倍余量）。活跃投递在数学上不可能被误判。另一条对账路径的宽限是 `EFFECT_UNKNOWN_GRACE_S = 300s`，超出测试 180s 窗口，同样不可能触发。

另一个假设是「负载导致任务撞上 `task_deadline_s=120`、run 转入非 succeeded 终态」。为此并发跑了 3 组共 60 样本，把每样本耗时从 17.8s 压到 22.3s，**零失败**——该假设未获支持，但也未被完全排除（并发只造成 1.25 倍减速，未逼近 120s 量级）。

**已补的诊断能力：** 原失败信息只有 `last=None`，无法判断 run 停在什么状态。已修改 `tests/integration/test_control_plane_reliability_runs.py`，超时后再取一次 summary 并把终态拼进断言，形如：

```
workflow did not succeed before timeout; last=None; run_status='running'
stage={'planner': 'assigned', 'implementer': 'queued', ...}
consumption={'unconsumed': 1} effect_unknown_count=0
```

这能直接区分「真卡死（`running`）」与「撞 deadline（`failed`）」——正是上面两个假设的分水岭。该诊断路径已用「临时把超时改为 1s」强制触发验证过确实生效，随后恢复为 180s。

**结论（不作过度声明）：** 修复后代码累计 **137 过 1 失败（约 0.7%）**；HEAD 35 过 0 失败。统计上仍**不能判**该失败与修复无关——若真实故障率为 0.7%，HEAD 跑 35 样本全过的概率是 0.993³⁵ ≈ 78%，即 HEAD 的干净结果区分力很弱。可确定的是：不是确定性回归；修复后单样本耗时（17.8s）与 HEAD（18.5s）持平，无性能退化；且 60 个并发样本零失败。

### 补记：又跑了两次独占 100 样本，出现决定性分布（2026-09-02 下午）

本节上文只跑了 1 次 100 样本、且当时并发跑着 pre-commit（混淆变量）。随后补跑两次**独占** 100 样本，得到此前没有的分布：

| 运行 | 条件 | 结果 |
| ---- | ---- | ---- |
| 100 样本（并发 pre-commit） | 污染 | 第 34 个失败 |
| 100 样本 **独占** run 1 | 干净 | 第 67 个失败 |
| 100 样本 **独占** run 2 | 干净 | **第 53 个失败** |
| 35 / 40 / 60 样本各轮 | — | **合计 135 样本，0 失败** |

**短跑从不失败，100 样本跑两轮失败两轮**，且失败索引（34 / 67 / 53）不固定。这个分布强烈指向**随累积量增长的时序/负载型失效**，而非按索引触发的确定性回归——否则索引应当可复现。

~~**支持「累积」的三条独立观测**~~（**此假设已证伪，保留原文以存证，勿采信**）：单样本耗时在 run 1 内从 23.0s 升到 28.6s；`live_server` 是 `scope="session"`（`tests/e2e/conftest.py:570-571`），100 样本共用同一 server 进程与数据库；`reconcile_missing_dispatches` 调 `store.list_runs(None)`（`workflow_engine.py:1227`）无过滤枚举全部 run。

**证伪依据：** 下文 run C 的失败发生在 **index 10**（开跑约 3.5 分钟、仅第 11 个样本），此时累积量微不足道。累积假说不成立。短跑全过应归因于统计——按观测故障率约 1.3%，135 个样本连续全过的概率约 17%，并非小到不可能。

**本次修复新增的周期性工作已核验为有界，不是劣化源：** `list_stale_outbox_leases` 同时受 `status=="leased"`（瞬态集合，稳态近 0）、`updated_at <= cutoff`（120s）与 `.limit(100)` 三重约束；`_heal_run_dispatch` 新增的 outbox 批量查询被 `if open_messages:` 挡在前面，已完成的 run 不会触发；`update_run_metadata` 仅在 `cooldown_changed` 为真时写一次。

**两次失败详情均被吞掉，原因已查明并已规避：** `scripts/run_control_plane_reliability.sh` 硬编码 `--junitxml`，而 junitxml 在 `pytest_sessionfinish` 阶段写文件失败（第一次是我传了 git-bash 风格 `/u/...` 路径，Python 的 `os.makedirs` 解析成 `U:\u` 遭 `WinError 5`；第二次是沙箱拒绝写 `.reliability-results/run100-b/reliability.xml`）。`--maxfail=1` 下测试一失败就进 sessionfinish，于是**真正的断言信息被这次崩溃盖掉了**，日志里只剩一个 `F`。规避办法：绕开脚本直接调 pytest、不带 `--junitxml`。

**建议：** 交给 CI 每周 100 样本基线仲裁。有了上述诊断，下次复现时能直接读到终态，不必再靠猜。

### 卡死已定性：不是死锁，也不是本次修复引入的（最终结论）

绕开脚本、不带 `--junitxml` 直接调 pytest 后，run C 在 **index 10** 失败并完整打印了终态，此前悬置的问题就此闭合：

```
workflow did not succeed before timeout; last=None; run_status='running'
stage={'planner': 'succeeded', 'implementer': 'running',
       'reviewer': 'queued', 'fixer': 'queued', 'tester': 'queued'}
consumption={'consumed': 3} effect_unknown_count=0
```

**由此硬排除三项：**

| 假设 | 判定 | 依据 |
| ---- | ---- | ---- |
| 撞 `task_deadline_s=120` 转入终态 | **证伪** | `run_status='running'`，不是 `failed`/`needs_attention` |
| outbox 悬挂（P1-1） | **证伪** | `consumption={'consumed': 3}`，无 unconsumed 悬挂消息；且 `planner='succeeded'` 证明派发链路是通的 |
| `unknown` 上报路径（P1-2） | **证伪** | `effect_unknown_count=0`，该代码路径从未触发 |

与「临时把超时改成 1s」**强制**卡死那次的签名对照，二者形态完全不同，可确证不是同一故障：

| | 强制卡死（1s 超时） | 真实失败（180s 超时） |
| -- | -- | -- |
| planner | `assigned` | **`succeeded`** |
| implementer | `queued` | **`running`** |
| consumption | `{'unconsumed': 1}` | **`{'consumed': 3}`** |

**定性结论：** run 处于**活锁式推进中**——阶段在往前走、消息在正常消费，只是没在测试 180s 预算内跑完。这是**超时预算余量**问题，不是正确性缺陷，也与本次四项 P1 修复无因果关系（P1-3 的探测器只在陈旧 lease / unknown 候选上触发，两者都不存在；P1-4 是读路径鉴权，与推进无关）。

### run D（补跑，收集全部失败）：3 failed / 97 passed，签名与 run C 完全一致

去掉 `--maxfail=1` 后补跑 100 样本（52m20s），失败 3 个：**index 42、65、91**。三者终态签名逐字符一致，且与 run C 的 index 10 失败一致：

```
run_status='running'
stage={'planner': 'succeeded', 'implementer': 'running',
       'reviewer': 'queued', 'fixer': 'queued', 'tester': 'queued'}
consumption={'consumed': 3} effect_unknown_count=0
```

**独占运行累计：6 次失败 / 约 230 样本 ≈ 2.6%，六次签名全部相同**（implementer 停在 `running`）。失败索引 10/42/53/65/67/91 无位置规律，排除确定性触发。

**机制收窄（新证据）：** 该测试用 **mock LLM**（`configure_mock_llm`，脚本化即时响应）——生成不耗时。implementer 卡 `running` 说明卡点在**派发→runner→terminal-idle 回执链路丢失终态回执**；叠加 `task_deadline_s=120` 无后台收割器（见上节 P2），任务卡死后 deadline 永不评估，一路挂到 180s 测试超时。

**下一步仲裁（已执行）：HEAD 对照臂跑满 100 样本 —— 结论：该 stall 为既有缺陷，与本次修复无关。**

### HEAD 对照臂结果：1 failed / 99 passed（52m56s，失败在样本 53）

HEAD 上同样出现同级别失败（1/100 ≈ 1% vs 修复后 6/230 ≈ 2.6%，Fisher 精确检验 p≈0.43，**两组无统计显著差异**）。且 HEAD 失败签名与修复后属**同一故障类**——同样是某阶段任务卡 `running`、消息全数消费、`effect_unknown_count=0`，只是卡住的阶段不同：

```
HEAD（index 53）：tester='running'，planner/implementer/reviewer/fixer 均 'succeeded'，
                  consumption={'consumed': 6}   effect_unknown_count=0
修复后（6 次）：   implementer='running'，planner='succeeded'，
                  consumption={'consumed': 3}   effect_unknown_count=0
```

卡在哪一阶段是随机的——共同机制是：**派发到 runner 的任务丢失 terminal-idle 回执后永久滞留 `running`，而 `task_deadline_s` 无后台收割器，deadline 永不评估**。

**最终定性（证据闭环）：**

1. HEAD 与修复后故障率无显著差异（1/100 vs 6/230，p≈0.43）；
2. 两者失败签名同属一类（stuck-running，非派发失败、非 unknown 误报）；
3. mock LLM 即时响应，排除生成耗时；
4. 修复代码的触发条件（陈旧 lease / unknown 候选）在这些失败中均不存在。

因此该 stall **不是本次四项修复引入的回归**，而是 HEAD 既有的缺陷；修复后的诊断插桩（超时打印终态签名）正是定位它的关键工具。**后台 deadline 收割器（P2）是正确的兜底方案**：它会把这类静默挂起在 120s 时转成显式的任务失败/重派，而不是无限期滞留。

### `tests/server/` 全量回归已完成（此前被终止的那项，已补跑）

**结果：3724 passed，23 failed，1 skipped，3 xfailed，13 errors，耗时 43m43s。**

（这次改用 `> log 2>&1` 重定向，可全程观察进度；上一版误用 `| tail -40` 导致零产出。）

**关键结论：23 个失败全部为既有失败，与本次改动无关——已用 A/B 实验逐组证实。** 做法是把**只含 coordination 的改动** `git stash push` 回退（其余工作树不动），跑同一组用例再对比：

| 分组 | 数量 | A/B 验证 | 失败原因 |
| ---- | ---- | -------- | -------- |
| `test_app.py` ×2 + `test_hosts_routes.py` ×2 | 4 | **回退后同样 4 个失败** | 静态文件挂载 / host 路由未挂载，与 coordination 无交集 |
| `test_sessions_sharing_mode.py` | 9 | **回退后 19 个失败，数量一致** | POSIX 路径（`/` `/root` `/home/alice`）阻断，Windows 不适用 |
| `test_smart_routing.py` | 8 | 同上 | `ModuleNotFoundError: No module named 'databricks'`，可选依赖缺失 |
| `test_openapi_drift.py` | 1 | 同上 | `openapi.json` 陈旧：`+25 paths added, ~3 changed`，**drift diff 中 coordination 相关行数 = 0** |
| `test_admin_list.py` | 1 | 同上 | `os.geteuid` 在 Windows 上不存在 |

**13 个 error 的构成：** 12 个是 `No module named 'asgiref'`（host tunnel 系列，可选依赖缺失）；第 13 个是 `test_external_runner_connects_to_local_server` 的 setup 错误，栈在 `httpcore/_sync/http_proxy.py`——沙箱 HTTP 代理拦截本地连接，**失败发生在传输层、尚未进入任何应用代码**。该项未做 A/B（其失败点在应用层之下，A/B 无意义）。

**A/B 后工作树已确认无损恢复：** `545 insertions / 18 deletions` 与操作前完全一致，关键符号（`_authorized_runs`、`OUTBOX_LEASE_TIMEOUT_S`）在位，`test_coordination_outbox_recovery.py` 18/18 通过。

### 由此暴露的一项 P2（新发现 → **已修复**）

`task_deadline_s` 只被**写入**任务（`workflow_engine.py:152-207`、`:308-329`），其强制函数 `_enforce_task_deadline`（`:837`）**仅在派发/推进的转接点被调用**（`:435-436`、`:1049-1050`、`:1117-1118`），**没有任何后台收割器**。后果：一个任务在 `running` 状态中超时，只要后续没有新的推进动作触发检查，它的 deadline 就永远不会被评估，run 会长期滞留在 `running`。

这正是本次失败能持续到 180s 而未自愈的机制。另需注意 run 级硬 deadline 读的是 `run.budget.get("deadline_s")`（`:789`），而测试传的是 `task_deadline_s`，二者不是同一个键——测试本就没有设置 run 级 deadline。

该项属既有设计缺口，非本次修复引入。**收割器已补**（见下）。

> 注：`tests/test_coordination.py` 在受管沙箱内运行会触发 `sitecustomize.py` 的批量删除守卫（`SystemExit: 1`），与改动无关——用 HEAD 版测试文件复现同样失败。绕法：对该子进程解除 `CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR`。

### P2 修复：后台 deadline 收割器（已落地）

三处改动，语义为 fail-closed——把静默挂起在 deadline 到期后转成显式的 `blocked` / `needs_attention` + 事件，而非重派（重派属于 stage 语义，不在本层做）：

1. **`store.list_expired_active_tasks(now, limit=50)`**（`store.py`）——有界查询：active 状态集（queued/assigned/running/waiting_*/reconciling）+ `deadline <= now`，按 deadline 升序，上限 50。健康系统里结果恒为空，扫描代价不随任务表增长。
2. **`engine.harvest_expired_task_deadlines_once()`**（`workflow_engine.py`）——逐个 task→`blocked`（CAS from active 集，冲突抑制）→ run→`needs_attention` → 记 `workflow.task.deadline_exceeded` 事件（payload 带 `harvester: true`）。幂等：已收割的任务离开 active 集，第二次调用 `harvested=0`。返回 `{"expired", "harvested"}` 计数。
3. **`CoordinationWorkflowScheduler`**（`workflow_scheduler.py`）——`_poll_loop` 每 30 圈（interval 1s → 约 30s 一轮）调用一次收割，收割到任务时打 warning 日志；异常只记日志不中断循环。scheduler 已在 `app.py:1371-1375` lifespan 挂载，真实运行中生效。

**测试**（`tests/test_coordination.py`，2 个新增）：

- `test_deadline_harvester_reaps_stuck_task`——用 `task_deadline_s: -1` 造出过期任务且不调用 `advance`（复现"无转接点触发"的场景），收割后任务 `blocked`、run `needs_attention`、事件带 `harvester` 标记，二次调用 `harvested=0`（幂等）。
- `test_deadline_harvester_ignores_live_tasks`——未来 deadline 的任务与 run 不被触碰。

coordination 三文件 **90 passed**（88 + 2），`ruff check` 全绿。

## 1. 总体结论

文档第 15 节那段长「已落地」引文**大部分属实**，但有 4 项声称与代码不符或被代码证伪，其中 1 项构成阻断级缺陷。

| 判定 | 数量 | 说明 |
| ---- | ---- | ---- |
| 已落地且属实 | 19 | 源码可验证，部分含措辞/口径偏差 |
| P1 阻断 | 4 | 违反文档自身的设计要求，必须修复后才能过 Gate C |
| P2 缺陷 | 12 | 局部不符、空心能力或文档超前于代码 |
| 文档自认未完成 | 7 | 属实，无需争议 |

**Gate 判定：**

- **Gate C（P2 结束）— 不通过。** 该 Gate 明文要求「必须证明消息持久化、重启恢复、控制面幂等和 `effect_unknown` 对账」。四项中「重启恢复」与「`effect_unknown` 对账」存在实测缺口（见 P1-1、P1-2、P1-3）。
- **Gate B（P1 结束）— 条件通过。** 可观测性主体落地，剩模型事实链与 Behavior 回执缺口。
- **Gate D（P3/90 天）— 样本就绪，未执行。** `demos/agentnexus-acceptance/` 已可用，但 3–5 名真实用户验证尚未做。
- **Gate E / P6 — 远未达成。** 文档自认的环境门禁（真实付费 API、签名证书、24h soak、100 样本）全部未执行。

## 2. P1 阻断项

### P1-1　Outbox 行卡在 `leased` 导致消息永久悬挂，且四重防线全部绕过

这是本次审查发现的最严重缺陷。它同时击穿了文档 §9.4、§10.4 和 §21.3 三处设计要求。

**触发路径：** Dispatcher 认领 outbox 行时写入 `leased`（`omnigent/coordination/store.py:829`），此后若在 `record_delivery_attempt` 或 `requeue_outbox` 完成前崩溃，该行以 `leased` 落盘。

**重启后四道防线逐一失效：**

1. **认领不到** —— `claim_pending_outbox` 的过滤条件是 `SqlCoordinationOutbox.status == "pending"`（`store.py:820`），`leased` 行永不再次被认领。
2. **无启动回收** —— `requeue_outbox` 全仓仅两处调用（`dispatcher.py:116`、`dispatcher.py:243`），均在投递失败路径；没有任何启动 sweep 把陈旧 `leased` 回退为 `pending`。
3. **补齐逻辑跳过** —— `CoordinationWorkflowScheduler` 确实在 lifespan 启动并每 1 秒跑 `reconcile_missing_dispatches`（`workflow_scheduler.py:55-63`），但 `_heal_run_dispatch` 的跳过条件是「存在 `unconsumed` 且 `queued/active` 的消息就 continue」（`workflow_engine.py:1228-1232`）。悬挂消息恰好是 `active + unconsumed`，因此被判定为「还在投递中」而跳过。
4. **对账不报警** —— `reason_for_unknown` 首行即 `if not attempts: return None`（`reconciliation.py:75`）。崩溃发生在写入 delivery attempt 之前，`attempts` 为空，因此既不打 `effect_unknown_reason`，也不发 `effect.unknown_detected` 事件。

**后果：** 消息永久停留在 `active + unconsumed`，Task 停在 `assigned`/`running`，Run 停在 `running`，**且 UI 上不产生任何告警**。这不是数据丢失，是静默死等——在用户体验上更糟。

**违反的文档条款：**

- §9.4「若 Adapter 在注入后、receipt 落库前崩溃，进入 `delivery_state=unknown`」
- §10.4「每次模型 Turn、工具和投递都写 ExecutionAttempt/checkpoint；进程内 Future 不是恢复依据」
- §21.3「持久消息丢失率 0」「`effect_unknown` 检出覆盖率 100%」

**修复建议（任选其一，建议 1+2 同时做）：**

1. 启动时执行 sweep：把超过 lease 超时阈值的 `leased` 行回退为 `pending` 并递增 `retry_count`，纳入现有退避。
2. `_heal_run_dispatch` 增加「outbox 行既非 `pending` 也无在途 attempt」的判定分支，而非只看消息状态。
3. `reason_for_unknown` 对 `attempts` 为空但消息已超时未消费的情形，同样标记 `effect_unknown`（理由可写 `delivery_never_attempted`）。

**验收回归：** 在 §18.3 故障注入清单中补一条「Server 在 outbox 认领后、投递前退出」，断言重启后消息最终进入 delivered 或 `effect_unknown`，不得永久停留在 `active + unconsumed`。

### P1-2　`delivery_state=unknown` 是死代码，兜底分支永不触发

`unknown` 在 `types.py:14` 定义、`reconciliation.py:86` 被读取，但**全仓无任何写入点**。实际赋值只有 `failed` 与 `confirmed` 两种（`dispatcher.py:109/214/236/253`、`store.py:593`）。

这意味着 P1-1 的兜底设计在代码层面根本不存在：即便正确识别了「注入后、receipt 落库前崩溃」，也没有路径把状态写成 `unknown`，后续对账无从触发。此项与 P1-1 需一并修复。

### P1-3　`effect_unknown` 对账无真实状态探测，仅超时打标

`omnigent/coordination/reconciliation.py` 全文只 import 了 `logging`、`time`、`dataclasses`、`typing`（`:14-25`），**不含 `subprocess`、git 库或任何进程查询**。它不查 Git HEAD/dirty、不查目标进程存活、不核对 Vendor transcript。

文档 §10.4 的要求是「已发出但未拿到结果的 Shell、Git、Merge、外部 API 标记为 `effect_unknown`，**先探测真实状态**，禁止盲目自动重放」。当前实现只满足「打标」，不满足「探测」。

**判定：** `effect_unknown` 检出覆盖率不是 100%，而是仅覆盖「runner 已确认注入但长期无消费回执」这一子集。崩溃在注入前发生的场景（P1-1）完全不覆盖。

### P1-4　`GET /v1/coordination/runs` 漏 ACL，可跨用户枚举

```python
# omnigent/server/routes/coordination.py:835-836
if root_session_id:
    await _require_coordination_tree(request, root_session_id)
```

不传 `root_session_id` 时**零校验**，而 `store.list_runs(None)`（`store.py:308-316`）返回当前 workspace 的全部 run。多用户模式下任意已登录用户可枚举他人 Run。

文档「已落地」引文声称「Coordination API 现已接入同一 Session ACL：多用户模式下，GET 要求 read」。实际情况是：19 个写端点均正确调用 `_require_coordination_acl`（`:275-324`，按 method 定级，`:255-257`），**唯独这个读端点漏了**。属于典型的"写接口严、读接口松"。

**修复：** 无条件调用 `await _require_coordination_acl(request, ...)` 后再按 `root_session_id` 过滤；无 root 时应对返回结果逐条做会话树校验。

## 3. P2 缺陷清单

| # | 位置 | 问题 | 违反条款 |
| - | ---- | ---- | -------- |
| 5 | `coordination.py:975-987` | `POST /workflows/template` 的 DAG 校验（975-986）在 ACL（987）之前，未授权者可探测图结构 | §14.1 |
| 6 | `coordination.py:893-912` | `POST /tasks` 不校验 `assignee_session_id` 的会话树归属 | 引文「recipient 先经会话树校验」 |
| 7 | `coordination.py:485-507` vs `workflow_engine.py:225-323` | 环检测只存在于路由层，引擎层 `start_dag_workflow_run` 无环检测，绕过 HTTP 直调可写入环图 | §10.2 |
| 8 | `crash_reporter.js:273` / `:258-272` | 声称覆盖 unresponsive/responsive；实际 `responsive` 为空实现，`unresponsive` 只写一行 txt 不产 bundle，测试无对应用例 | P6 崩溃诊断 |
| 9 | `desktop-release.yml:60` vs `:38-40` | 签名判定读 `$WIN_CSC_LINK`，但 env 只导出 `CSC_LINK`，`signed` 标识恒为 `unsigned`（签名本身不受影响） | P6 发布流水線 |
| 10 | `demos/agentnexus-acceptance/workflow.plan-implement-review.json:3,8,20,34` | 含 4 处 `REPLACE_WITH_*` 占位符，不能「直接 POST」 | 引文「可直接 POST」 |
| 11 | `control-plane-reliability.yml:11-12,49` | cron 有每日 `15 2 * * *`（非仅每周）；定时触发时 `inputs.runs` 为空取默认 100，非「低成本」 | 引文「每周自动保持 Ubuntu 低成本」 |
| 12 | `store.py:876-896`、`dispatcher.py:58-70` | 无 dead-letter / needs-attention 队列；空闲仍 0.5s 空转热轮询，无积压感知退避 | §10.4 背压 |
| 13 | 全仓 | 无 `AgentInstance` 类、无 `last_consumed_target_sequence`；`target_sequence` 仅存在于 outbox/delivery_attempt，无消费游标持久化，无 Vendor turn/session ID，无暖恢复 transcript 核对 | §9.5、§10.4 |
| 14 | §15 表格 | `WS /v1/coordination/updates` 在文档中列出，代码中不存在 websocket 路由 | §15 |
| 15 | `behavior.py:83-92,109-115` | `lean-engineering` 硬编码为 Python 常量，无独立包清单、版本文件或许可证文件 | §11.4、§20.3 |
| 16 | 根 `README.md` | 未链接 migration guide / privacy / support matrix；仅 `web/electron/README.md:431-436` 链接了另一套同名文档 | 引文「README 已链接」 |

## 4. 已落地且属实（可计分）

以下经源码核实，与文档声称一致：

**协调域（§15、§10）**

- `omnigent/coordination/` 共 9 个模块：`store.py` 1169 行、`workflow_engine.py` 1479 行、`workflow_auto_advance.py` 181、`dispatcher.py` 280、`behavior.py` 303、`policy_gate.py` 157、`limits.py` 104、`reconciliation.py` 143、`workflow_scheduler.py` 65。
- 路由齐全：`/runs`（801/817/829/845）、pause/resume/cancel（1116/1129/1142）、`/workflows/plan-implement-review`（933）、`/tasks/{id}/advance`（1017）/report（1045）、`/tasks/{id}/retry`（1155）/reassign（1174）、`/messages`（550/630/720/753）、`/artifacts` 四件套（1405/1440/1459/1475）、`/events`（1389）、`/workspaces/lease`（1206）、merge-previews（1245/1331）。
- 通用模板 DAG 的分叉/汇合真实可用：`_dispatch_ready_tasks`（`workflow_engine.py:708-754`）以 `all(dep in successful for dep in task.dependencies)` 判定出队；无依赖节点在启动时直接置 `assigned` 并立即派发（`:296`、`:307`）；失败节点置 `needs_attention` 后下游不出队（`:637-651`）。
- `reassign_task` 三分支与文档完全一致：已确认 → 抛错要求先 cancel/retry（`:1104-1107`）；queued → 重定向收件人（`:1119-1140`）；active+unconsumed → 记 rejected 回执并重发（`:1141-1175`）。
- 幂等成立：已在目标态返回 False（`store.py:287-288`、`384-385`），`advance` 捕获 `StateTransitionConflict` 按幂等处理（`workflow_engine.py:359-368`），`_dispatch_ready_tasks` 未抢到则 continue 不重复派发（`:725-730`）。
- 迁移 `za3b2c4d5e6f`、`za4b2c4d5e6f` 均存在，upgrade 先 inspect 判存在（幂等），downgrade 可 drop_column 回滚。

**口径校正（非缺陷，但文档措辞不准）：** 文档称「状态迁移全部使用 SQL CAS」，实际是 `_session_immediate`（`store.py:227` → `db/utils.py:722` 的 SQLite `BEGIN IMMEDIATE`）下的 Python 读-判-写，靠写事务串行化达成等价语义，**不存在 `UPDATE ... WHERE status = expected` 语句级 CAS**。语义等价，措辞需订正。

**Behavior Pack（§11.4、BEHAVIOR-001/002/003）**

- `behavior.py` 是纯领域模块（仅 import hashlib/json/dataclasses/typing，`:9-14`）；digest 由 `_pack_digest` 规范化计算（`:63-70`）；优先级 `role→workflow→user`（`:207-219`）；strict 授权门（`:221-227`）；安全边界 `_SAFETY_BOUNDARY` 恒随注入（`:33-37`、`:238-249`）；`compose_injection_prompt` 无副作用。
- `GET /v1/coordination/behavior/{session_id}`（`:646-717`）真实读取 `store.list_delivery_attempts`（`:698-704`），非硬编码。
- `workflow_behavior_payload`（`behavior.py:267-294`）覆盖 kickoff（`:193`）、派发（`:1450`）、retry（`:1055→1273/1342`）、reassign（`:1170-1175`）——**四条路径都接了**，文档声称属实。
- 指令前置 prompt 真实存在：`payload["prompt"] = f"{block}{prompt}"`（`workflow_engine.py:702-706`）。
- `behavior_modes`（`coordination.py:439-441`）与 `tasks[].behavior_mode`（`:467`）均被接受并持久化。

**effect_unknown（P1-3 之外的部分）**

- 扫描循环存在：`dispatcher.py:58-70`，`reconcile_every_loops = 60`（`:37`），在 lifespan 启动（`app.py:1364-1368`）。
- 判定与 CAS 属实：`reconciliation.py:73-91` 只取 `attempts[-1]`，非 confirmed/unknown 一律跳过；宽限 `EFFECT_UNKNOWN_GRACE_S = 300.0`（`:34`）；CAS 幂等（`store.py:711-717`）。
- 事件与计数属实：`effect.unknown_detected`（`reconciliation.py:37`、`:115-129`），Run summary `effect_unknown_count`（`coordination.py:865-884`）。

**口径校正：** 「每约 30 秒」是按轮询次数换算（60 × 0.5s），**不是时间常量**；有积压时间隔 = 60 × (0.5s + 投递耗时)，实测会显著超过 30s。

**Windows 发布与桌面端（§15、P5、P6）**

- NSIS 配置属实：`oneClick:false`、`perMachine:false`、`allowToChangeInstallationDirectory:true`、`deleteAppDataOnUninstall:false`（`web/electron/package.json:168-179`）；win target 同时产出 `nsis` + `zip`（`:154-160`）；双协议注册（`:47-55` + `main.js:3206-3207`）。
- 升级备份属实：`update_backup.js:20-28`（chat.db/-shm/-wal、config.yaml、auth_tokens.json）、`:56-65`（settings.json）、`:82-91`（daemons）；保留 5 份（`:18`、`:102-123`）；失败即不安装（`desktop_updater.js:278-295`）；`restoreFromBackup` 存在（`:186-213`）。
- 卸载询问属实：`build/installer.nsh:8-21` 以 `${ifNot} ${Silent}` 包裹，静默/更新路径永不清理。
- `upgrade_guard.js` 四项行为全部属实：记录（`:91-116`）、新版本清标记（`:156-159`）、旧版本恢复（`:160-165`）、恢复失败保留标记（`:165-171`）；且在 `main.js:3164` 早于 server 启动调用。
- 品牌迁移属实：`ai.agentnexus.desktop`（`package.json:42`）、productName（`:3/:43`）；兼容项 `omnigent` scheme、`window.omnigentDesktop` IPC、`~/.omnigent` 均保留。

**验收样本与可靠性基线（Gate D、P6）**

- `demos/agentnexus-acceptance/tasks.json` 确为 20 个任务（T01–T20），四项字段齐全（`:7-247`）。
- `sample_repo` 仅依赖 `decimal` + `unittest`，9 个测试方法，无第三方依赖。
- `verify_baseline.py` 实跑通过：`tests_run: 9, success: true`，exit 0。
- 可靠性脚本 sh/ps1 语义等价，均支持 `RUNS`/`AGENTNEXUS_RELIABILITY_RUNS`/`RELIABILITY_ARTIFACTS`，默认 100，输出 JUnit。
- `control-plane-reliability.yml` 的 `include_windows` + Windows runner + JUnit 上传属实（`:19-23`、`:92-97`、`:127-135`）。
- 进程清理属实：`process_manager.py:1357-1368`（terminate_tree → wait → kill_tree）、`process_reaper.py:81-88`（Windows 路径规范化边界匹配）、`:103`（识别 `pytest.exe`）、`tests/e2e/test_pytest_process_leak_e2e.py:86-127`（Windows 真实 server 子进程探针）。

## 5. 实测结果

以下测试由本次审查实际运行（`python -m pytest`，环境 Windows / Python 3.10），非引用历史记录：

| 测试文件 | 结果 | 耗时 |
| -------- | ---- | ---- |
| `tests/testing/test_process_reaper.py` | 10 passed | 19.54s |
| `tests/integration/test_control_plane_reliability_runs.py`（RUNS=2） | 2 passed | 46.45s |
| `tests/integration/test_control_plane_a2a_bus_live.py` | 3 passed | 60.32s |
| `tests/e2e/test_pytest_process_leak_e2e.py` | 1 passed | 105.36s |

A2A 三项断言内容与文档声称一致：消息到达 harness 并回执 `consumed`（`:264`）、Plan→Implement→Review 四阶段自动推进且 consumed≥4（`:322`）、Claude 走 `/v1/messages` 且 Codex 走 `/v1/responses` 的跨族 E2E（`:391`）。

**一处需关注的历史痕迹：** `.reliability-results/reliability.xml`（2026-09-02T00:18:43）记录 `errors=1`，失败原因为 setup 阶段 `httpx.ConnectTimeout`。本次实跑未复现，但说明该基线并非稳定全绿，100 样本批量运行时需预期偶发 setup 抖动。

## 6. 文档自认未完成项（核实属实）

以下确为代码所无，非隐瞒：真实付费 API E2E、通用 DAG 的付费环境验证、Windows 真实签名证书、干净机 15 分钟首次协作验收、候选发布 24 小时 soak、100 次真实环境可靠性 Run、macOS 正式签名包（`desktop-cross-platform-smoke.yml:41-48` 已产出构建配置，但 macOS 工件按 `docs/verification-status.md:163` 尚未产出）、BEHAVIOR-003 的两项剩余（会话级注入持久回执、多环境门禁）。

BEHAVIOR-003 剩余项实测确认：全仓无 behavior 注入回执写入，端点对 session_mode 硬编码 `reason="session_mode_not_yet_injected"`、`delivery_state="none"`（`coordination.py:685-687`），永不为 confirmed；`tests/test_behavior.py` 仅 9 个纯单测，无并发模式隔离与 E2E。

## 7. 建议的下一步

按阻塞关系排序：

1. **修 P1-1 + P1-2（同一改动）**：补 leased 回收 + 让 `unknown` 状态真正可写 + `attempts` 为空时的超时兜底。这是唯一能同时关闭三个 P1 的改动点。
2. **补回归测试**：在 §18.3 增加「outbox 认领后崩溃」注入用例，并把它纳入可靠性 Run 的断言（当前断言只有 `effect_unknown_count == 0`，无法捕获悬挂）。
3. **修 P1-4 ACL**：`GET /runs` 无条件鉴权。
4. **再跑 Gate C**：在上述修复后，以 `RUNS=100` 跑可靠性基线，确认无悬挂、无 `errors`。
5. **P2 批量清理**：#5、#6、#7 属同一 ACL/校验前置问题，可一并处理；#9 是环境变量名笔误，一行修复。
6. **订正文档措辞**：SQL CAS → 事务串行化；「每约 30 秒」→ 按轮询次数换算；补记 `WS /updates` 未实现。

在 P1-1 修复前，不建议进入 P4 之后的任何工作——文档 §25 Gate C 的规则正是为此而设：「必须证明消息持久化、重启恢复、控制面幂等和 `effect_unknown` 对账，否则不能进入 Git 自动协调」。当前状态不符合该条件。
