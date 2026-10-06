# Cine 改编剧本链路修复

## 范围

按用户澄清，本轮修复程序交接，不重写《阴差打工手册》的情节、台词、大纲或角色。原画布三集剧本与总卡均与备份逐值一致。没有派发 Cine 改稿，没有生图或视频生成。

## 已修复的链路缺口

1. **版本选择不一致**：原先 check 优先 canonical 文件、忽略同目录命名版，seed 又只读取 canonical。现在统一使用 production.json 中 artifacts.<stage>.path；无清单时只允许唯一候选。缺失绑定、失效路径和歧义不再静默回退。
2. **重复起稿与绑定漂移**：已有 canonical、命名版或绑定输出均不能被 seed 覆盖。验证证明记录清单的哈希或原先不存在的状态，验证期间切换绑定、验证后新增清单都会使证明失效。
3. **原片故事草稿在交接中丢失**：原 source-material 只导出逐镜头侧车。现在同时携带当前 revision 的 adaptation_story 及来源文件哈希；旧 revision 被拒绝。导出仍标 exported_unverified，绝不冒充实际阅读。
4. **剧本阶段检查发生太晚**：handoff.py 新增 --stage outline/cast/art/script，只核验对应依赖闭包；剧本阶段不再要求预先制造分镜与映射。默认完整检查仍保留原行为。

没有新增五份原生创作 JSON 的 schema，也没有改作品来迎合校验器。

## 正确的使用顺序

原视频索引与实际阅读 → cine_verify_report(scope="adaptation") → 源材料与改编方案 → 大纲原生检查及阶段 pins 检查 → 剧本原生检查及阶段 pins 检查 → 后续分镜。

示例命令使用既有 skill 目录：

```text
python <film-analysis>/pipeline/production.py check <production-dir> --stage script
python <film-analysis>/pipeline/handoff.py <production-dir>/production.json --stage script
```

选择路径不等于校验 pins；native_validated 不等于 stage_inputs_validated，两者也都不证明故事质量或生成授权。改编幅度仍须来自用户决定，不能由工具自动选“抽核”。来源未读懂时，不得把旧改编文本写成原视频事实。

## 真实回归

- “阴差”原 outputs 目录同时存在 script.json 和 天道精算师-script.json，新 resolver 明确报歧义；两份原文件哈希未变。
- 本次原视频约 505.5 秒，预处理覆盖 17 个时间批次、提取 51 张证据图，但没有任何实际看图回执，verify_report 正确返回 needs_story_completion / 0 个已完成批次，而不是 ready_for_adaptation。
- 原画布三集与总卡内容、revision 未变。新建的空白 Topic 已改名为“阴差｜改编链路回归（仅原片证据）”，没有派发模型任务。
- Seedance 与 runner 联动回归 **99 passed**；目标 Python 文件 Ruff 检查通过。

原始复验证据位于 outputs/cine-adaptation-chain-regression/。测试只证明路径、版本和状态防错；不能宣称完整改编质量已经通过。

## 生效范围与未完成项

本轮代码和测试已完成，**尚未发布或重启线上服务**。发布需要同时更新 film-analysis skill 包中的 production.py/handoff.py/交接说明，以及服务端 production_gate.py，不能仅热换一边。

阶段 pins 检查提供可执行入口，但不是所有模型派发路径的强制编排器；原片语义阅读和改编方案的质量仍需独立验收。未替用户选择真实工程的绑定文件，也未补写虚假的阅读回执或审批状态。
