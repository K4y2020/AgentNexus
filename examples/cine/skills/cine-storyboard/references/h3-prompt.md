# H3 视频提示词 · 写法规范（内化版）

方法论学自 MiniMax-H3 官方提示词指南（I2VA / 多图对齐模式），**内化成本 skill 自带文档——不依赖任何外部 skill**。写每段的 `h3Prompt` 照这份做，结构部分有质量门逐字对账。

## 适用边界

### 表演与时间

切镜时间不等于表演秒表。剧本估时只用于预算；最终 H3 保留真实切点、总时长及明确需要同步的事件，镜内按因果与先后描述连续表演。不将每次眼神、伸手或停顿都补成 From/to 区间。用户指定的音乐踩点、口型和精确动作要求仍须保留，不能为“自然”删掉；后期音乐踩点只约束画面节奏，片段背景配乐仍写 `N/A`，剧内音乐则保留实际声源。动作信息的取舍服从故事，不以少写几个数字作为质量证明。

**先按参考图的实际来源选格式。** 画布（Cine 制作流程）上的视频卡只连本段的角色三视图和场景概念图，没有逐镜关键帧——一律用下面的 **Ref2VA 六段式**，不得把设定图声明为首帧或切点关键帧。只有真的逐镜出了分镜图、按 Picture 序挂载的导出包，才用后面的**关键帧对齐格式**。

事故记录：一次无人值守运行把“Picture 1 (from Shot 1) aligns with 0.00 s”写进了只连角色图和场景图的卡片——模型会把第一张角色三视图当成第一镜的画面，场景图从头到尾没被提到。`production.py` 现在用 `--ref2va` 校验分镜，这种写法过不了门。

### 旧制作包语言规则

- **默认整条英文**（`promptLang: 'en'`）——官方规范的口径：正文、对齐指令、字段名、镜头标记全英文，禁角色名（用 an old ferryman 这类通用身份）
- 三样东西保留原文语言（官方规定）：**台词**（`<d>[Chinese] …</d>` 逐字原文，一个标点都不许动，门盯着）、歌词、画面里可见的文字（英文双引号原样引用）
- `promptLang: 'zh'` 可切整条中文（对齐指令、字段名、镜头标记都有中文版，人名放行）——偏离官方推荐的备选项，实测中文效果不稳就回英文

## Ref2VA 结构（画布流程，`--ref2va` 逐条对账）

```text
subject_definitions:
<Subject 1> — C01, a slight nineteen-year-old woman with two long braids, hugging an old leather suitcase; face, hair and costume come entirely from <Picture 1>.
<Subject 2> — C03, a hunchbacked ferryman of about seventy; face, build and clothing come entirely from <Picture 2>.
<Subject 3> — S02, an old wooden ferry pier reaching into dense white fog; layout, materials and lighting come entirely from <Picture 3>.

summary: 一句话写这段发生什么（英文）。

retention_analysis: <Subject 1>, <Subject 2> and <Subject 3> are retained exactly as referenced — the same faces, costumes, environment and lighting. Only the action, the camera and the timing are new.

detailed_description:
[Shot 1] Cinematic, live-action, … 这几秒发生什么、镜头怎么动、谁说了什么。On screen: <Subject 1>, in <Subject 3>.
[Shot 2] At 00:03.000, the camera cuts … <d>[Chinese] 台词原文</d> On screen: <Subject 2>, in <Subject 3>.

overall_soundscape: …

non_diegetic_music: N/A
```

- **一张参考图一个 Subject**：本段场景（剧本场次的 `sceneId`）一个，`cuts[].characters` 里出现过的每个角色各一个；不多不少——画布只会给这段连这些图。编号从 1 连续，按出镜先后写、场景放最后即可；导入画布时会按 Seedance 实际的参考图顺序（画布上卡片的创建顺序，与连线顺序无关）统一重排编号，正文里的 `<Subject N>` 一起改。
- **定义行写 ID**（`C01`、`S02`），用 `<Subject N> — ` 开头（长破折号），并引用自己的来源图 `<Picture N>`；英文模式正文禁人名，ID 不算人名。导入画布靠 ID 认出这是哪张参考图。
- **每个 Subject 都要在 `detailed_description` 的镜头行里出现**，场景也一样——定义了却不用，模型照样可能忽略那张图。
- **承接段（`continuesFrom`）多一张图：上一段的尾帧。** 上一段生成完，画布自动把它的最后一帧接到这一段，它也要定义成一个 Subject，ID 写上一段的段号，只写段号不写角色和场景 ID，并在 `[Shot 1]` 里用上：`<Subject 4> — E01-01, the last frame of the previous segment; positions, framing and light at the opening come entirely from <Picture 4>.`，`[Shot 1] Continuing directly from <Subject 4>, …`。编号放最后，导入时同样按画布重排。不承接的段不写它，画布也不会给它接尾帧。
- 六个字段各出现一次、按序；`[Shot k]` 切点时刻规则与下面相同；`retention_analysis` 写参考内容保留，不是观众留存；开场钩子写进动作时间线。

## 关键帧对齐结构（仅逐镜出了分镜图的导出包）

```text
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 2) aligns with the 3.00-second mark of the target video; ….
（单分镜的段改用官方 I2VA 固定句：For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.）

integrated_multimodal_description:
[Shot 1] Cinematic, live-action, cold gray-green palette. 按 <Picture 1> 的构图锚定人物与状态，再写这几秒发生什么、镜头怎么动、谁说了什么（全英文）。
[Shot 2] At 00:03.000, the camera cuts to <Picture 2>: ……（**每个镜头独立一行**，切点时刻开头，等于前面分镜秒数的累计）

overall_soundscape: 1–4 句英文：环境声、动作声、非语言人声。不复述台词。

non_diegetic_music: N/A
```

每段背景配乐字段必须精确为 `N/A`，由用户在后期剪辑时统一添加配乐。不要在描述或声景中另写观众背景音乐。剧中人物听得到的收音机、现场演奏、手机外放等音乐仍写在对应镜头的描述里，交代实际声源。

中文模式（promptLang=zh）的对应 token：`参考图与目标视频的对齐——` / `整体视听描述：` / `[镜头 k] 于 00:0X.XXX，` / `非叙事配乐：N/A`；Ref2VA 的六个字段名保持英文，配乐值也固定为 `N/A`。

首行对齐指令和切点时刻**由分镜秒数推导**，改了秒数忘改提示词，validate 当场拦。

## 运镜

- 词表 20 种（schema.md 的 camera 枚举），可加幅度（小幅/大幅）与速度（缓/快），写成自然动作句：「镜头小幅缓推向掐白的指节」
- **每个分镜的运镜词必须落在自己那一行里**：英文用官方词（static shot / push in / tracking shot……），中文模式用词表的中文词（固定/推/拉/跟拍……）——门按 `promptLang` 检查

## 说话人与台词

- 说话人第一次出现给足辨识信息（身份、年龄段、音色、语速），编号 `(S1)` `(S2)` 全段稳定；同说不同人用 `(S1,S2)`
- `<d>` 里只放语言标签和台词原文；身份、音色、语气写在 `<d>` 外面
- **画外音**：中文写「以画外音说（唇形完全闭合）」；英文用官方句式 `says in an off-screen voiceover … while their lips remain completely closed`
- 画面里真实可见的文字（招牌、字条）用英文双引号原样引用，不翻译

## 声音字段的分工（踩过的坑）

- 台词、歌声、剧内音乐 → 描述字段；环境与动作声 → `overall_soundscape`；`non_diegetic_music` → 固定 `N/A`，用户后期统一配乐
- **声景也是动作指令**：画面动作改了，声景必须一起改——声景里写「铜铃在撞击时炸响」，视频就真把撞击演出来

## 关键帧怎么用

- 主分镜图（f1）钉 0.00 秒，是这一段世界观的完全参照；每个 `[Shot k]` 先锚定 `<Picture k>` 的构图与人物状态，再写动作展开
- 动作遵守 cine-script 的**常见动作原则**：挑担上船、搭手卸担这类模型见过千万次的动作；精确物理交互、微表情不要写
- 人物**此刻的位置状态**（已上船 / 在舱内）要和分镜图一致——图与文对不上，模型听图的，动作就乱
