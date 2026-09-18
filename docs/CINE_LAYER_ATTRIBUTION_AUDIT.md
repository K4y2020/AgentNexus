# Cine 分层与测试归因更正

## 更正结论

撤回“删邻居证明剧本编排被反向提示词污染”以及据此认定“模型未稳定遵守剧本规则”的结论。现有记录只能证明模型在一个混合输入的审查任务里建议删邻居，没有证明剧本生成链路自动注入了这条限制，也没有发生项目角色的自动删除。

## 可核实事实

1. `outputs/cine-publish-20260910/review-request.txt` 由测试控制器拼成，开头称其为动作喜剧提纲，末尾又加入“无额外人物、不可能物理”，没有区分生成约束与故事要求。
2. 同目录 `writing-request.txt` 是独立的剧本/分镜创作任务，不包含“无额外人物”。其中“只出现一个画内人物”是该故事明确要求，不是从生成模板注入。
3. 发布备份 `previous-bundle.tar.gz` 的 config、novel-script 和 novel-storyboard 中均无“无额外人物”。v25 的 config 与 novel-storyboard 各出现一次，来自本轮新增的审查说明，不是正在执行的禁人模板。
4. 两份 skill 核心被整体预载进 Cine config。因此新增的下游审查说明也可出现在写剧本的上下文中；这是需要讨论的职责范围问题，但还不是它造成具体错误的证据。

## 实际链路核查范围

- `novel-art` / `novel-characters` 确实包含 `image.negativePrompt` 字段及图片预设，例如空景禁人。它们服务于资产图片，不意味着剧情中不能有人。
- `novel-script/references/schema.md` 的剧本结构是场次、动作与对白；`novel-storyboard/scripts/novel-storyboard.mjs` 的 `expandScript` 读取角色、道具、flow 和估时，不读取 image.negativePrompt。
- `agentnexus/seedance/storyboard_import.py` 把已有 script/document 交给 V3 importer，不把生成负面约束逆写为剧本人物限制。
- V3 provider 层存在 negativePrompt/negative_prompt 的传递，属于生成端。此次检查未发现它自动回灌上述剧本路径。

以上是已检查路径的结论，不是对所有 harness、历史会话或任意模型行为的全局保证。模型读取完整资产文件时仍可能误解字段，需要正确分层的实测，不能靠搜索无命中证明绝无污染。

## 已发布改动的依据

| 改动 | 当前判断 |
| --- | --- |
| 自然节拍、估时不当动作排程 | 来自用户明确需求；不因本次归因错误回滚 |
| H3 引用和切点语法 | 有原生契约依据；成片收益仍受样本限制 |
| 道具交接、身份与状态区分、单镜负载 | 通用审查原则且有独立例子支撑；不等于已证明稳定创作提升 |
| 针对混合测试强化“不删配角”“不补回忆解释” | 不能作为已定位系统缺陷的修复证据；其合理范围应是相应审查阶段，不应继续由该样本驱动全局补丁 |

## 本轮修正

评测 fixture 升为 v2：保留历史混合用例与 ID，标为 video_prompt_review，不用于剧本回归比较；新增不带生成限制的 script_review 和独立的 generation_constraint_adaptation。所有用例明确 stage，控制规则禁止跨阶段推断。新增测试验证阶段分离和原始角色存在。

撤回旧报告中的跨层评分，保留原始输入、输出和发布哈希，不篡改历史记录。本轮没有修改 skill/config、回滚线上 v25、重启服务或调用生成模型。

后续应先分别测试纯剧本、分镜、生成约束，再决定是否收窄已发布的审查条款。不能因为模板约束的审查建议，就反过来限制剧本出场人物；也不能一概忽略用户明确提出的故事人数限制。
