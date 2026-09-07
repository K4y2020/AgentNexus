---
name: novel-storyboard
version: 1.3.0
description: |
  给 AI 短剧出分镜：三层结构——段（一次视频生成，≤15 秒）→ 分镜（段内默认 2–5 秒、可配置的剪切，认领剧本节拍）
  → 分镜图（每切一张关键帧：主分镜图钉 0.00 秒，子分镜图钉各自切点）。
  每段自带一条 MiniMax H3 视频提示词（官方口径默认英文、逐镜换行，promptLang 可切中文）：对齐指令和
  [Shot k] 切点时刻由分镜结构推导、逐字对账，台词逐字进 <d> 块（写法规范已内化为
  references/h3-prompt.md，不依赖外部 skill）。
  产出 storyboard.json + Markdown + 单页评审报告（分镜节奏带 / 分集分镜表 / 生成批次单 /
  配音对齐单，含导出 JSON）。Cine 先读 V3 画布再直接编辑卡片，以真实设定图为参考，经授权提交 workers 出图。
  17 道质量门全部由脚本确定性检查（第 17 道 shot-recipe 可选：挂上 shot-recipes 卡库才查，不挂就明说跳过）；
  export 一键导出 H3 投产包（每段提示词 + 按 Picture 序的分镜图清单）。本地脚本零依赖，生成仍可能计费。
  Use when asked to 分镜、出分镜、镜头表、切镜、storyboard for AI short drama。
allowed-tools:
  - Read
  - Write
  - Bash
  - Task
  - Glob
triggers:
  - novel-storyboard
  - 分镜
  - 出分镜
  - 镜头表
  - 切镜
  - 首帧
  - storyboard
  - shot list
metadata:
  license: Apache-2.0
  requires:
    bins:
      - node          # >= 18，只用标准库，无 npm 依赖
  runtimes:
    - claude-code
    - codex
---

## novel-storyboard

### 跨 skill 交接与适用范围

跨 skill 管线以 [production-handoff.md](../film-analysis/references/production-handoff.md) 为权威；本 skill 仍可独立使用，独立产物不自动代表全管线就绪。
先明确模式：`analysis` 只记录观察，`faithful` 忠实重建，`adaptation` 按已确认方案改编，`original` 原创。复用用户已给出的答案，只补缺失或冲突项；改编幅度不得默认“抽核”。来源观察保持不可变，与虚构改编分开保存；改编方案先于新分镜，按叙事功能映射来源，不逐切照搬。
合同哈希与上游版本 pins 存在 sidecar，不改 outline/cast/art/script/storyboard 五份原生 schema。共享检查命令为 `python <film-analysis>/pipeline/handoff.py <production.json>`；只检查结构交接就绪，不替代原生 validator、视觉 QA 或运行时来源门，更不是 production PASS。共享文件/检查器不可用时明确交接检查受阻，不伪造通过。
本文及下级参考中的风格、钩子、爽点门限用于短剧；非短剧保留原作节奏，不为过门改成爽剧。仅使用现有参数；不支持的风格/提供商门如实报告不兼容，不编造 CLI 开关或绕过校验。
Cine 负责制作，V3 保留画布和 workers。先 `seedance_read_canvas` 再 `seedance_edit_canvas`；更新/删除带读到的 `expected_revision`，冲突先重读，删除另需 `confirm: true`。生成须已有明确范围授权才可 `submit_generation` 并置 `generation_allowed=true`，重试也须在授权范围内，不承诺免费。
V3 Agent 仅定位为 QA；现有 `seedance_agent_message` 没有代码级只读隔离。只读 QA 强制隔离落实前，在本地或只读快照上检查，禁止用泛用 Agent 消息冒充安全 QA，也不通过它委托制作。禁止 codex `$imagegen` 及其 fallback。
渲染图像字段只接受真实本地图像路径，不能填远程 asset ID。画布引用反过来需要已上传的真实资产及返回 ID，不能拿本地路径冒充远程引用；上传/引用能力不可用就阻塞。路径、旧文件、任务状态都不是图像证据；核对本次来源并实际看图后才可验收。不可用图像保持缺失，阶段校验通过不等于制作通过。
`analysis` 在来源分析结束，不进入 novel 制作阶段。跨 skill 的 `source-material.json`、`shot-mapping.json` 按共享合同组织，原始观察与解读分离；未知身份、对白和动作保持未知。使用实际文件哈希，不能只刷新 pins 来消除过期错误，须先复核受影响的下游产物。


给 AI 短剧出**分镜**——管线里第一个直接面对视频模型的层。镜头数量按叙事、生成成本和一致性预算确定。下述 exporter/validator 面向 H3，默认段上限 15 秒，并非所有模型的通用限制。

**核心机制：镜头认领节拍。** 每个镜头声明它覆盖剧本某场的哪几个连续节拍（`sceneIndex` + `beats: [起, 止]`），镜头不许跨场次——换景必换镜。这让分镜和剧本的关系变成可机械对账的：

| 交付 | 解决什么 |
| --- | --- |
| 节拍认领 | 每个节拍被恰好一个镜头认领、顺序不乱——剧本改了重跑 validate，失效的镜头当场点名 |
| 段默认 ≤ 15 秒 | H3 工作流默认上限，使用现有 `params.maxSegmentSeconds` 配置；切长用 `params.minCutSeconds` / `params.maxCutSeconds` |
| 台词装得下 | 认领节拍的台词秒数 ≤ 镜头秒数——逐镜检查，不是拍脑袋 |
| 首帧 + 运动双提示词 | 首帧给图像模型（配合参考图），运动是模型无关的过程描述；景别、运镜是枚举，英文短语必须写进对应提示词 |
| **H3 视频提示词（每镜一段）** | MiniMax H3 的 I2VA 结构：固定对齐指令 + integrated_multimodal_description + overall_soundscape + non_diegetic_music。**认领节拍的台词逐字进 `<d>[Chinese] …</d>` 块**——对白、声景、配乐一段提示词全带上 |
| 生成批次单 | 同场景 + 同光照的镜头归一批，共用同一张环境参考图——AI 版的顺场表，脚本自动汇总 |
| 配音对齐单 | 每句台词对到镜号——TTS 音频贴到哪一段视频，脚本自动汇总 |

`{baseDir}` = 本文件所在目录。脚本 `{baseDir}/scripts/novel-storyboard.mjs`，零依赖，`node` 直接跑。

**边界（不做的事）**：不写戏不改台词（`novel-script` 的活）、不出场景/角色/道具设定图（`novel-art` / `novel-characters` 的活）、不做视频生成与剪辑合成。口型/唇形同步暂不管——那是生成管线的事。

---

### Step 0 — 定输入与范围

**script.json 是硬前提**——分镜离开剧本没有意义，validate/render 都必须给 `--script`。其余上游按有则用：

- `--outline` / `--cast`：提示词禁人名检查 + 报告里 C01 显示成人名
- `--art`：报告里 S01 显示成场景名 + 批次单嵌场景设定图
- `--shots <卡片目录>`：**可选**挂载 shot-recipes 的镜头配方卡库（指向 `shot-recipes/references/cards`，只接受目录不接受导出的 JSON），开第 17 道 `shot-recipe` 门。没装 shot-recipes 就别给——本 skill 自包含，不依赖它

**一次切几集**：跟剧本的批次走（剧本写到哪就分到哪），默认一批 ≤ 3 集。

目标提供商必须明确。现有 `export` 和 H3 提示词门仅支持 H3；Seedance 等未支持的提供商导出明确 blocked，可交付结构化分镜草稿，但不得为了通过校验填写假的 `h3Prompt`。草稿缺 H3 字段时如实报告原生校验未通过，不声称已实现通用 provider 导出。

### Step 1 — seed 工作底稿

```bash
node {baseDir}/scripts/novel-storyboard.mjs seed <script.json> --eps 1-3 > <workdir>/storyboard.json
```

确定性展开：每场的节拍清单（编号、动作/台词、每拍秒数、说话人）进 `seedScenes`，这就是切镜时的工作底稿。**每拍几秒是算出来的，不要让模型重新估。** shots 留空，切镜才是模型的活。

### Step 2 — 逐集分段切镜

每集一份任务，能并发就并发。每份任务拿到：

- `{baseDir}/references/storyboard-pass.md` 和 `{baseDir}/references/schema.md`（读它们，照着做）
- 该集的 seedScenes 底稿 + 场景卡（art.json 的锚点与光照提示词）+ 角色卡（cast.json 的形象要点）

流程：**先按剧情单元分段**（不跨场，段时长由叙事及模型能力决定），**段内分镜默认 2–5 秒，按已确认节奏配置 `params.minCutSeconds` / `params.maxCutSeconds`**（对话正反打、关键动作插入特写、进场三件套——切镜语法都在 storyboard-pass.md），每切写一条分镜图提示词。

**仅 H3 导出工作流每段写一条 `h3Prompt`**，照 `{baseDir}/references/h3-prompt.md` 写（官方方法论的内化版，**不依赖任何外部 skill**）。官方口径默认英文（`promptLang` 可切中文），**每个镜头独立一行**。要点：首行对齐指令和 `[Shot k]` 切点时刻**由分镜秒数推导，一个字符都不许漂**（validate 逐字对账）；认领台词**逐字**进 `<d>[Chinese] …</d>`；每切的运镜词写进自己那一行；声景与配乐分进后两个字段——**声景也是动作指令，画面改了声景一起改**。

切完把 `seedScenes` 删掉。

### Step 3 — 校验 ⛔ 不能跳

```bash
node {baseDir}/scripts/novel-storyboard.mjs validate <storyboard.json> \
  --script <script.json> --outline <outline.json> --cast <cast.json> \
  [--shots </path/to/cards>]
```

17 道质量门全是代码：节拍全覆盖（分镜级，恰好一次、按顺序、连续）、段 0 < 总秒 ≤ 15、**每切默认 2–5 秒（现有 params 可配置）**、台词装得进分镜、每集总时长在剧本目标 ±15% 内、同框 ≤ 3 人（超了必须带拆解说明）、段号 E01-01 格式连号、景别短语在分镜图提示词里、**风格短语统一**（`style` 预设 realistic/ghibli 与角色/场景 skill 同名对齐，同剧分镜图不许画风漂）、运镜用 H3 词表且在自己的 [Shot k] 段落里、**H3 对齐指令由分镜结构推导逐字对账 + 切点时刻逐个对**、**认领台词逐字进 `<d>` 块**、**提示词语言与 promptLang 一致**（双向查：中文写成英文、英文混进中文都拦）、分镜图提示词全英文非空、英文提示词不含角色名（中文 H3 提示词放行）、场次/人物/道具对账剧本、**镜头配方对账**（可选门，见下）。

**适用的结构错误逐条修后重跑。** H3/短剧风格门不兼容则报告阻塞，不伪造提示词或重写原片来通过。

**第 17 道 `shot-recipe`（可选挂载）**：给了 `--shots` 才查，不给就明说跳过。cut 上可以写一个可选的 `recipe`（配方卡 id，**cut 级不是 segment 级**，**多格配方靠连续同 id 的分镜表达**，不是数组），门查三条——id 在卡库里、卡片的每条 `must_phrases` 出现在该切的 `frame` 里（两边小写化后 `includes`）、卡片 `cuts` 下限 ≥ 2 时连续同 id 的分镜数不得低于该下限。卡片的**建议景别与运镜不设门**，只在报告的「配方」列和 `checkup` 末尾提示偏离：配方是语汇不是法条，可选挂载的东西一旦变严就没人挂。

### Step 4 — 出分镜图（可选）

分镜由 Cine 制作；先 `seedance_read_canvas` 检查当前绑定项目，再用 `seedance_edit_canvas` 编辑，更新/删除带 `expected_revision`。经明确范围授权后才可 `submit_generation` 并置 `generation_allowed=true`。按 `references/frame.md` 执行；不经 `seedance_agent_message` 制作或冒充隔离 QA。

- **Seedance 未运行时**：生成提示词与占位，仍产出完整分镜结构，只交提示词，报告显示占位不装有
- **参考图**：核对实际资产后通过画布引用连线挂上该段场景设定图（该光照状态）+ 画内角色的设定图 + 涉及道具的设定图，提示词只负责取景和此刻的姿态
- 一格一次调用绝不批量；输出 `./<段号>/f<切序>.png`（f1 = 主分镜图，每段一个文件夹）
- **取得明确生成授权后，默认先出第一段的整套分镜图给用户看效果**（3–5 张），确认画风和正反打构图再往后补——一集约 30–40 格，错了浪费的是整批
- 单个失败跳过不阻断，最后汇总说明

### Step 5 — 输出与汇报

```bash
cd <输出目录>
node {baseDir}/scripts/novel-storyboard.mjs render <剧名>-storyboard.json --md \
  --script <script.json> --outline <outline.json> --art <art.json> > <剧名>-storyboard.md
node {baseDir}/scripts/novel-storyboard.mjs render <剧名>-storyboard.json --html \
  --script <script.json> --outline <outline.json> --art <art.json> > storyboard-report.html
```

报告界面语言用 `--lang zh|en` 指定（优先级 `--lang` > JSON 顶层 `lang` 字段 > 默认中文）——只切界面标签，与 `promptLang`（H3 提示词语言）互相独立。`render` 从 `<段号>/f<切序>.png` 找图（批次单还会找场景设定图）。只放核对来源并实际看图验收的本地文件；旧文件不算本次成图，缺图保持占位。报告含：KPI 带、分镜节奏带（粗分隔 = 段边界、片宽 = 分镜时长占比、颜色深浅 = 景别远近、点击跳段卡）、分集分镜表（主分镜图 + 子分镜条 + 逐切分镜行 + 分镜图/H3 提示词复制按钮）、生成批次单、配音对齐单、质量门、导出 JSON。Markdown 版每段附完整 H3 提示词，直接复制可用。

汇报一句话说清：几集几镜、总时长 vs 目标、几个生成批次、出了几张首帧、报告路径；没过的门和没出的图明说。

最终落地：

```
<输出目录>/
├── <剧名>-storyboard.json
├── <剧名>-storyboard.md
├── storyboard-report.html         ← 双击就能开
├── manifest.json                  ← export 生成
└── E01-01/                        ← 一段一个文件夹 = 一次 H3 生成的全部材料
    ├── f1.png                     ← 主分镜图（授权生成并取得真实本地图像后才有）
    ├── f2.png …                   ← 子分镜图
    └── prompt.md                  ← H3 提示词（export 生成）
```

---

## 五个 skill 的接力（管线到此闭环）

```
novel-outline    → outline.json    （什么：结构与分集）
novel-characters → cast.json       （谁：角色设定图）
novel-art        → art.json        （哪里：场景/道具设定图）
novel-script     → script.json     （戏：场次、节拍、台词）
novel-storyboard → storyboard.json （怎么拍：镜头、首帧、批次）
```

分镜是消费端：seed 吃 script.json，分镜图出图吃 art 和 characters 的设定图当参考，H3 提示词仅供 H3 已授权生成使用，配音对齐单接 script 台词本的 TTS 产物。五份 JSON 各自的报告都带导出按钮，改完都能喂回各自的 render/validate。

## 边界

- 报告界面内置中英（`--lang`，默认中文）；提示词语言由 `promptLang` 单独控制（默认英文）
- 秒数是**计划的生成时长**，不是已生成资产的实际时长——段上限按你的模型改 `params.maxSegmentSeconds`，切的节奏区间改 `min/maxCutSeconds`
- 口型/唇形同步暂不管——那是生成管线的事
- 分镜图不追求一次到位——它是给视频模型的构图锚，构图对、资产对就够，微调交给重生成

## 门失败会累积

`validate` 与 `checkup` 每次都把门的结果追加到**当前目录**的 `.gates.jsonl`；跑 `stats` 汇总：

```bash
node {baseDir}/scripts/novel-storyboard.mjs stats
```

回答三件事：**哪道门最常响**（那条规则模型最常无视，该改的是措辞）、**哪道门从没响过**（可能是死门，也可能规则已被内化）、**失败详情长什么样**（反复出现却没有门的那类问题，只能靠人看）。

不想记加 `--no-log`；写不进去静默跳过，不影响校验。

## 自测

```bash
node {baseDir}/scripts/selftest.mjs
```

254 项断言，不调模型、不花额度。17 道质量门每一道都有击穿用例。改完脚本先跑这个。

## 自带样例

`{baseDir}/examples/渡口-storyboard.json`：《渡口》第 1 集完整分镜——10 段 34 切认领剧本全部 35 拍，平均 3.5 秒一切，共 119 秒 / 目标 120 秒，2 个生成批次，每段带完整的 H3 视频提示词（多图对齐 + 切点时刻全部对账通过）。当质量基准，也是自测夹具。
