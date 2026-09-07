# Cine Workflow Optimization and A/B Report

Date: 2026-09-07

## Conclusion

工程交接能力已补齐一版，但 **A/B 没有证明整体创作质量优于旧版**。
保留技能候选及可执行检查器，不将本轮结果作为线上创作质量验收 PASS。
本次没有重启服务、重新注册运行中的 Cine、修改旧项目/画布或提交真实生成。

## Implemented

- 六个 skill 与 Cine 配置统一制作职责：Cine 制作，V3 平台承载画布、资产与 workers。
- 清除角色/美术/分镜中互相矛盾的 imagegen、CLI fallback、V3 Agent 制作要求，包括被引用的说明文件。
- V3 Agent 定位为 QA；现有通用接口不是强制只读，当前改为只读快照/本地 QA，不伪称隔离已实现。
- 增加共享 `production-handoff.md`，区分 analysis / faithful / adaptation / original；保留原片观察与原创改编的分离。
- 增加只读 source-material 转接：从当前 committed revision 与 review sidecar 搬运来源信息，不提升审阅状态。
- 增加独立 `production.json`：原生五份 JSON 无需改格式，使用文件哈希和直接上游 pins 发现失效下游。
- 增加逐切映射检查：源镜头、叙事功能、改编说明、实际剧本节拍、新分镜之间的引用与覆盖。
- 明确 H3 export 的提供方边界；12 秒长镜头使用既有时长参数，Seedance 草案不靠伪造 H3 字段通过检查。
- 第二轮收紧阻塞范围：来源不完整限制忠实性声明，不应阻断已授权原创草案；仍可交付非可执行的中立制作计划。

共享检查成功仅为 `handoff_validated`，不是视觉理解、五套原生 schema、上传资产、生成授权或成片验收通过。
它由 skill 指示调用，**尚未自动接入所有服务端派发路径**。不得把它宣传成后端不可绕过的安全门。

## A/B Method

使用三个相同输入、独立新上下文的子 agent，均继承本任务模型/推理配置，未指定不同模型。
没有独立审计提供方最终模型 ID；也不是在线 Cine/Gemini 会话的前后生产实测。

1. 30 秒科幻救援改编：部分源图未审阅，要求保留节奏，交付四个节拍和映射。
2. C02 合并到 C01 后，下游仍是昨日版本：单文件校验都通过，不能修改现有画布。
3. 独立短片的 12 秒不切镜头、无台词，目标 Seedance，本地参考图尚无远程 asset ID。

A 为完整旧技能快照；B1 为初版清理与交接契约；B2 根据首轮反馈补充局部阻塞和可用草案原则。
每个版本各生成一次三案答复。评审隐藏版本名称，第二轮交换 X/Y 顺序，由三个新上下文进行成对比较。
评审来自同一继承模型，不是跨厂商三模型互审，也不构成三个独立生产样本。

首轮三个评审均偏好 A。不过评审摘要把“无台词”误写成“静音”，且没交代现有 V3 接口的只读能力事实；
这些评审保留为开发反馈，不作为干净的最终实验。第二轮改用原始题面和共同接口事实，重新评审。
第二轮沿用 A，使用新的 B2。它属于开发集迭代对比，不是未见样本的泛化验证。

## Results

第二轮 X=A，Y=B2：

| 场景 | 评审1 | 评审2 | 评审3 | 主要观察 |
| --- | --- | --- | --- | --- |
| 改编与节奏 | A | A | A | A 不强行在 14 秒切镜；B2 将中段拆成两切，偏离保留节奏的优先级 |
| 旧版本交接 | 持平 | 持平 | 持平 | 两者都发现过期；B2 pins 更明确，但步骤顺序与不必要等待削弱了可用性 |
| Seedance 长镜头 | 持平 | B2 | B2 | B2 更清楚地区分无对白与静音，保留中立草案和提供方限制 |
| 总体 | A 略优 | 持平 | 持平 | 没有整体优胜证据 |

两版均未声称本地路径等于已看图片，也未伪造 asset ID 或生成完成。
B2 仍有两个具体执行风险：在文字清单中把交接检查排在写 pins 前，以及把只读画布检查推迟到额外许可之后。
正确操作应为：复核下游 -> 写实际 hashes/pins -> 执行交接检查；已有只读检查授权不应反复索要。
这些输出说明“规则写得更完整”不等于“模型一定执行得更好”。不提供虚构的质量提升百分比。

## Executed Checks

- `uv run pytest examples/cine/tests -q`: **102 passed**，包括新增交接测试 25 项；7 条现有 PySceneDetect 弃用警告。
- `uv run pytest tests/seedance tests/inner/test_image_tool.py tests/test_cine_bundle.py tests/runtime/test_prompt.py -q`: **52 passed**。
- 五套 `node .../scripts/selftest.mjs`: **1170 项断言通过**（249 + 355 + 158 + 154 + 254）。
- 新 Python 文件 Ruff 检查通过；独立代码审查没有发现结构检查范围内的可操作缺陷。
- 真实既有 0-30 秒样本转接：**11 个源镜头全部保持 unverified，观察与回执为空**；未修改样本或重新调用视觉模型。
- Codex 通用 skill 校验器仅 film-analysis 通过；五个 novel skill 保留的既有 `version`/`triggers` 字段不被该校验器接受。
  AgentNexus bundle 测试通过；未为满足另一套元数据规范而改动现有字段。

没有运行全仓所有测试、真实上传、生图、视频生成、Cine 在线 A2A 全链路或最终成片视觉质量评估。

## Reproduce

在仓库根目录运行交接回归：

```powershell
uv run pytest examples/cine/tests/test_handoff.py -q
```

只读导出当前来源材料（将输出保存到新的制作目录，不覆盖源账）：

```powershell
uv run python examples/cine/skills/film-analysis/pipeline/handoff.py --source-project <bound-project>
```

对按共享契约建立的制作目录运行：

```powershell
uv run python examples/cine/skills/film-analysis/pipeline/handoff.py <production.json>
```

人工验收：修改 outline 后仅更新它自身的 hash，下游 pins 保持原值，应收到 `UPSTREAM_STALE`。
不要为了通过检查自动刷新全部 pins；先做实际语义复核。

## Evidence and Release Boundary

- 完整提示词、评审输入及限制：`docs/evaluations/cine-workflow-20260907/protocol.json`。
- 原始答复：同目录 `output-a.md`、`output-b.md`、`output-b2.md`。
- 六份原始评审：同目录 `round1-judge-*.md`、`round2-judge-*.md`。
- 版本快照：`.codex-tmp/cine-workflow-ab/baseline/skills`、`optimized-skills`、`round2-skills`（本地未跟踪实验产物）。
- 测试题夹具：`examples/cine/tests/fixtures/workflow_ab_protocol.json`。

当前运行中的 Cine 未替换。仓库内配置与技能是这轮候选；下一次内置 Agent 重新注册可能载入它们。
工程检查可单独使用；上线前仍需修正上述执行反例，并用保留集及实际 Cine 模型复测。
V3 Agent 强制只读 QA、Seedance 专用导出、所有派发路径自动交接门仍未完成，不属于本轮“已完成”声明。
