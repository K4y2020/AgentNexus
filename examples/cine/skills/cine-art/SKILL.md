---
name: cine-art
version: 1.2.0
description: |
  给 AI 短剧出美术设定集（场景 + 叙事道具）：场景的设计意图、一致性锚点、光照时段变体、
  空景提示词；道具的戏剧功能、状态变体、尺度参照、白底无手提示词。
  产出 art.json + Markdown + 单页评审报告（含导出 JSON）。
  为 AI 生成而设计，不是实拍——环境和道具都是生成资产，交付的是让它们跨集长一样的一致性方案；
  11 道质量门全部由脚本确定性检查（锚点 3–5、无人无手、白底可抠、尺度短语、提示词英文……）。
  有 cine-outline 的 outline.json 就用 seed 预填场景清单与出现集；Cine 通过 seedance_read_canvas / seedance_edit_canvas 同步美术设定，授权后交 V3 workers。
  本地脚本零依赖；模型与生成服务的费用按实际运行环境计。
  Use when asked to 场景设定、出场景、环境设定集、场景一致性、scene bibles for AI short drama。
allowed-tools:
  - Read
  - Write
  - Bash
  - Task
  - Glob
triggers:
  - cine-art
  - 美术设定
  - 场景设定
  - 场景道具
  - 出场景
  - 道具设定
  - 环境设定
  - scene bible
  - prop sheet
metadata:
  resource-access: documentation
  license: Apache-2.0
  requires:
    bins:
      - node          # >= 18，只用标准库，无 npm 依赖
  runtimes:
    - claude-code
    - codex
---

## cine-art

### 跨 skill 交接与适用范围

跨 skill 管线以 [production-handoff.md](../film-analysis/references/production-handoff.md) 为权威；本 skill 仍可独立使用，独立产物不自动代表全管线就绪。
先明确模式：`analysis` 只记录观察，`faithful` 忠实重建，`adaptation` 按已确认方案改编，`original` 原创。复用用户已给出的答案，只补缺失或冲突项；改编幅度不得默认“抽核”。来源观察保持不可变，与虚构改编分开保存；改编方案先于新分镜，按叙事功能映射来源，不逐切照搬。
合同哈希与上游版本 pins 存在 sidecar，不改 outline/cast/art/script/storyboard 五份原生 schema。共享检查命令为 `python <film-analysis>/pipeline/handoff.py <production.json>`；只检查结构交接就绪，不替代原生 validator、视觉 QA 或运行时来源门，更不是 production PASS。共享文件/检查器不可用时明确交接检查受阻，不伪造通过。
本文及下级参考中的风格、钩子、爽点门限用于短剧；非短剧保留原作节奏，不为过门改成爽剧。仅使用现有参数；不支持的风格/提供商门如实报告不兼容，不编造 CLI 开关或绕过校验。
Cine 负责制作，V3 保留画布和 workers。先 `seedance_read_canvas` 再 `seedance_edit_canvas`；更新/删除带读到的 `expected_revision`，冲突先重读，删除另需 `confirm: true`。生成须已有明确范围授权才可 `submit_generation` 并置 `generation_allowed=true`，重试也须在授权范围内，不承诺免费。
V3 Agent 仅定位为 QA；现有 `seedance_agent_message` 没有代码级只读隔离。只读 QA 强制隔离落实前，在本地或只读快照上检查，禁止用泛用 Agent 消息冒充安全 QA，也不通过它委托制作。禁止 codex `$imagegen` 及其 fallback。
渲染图像字段只接受真实本地图像路径，不能填远程 asset ID。画布引用反过来需要已上传的真实资产及返回 ID，不能拿本地路径冒充远程引用；上传/引用能力不可用就阻塞。路径、旧文件、任务状态都不是图像证据；核对本次来源并实际看图后才可验收。不可用图像保持缺失，阶段校验通过不等于制作通过。
`analysis` 在来源分析结束，不进入 novel 制作阶段。跨 skill 的 `source-material.json`、`shot-mapping.json` 按共享合同组织，原始观察与解读分离；未知身份、对白和动作保持未知。使用实际文件哈希，不能只刷新 pins 来消除过期错误，须先复核受影响的下游产物。


给 AI 短剧出**美术设定集**：场景 + 叙事道具。**前提刻在骨子里：这是 AI 生成，不是实拍**——没有堪景搭景置景采买，环境和道具都是要被生成几十次还得长一样的资产，所以交付物全部围绕一致性：

| 交付 | 解决什么 |
| --- | --- |
| 一致性锚点（每景 3–5 个） | 观众靠它认场景，QC 靠它核对生成镜头有没有漂 |
| 光照时段变体 | AI 换时段是重新生成不是重新打灯，每个状态落成完整提示词 |
| 空景出图提示词 | 环境和角色是两层资产，参考图里混进人，一致性全毁 |
| 变体机制（variantOf） | 变体复用母场景资产有助一致性；新生成仍有成本 |
| 道具状态变体 | 皮箱的合上与打开是两张参考图——道具有状态弧，场景没有 |
| 道具尺度参照 | AI 经常把手持道具画成家具尺寸，提示词必须带尺度短语 |
| 道具白底无手 | 道具图要被贴进各种镜头，必须可抠；拿着道具的手是最常见污染 |

`{baseDir}` = 本文件所在目录。脚本 `{baseDir}/scripts/cine-art.mjs`，零依赖，`node` 直接跑。

**边界（不做的事）**：不做分镜、不写剧本、不管角色（`cine-characters` 的活）、不排大纲（`cine-outline` 的活）。**道具只收叙事道具**（有特写、跨集、承载剧情的，通常 3–8 件）——场景陈设归场景锚点，一次性手部道具镜头级提示词解决，都不单独建资产。

---

### Step 0 — 定输入与画风

三种输入，优先级从高到低：

1. **outline.json**（cine-outline 的产出）——最优，场景清单、出现集、承载爽点、复用方案都是现成的
2. 小说原文——自己归纳场景清单（主舞台优先，参考 cine-outline 的主场景上限思路：别贪多）
3. 用户手写的场景清单

画风：**默认 `realistic`**（半写实厚涂），动画质感用 `ghibli`。**跟角色 skill 保持同一档**——角色是吉卜力、场景是半写实，合成的时候没法看。跑 `node {baseDir}/scripts/cine-art.mjs styles` 看预设全文，整块取用不混搭。

有 cast.json（cine-characters 的产出）也带上——校验「提示词不含角色名」要用。

### Step 1 — seed 骨架（有 outline.json 才有这步）

```bash
node {baseDir}/scripts/cine-art.mjs seed <outline.json> > <workdir>/art.json
```

确定性搬运：场景 id/名称/主场景标记/出现集/承载爽点，带复用方案的场景会有 `seedNote` 提示做成变体。**这些事实不要让模型重新想一遍。**

没有 outline.json 就自己按 `references/schema.md` 建清单。

### Step 2 — 逐场景填设定 + 提取叙事道具

每个场景一份，能并发就并发。每份任务拿到：

- `{baseDir}/references/scene-pass.md` 和 `{baseDir}/references/schema.md`（读它们，照着做）
- 该场景的骨架 + 原文/大纲里关于这个空间的全部信息
- **同批其他场景的名字**（空间气质要区分开，别都写成同一种破旧）
- 画风预设全文（`styles` 命令的输出）

核心要求都在 scene-pass.md 里，最重的三条：锚点要**可画可认可核对**（「补丁船篷」是锚点，「陈旧的氛围」是形容词）；光照状态**从分集反推**，不写用不上的全家桶；**能做变体就别开新景**。

**叙事道具优先吃大纲**：`outline.json` 从 1.1.0 起带 `props`，`seed` 会把道具表连同「承载什么」「托起哪几个爽点」「哪几集出现」一起搬过来，这一层只填设计字段（尺度、锚点、状态变体、白底提示词）。**大纲没有 props 就从原文提取**，那时这步是模型的活：只收**有特写、跨集出现、承载剧情**的，通常 3–8 件，跟主角数量一个量级。每件按 `references/prop-pass.md` 填：戏剧功能、锚点、状态变体、尺度、白底无手提示词。皮箱这种「跟人走的道具」就该在这——塞进场景锚点和角色画像都不对。

### Step 3 — 校验 ⛔ 不能跳

```bash
node {baseDir}/scripts/cine-art.mjs validate <art.json> --cast <cast.json>
```

11 道质量门全是代码。场景 + 共用 7 道：锚点 3–5、光照状态 ≥1、**无人**、提示词全英文、不含角色名（给了 --cast 才查）、变体引用完整、风格与反向词匹配。道具专属 4 道：**状态 ≥1**、**尺度短语写进提示词**、**反向词禁手**、**设定图纯白背景**。

**有违规逐条修，改完重跑，直到通过。**

### Step 4 — 出图（可选）

场景和道具各一张 16:9 设定图，版面都是**主视角大图 + 底部和右侧的 L 形细节边框**。场景：标准取景 + 第一个光照状态，细节格是锚点特写。道具：白底三四分之一主视角（主状态），细节格是锚点特写 + 其他状态 + 侧面。读 `{baseDir}/references/sheet.md` 照调用契约做，要点：

- **V3 工具不可用或未获授权就跳过出图**，只交提示词并标图像缺失
- **全图无人**；道具图另加**无手**、**纯白背景**，出现人影或手标失败，仅在已有授权范围内重生成
- **变体场景拿母场景成图当参考图**（核对实际资产后通过画布引用连线）——变体机制的意义就在这
- 一个场景一次调用绝不批量；单个失败跳过不阻断

### Step 5 — 输出与汇报

```bash
cd <输出目录>
node {baseDir}/scripts/cine-art.mjs render <剧名>-art.json --md   > <剧名>-art.md
node {baseDir}/scripts/cine-art.mjs render <剧名>-art.json --html > art-report.html
```

报告界面默认中文；用户要英文界面就加 `--lang en`（或在 art.json 顶层写 `"lang": "en"`，`--lang` 优先）。`render` 自动去 `images/<slug>-sheet.png` 找图（场景和道具都找），使用本次独立输出目录，仅放核对来源并实际看图验收的本地文件，旧文件不能充当本次成图；缺图保持占位。报告含：KPI 带、场景清单、场景设定卡、道具清单、道具设定卡（锚点核对表 / 状态变体 / 提示词包全带复制按钮）、质量门面板、导出 JSON（下载的就是 art.json 原样）。

汇报一句话说清：几个场景（主场景/变体各几）、几件道具、锚点总数、出图数、报告路径；没过的门和没出的图明说。

最终落地：

```
<输出目录>/
├── <剧名>-art.json
├── <剧名>-art.md
├── art-report.html                ← 双击就能开
└── images/
    └── <slug>-sheet.png           ← 授权生成并取得真实本地图像后才有
```

---

## 三个 skill 的接力

```
cine-outline    → outline.json （什么：结构与分集）
cine-characters → cast.json    （谁：角色资产）
cine-art        → art.json     （哪里 + 手里拿的：美术资产）
```

seed 吃 outline.json 的场景与道具两块（大纲没有 `props` 时道具留空，模型从原文提取），`--cast` 吃 cast.json。三份 JSON 各自的报告都带导出按钮，改完都能喂回各自的 render/validate。

## 边界

- 报告界面内置中英（`--lang`，默认中文、或跟 art.json 的 `lang` 字段）；出图提示词永远英文
- 画风要跟角色 skill 同档，别一半写实一半动画
- 场景与道具设定由 Cine 先读画布再直接编辑，授权后提交 V3 workers；禁止代理制作及 codex `$imagegen`
- 场景数量不设硬上限——上限在 cine-outline 的主场景门那里管；这里管的是每个资产的质量
- 道具只收叙事道具，3–8 件为宜——每多一件就多一份跨集一致性维护

## 自测

```bash
node {baseDir}/scripts/selftest.mjs
```

158 项断言，不调模型、不花额度。11 道质量门每一道都有击穿用例。改完脚本先跑这个。

## 自带样例

`{baseDir}/examples/渡口-art.json`：《渡口》三场景 + 两件叙事道具（旧皮箱、县衙旧砚）的完整设定，全部质量门通过（含对着 cine-characters 样例 cast 的角色名检查）。当质量基准，也是自测夹具。
