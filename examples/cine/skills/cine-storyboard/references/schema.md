# storyboard.json 结构

三层：**集 → 段（segment）→ 分镜（cut）**。

- **段** = 一次视频生成调用，总时长 ≤ `maxSegmentSeconds`（默认 15 秒），不跨场次——换景必开新段
- **分镜** = 段内的一次剪切，`minCutSeconds`–`maxCutSeconds`（默认 2–5 秒），各自认领剧本节拍、带景别运镜和一张分镜图
- **分镜图** = 每个分镜一张关键帧：第 1 切的是**主分镜图**（钉在 0.00 秒），其余是**子分镜图**（各钉在自己的切点时刻）。**每段一个文件夹**：`<段号>/f<切序>.png` + `prompt.md`（export 生成，内容就是 h3Prompt）

```json
{
  "source": "渡口",
  "style": "realistic",
  "continuityContractVersion": 1,
  "promptLang": "zh",
  "params": { "maxSegmentSeconds": 15, "minCutSeconds": 2, "maxCutSeconds": 5, "maxOnScreen": 3, "tolerance": 0.15 },
  "episodes": [ { "ep": 1, "segments": [ ... ] } ]
}
```

`promptLang` 可省略（**默认 `en`——官方规范口径**）：整条英文、禁角色名，台词在 `<d>[Chinese]` 里保留原文。设成 `zh` 可切整条中文（对齐指令、字段名、镜头标记都有中文版，人名放行）——偏离官方推荐的备选项。`style` 可省略（默认 `realistic`），预设与角色/场景 skill 同名对齐（`realistic` / `ghibli`），对应的英文短语（如 `cinematic film still`）必须出现在**每条**分镜图提示词里——同一部剧的分镜图不许画风漂，门查。

## segment（段）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 段号 `E01-01`：集号 + 两位序号，**按顺序连号**。它就是素材文件名（`E01-01.mp4` / `E01-01-f1.png`） |
| `sceneIndex` | int | 这一段在剧本该集的第几场（1 起）。段内全部分镜同场 |
| `cuts` | cut[] | 段内分镜，按时间顺序。段总秒数 = 分镜秒数之和，**不单独存**——少一处会漂的冗余 |
| `h3Prompt` | string | **一段一条 H3 视频提示词**，正文语言跟 `promptLang`（默认中文），结构见 `references/h3-prompt.md` |
| `note` | string | 备注，可选 |

## cut（分镜）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `beats` | [int, int] | 认领该场第几拍到第几拍（含两端）。**每个节拍必须被恰好一个分镜认领**，按顺序、连续 |
| `seconds` | number | 分镜时长，2–5 秒——短剧的注意力节奏是硬门。认领节拍的台词秒数必须装得下 |
| `size` | enum | 景别：`extreme-wide` 大远景 / `wide` 全景 / `medium` 中景 / `close` 特写 / `extreme-close` 大特写 |
| `camera` | enum | 运镜，**直接用 H3 官方词表**（原样字符串）：`Static Shot` `Push In` `Pull Out` `Zoom In/Out` `Pan Left/Right` `Truck Left/Right` `Tilt Up/Down` `Pedestal Up/Down` `Arc Shot` `Tracking Shot` `Shake Slightly/Strongly` `POV` `Roll Clockwise/Counterclockwise` |
| `characters` | string[] | 画内人物（C 编号），必须 ⊆ 剧本该场人物；空镜给空数组。> `maxOnScreen` 时必须带 `note` |
| `characterStates` | object | 画内人物状态映射，键必须与 `characters` 完全一致，并逐字继承剧本该场 `characterStates`；如 `{ "C01": "home_morning" }` |
| `props` | string[] | 画内道具（P 编号），必须 ⊆ 剧本该场道具。可省略 |
| `frame` | string | **分镜图英文提示词**：这一格关键帧的样子。景别英文短语必须在里面；禁角色名 |
| `recipe` | string | 镜头配方卡 id，可选。挂了 `--shots <卡片目录>` 才查（`shot-recipe` 门）。**cut 级不是 segment 级**——一段可以跨多种配方；**多格配方靠连续同 id 的分镜表达**，不是数组 |
| `continuity` | object | `continuityContractVersion: 1` 时必填：轴线、画面位置、视线、运动方向和道具状态。用于检查相邻镜头，不能只写在 `frame` prose 里 |
| `note` | string | 备注，可选 |

`recipe` 是**可选挂载**：不给 `--shots` 就整门跳过。给了就查三条——id 在卡库里、卡片的每条 `must_phrases` 出现在该切的 `frame` 里（两边小写化后 `includes`）、卡片 `cuts` 下限 ≥ 2 时连续同 id 的分镜数不得低于该下限。卡片的**建议景别与运镜不设门**，只在报告里提示偏离：配方是语汇不是法条。

### continuity（连续性账本）

启用 `continuityContractVersion: 1` 后，每个分镜必须填写：

```json
{
  "axisId": "counter_axis_01",
  "screenPositions": {"C01": "left", "C02": "right"},
  "eyelineTarget": "C02",
  "motionVector": "left_to_right",
  "propStates": {"P01": "on_table"},
  "actionStates": {"C01": {"in": "idle", "out": "reaching"}},
  "emotionStates": {"C01": {"in": "breath_held", "out": "jaw_set"}},
  "establishing": true
}
```

`emotionStates` 记录的是可观察的表演状态，不是抽象情绪标签，例如 `breath_held`、`jaw_set`、`eyes_widened`、`guarded`、`teasing_smile`、`shoulders_dropped`。每个画内人物都必须填写 `in/out`；相邻镜头状态发生变化时，必须填写 `emotionCarryReason`，说明是哪个消息、动作、反应或权力变化造成了情绪转折。不要只写“变得更悲伤/更愤怒”，要写身体、呼吸、视线、姿态或语速的可拍变化。

相邻镜头会自动检查轴线是否改变、人物是否瞬移到画面另一侧、上一镜视线目标是否在下一镜兑现、动作 `out → in` 是否断裂、表演状态是否无因果跳变、道具状态是否无因果跳变、运动方向是否突然反转。还会对照 `frame`：画面写了抓取、转身、递交、行走、说话等可见动态动作时，不能用 `idle → idle` 充数；画面写了明确看向某处时，不能把 `eyelineTarget` 留空。确有越轴、换位、切断视线、交接、反向动作或情绪转折时，必须在当前镜头填写对应的 `axisBreakReason`、`positionChangeReason`、`eyelineBreakReason`、`actionCarryReason`、`emotionCarryReason`、`propChangeReason` 或 `directionChangeReason`；若证据不足，使用 `actionEvidenceReason` / `eyelineUnknownReason` 明确标记待确认。旧分镜未声明版本时保留兼容模式，但新生产包必须启用该合同。

## h3Prompt 的结构（三道门盯着，两处逐字对账）

写法见 `references/h3-prompt.md`（官方方法论的内化版，本 skill 自包含不依赖外部 skill）。骨架：

```text
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 2) aligns with the 3.00-second mark of the target video; ….

integrated_multimodal_description:
[Shot 1] Cinematic, live-action, …（首格锚定 → 动作 → 运镜 → 对白）
[Shot 2] At 00:03.000, the camera cuts to …（每个镜头独立一行，切点时刻开头）

overall_soundscape: …（环境声与动作声，1–4 句）

non_diegetic_music: …（1–3 句，没有就 N/A）
```

确定性检查的五条：

1. **首行对齐指令整行由分镜结构按 `promptLang` 推导**（`h3AlignmentLine`）：多分镜的段把每张分镜图钉在自己的切点秒数上；单分镜的段用固定句式。validate **逐字对账**——分镜秒数一改，旧指令立刻对不上
2. 三个字段名齐全且按序；描述正文有 `[Shot 1]`
3. **每个 `[Shot k]`（k ≥ 2）必须带切点时刻 `At 00:0X.XXX,`，且等于前面分镜秒数的累计**——节奏写在纸上就必须和提示词一致
4. 认领节拍的每句台词**逐字且只进认领它的本镜 `[Shot k]`** 的 `<d>[Chinese] …</d>`；不能因为台词出现在同一段的其他镜头，就算本镜通过。说话人身份音色语气用英文写在 `<d>` 外；画外音用 `says in an off-screen voiceover` 并注明唇形闭合
5. `<d>` 块之外的正文语言与 `promptLang` 一致（中文写成英文、英文混进中文都拦）；英文模式禁角色名，中文模式放行（身份靠分镜图锚定）；每个分镜的运镜词（中文用词表中文词如「推」「固定」，英文用官方词）必须出现在**自己的 [Shot k] 段落**里

## 时长约束链

台词秒数（按剧本语速折算）≤ 分镜 `seconds` ≤ 5 秒；段 Σ分镜 ≤ 15 秒；集 Σ段 落在剧本 `targetSeconds` ±15%。全部由 validate 逐级对账。
