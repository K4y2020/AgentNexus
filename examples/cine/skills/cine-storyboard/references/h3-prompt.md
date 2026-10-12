# H3 视频提示词 · 写法规范（内化版）

按 MiniMax H3 的参考关系组织提示词；规则在本 skill 内维护，不依赖外部 skill。先核实参考资产的用途，再选 Ref2VA 或已有关键帧格式。以下例子来自《渡口》，不是完整制作包。

## 格式、语言与时间

画布视频卡只连接角色设定图和场景概念图时，用 **Ref2VA 六段式**。设定图提供身份与美术参考，不是视频的首帧。只有实际生成逐镜分镜图并按 Picture 序挂载的导出包，才使用后面的关键帧对齐格式。

Ref2VA 六段的正文用英文，默认 `promptLang: "en"`。台词、歌词和画面可见文字保留原语言；台词逐字放入 `<d>[Chinese] …</d>` 或 `<d>[English] …</d>`，语言标签必须与内容一致。身份、音色和语气放在 `<d>` 外，正文用通用身份而非人名。已有 `promptLang: "zh"` 是兼容模式，不代表符合官方英文 Ref2VA 格式；其字段名仍用英文。

切点取各 cut 的累计秒数。首镜写 `[Shot 1]`，后续写 `[Shot 2] At 00:03.000, ...`，每镜独立一行，每镜的运镜枚举短语落在自己那一行。镜内用因果和先后描述表演，不将剧本估时展开为逐个微动作的秒表；确有同步要求时保留必要时间锚点。详细描述应交代构图、位置、光线、动作、运镜和声源，不为了建议词数堆术语或截掉对白。

## 全片风格跟设定图走

从本片 cast 的 `image.style`、`image.prompt` / `image.sheet` 以及 art 的 `image.prompt`、`image.tags` 取共同的媒介、笔触、配色与光线。预设与角色/场景同名：`photoreal` 是 **photorealistic live-action**，`semi-realistic`（旧名 `realistic`）是 **semi-realistic painterly**，`ghibli` 是 hand-painted anime。上游明确为其他预设时跟随上游；角色与场景的媒介冲突先解决。

写出一条英文风格句，可存于顶层 `styleSentence`，全片每段逐字复用。Ref2VA 放在 `detailed_description:` 之后、`[Shot 1]` 之前，不在 Shot 1 里重复风格。每个 `frame` 沿用这套媒介与具体画风。`seed --cast <cast.json> --art <art.json>` 读取上游预设并提供起稿风格句；按真实设定改句子时，只改一次并同步全片。

《渡口》的角色设定写半写实厚涂、低饱和冷调与晨雾柔光，场景标签也写 semi-realistic / painterly，因此采用：

```text
The target video uses semi-realistic painterly rendering with soft blended edges and visible brush texture, a muted ink-blue and cold gray-green palette, and soft misty morning light matching the cast and art references.
```

## Ref2VA 六段式

字段按此顺序各写一次：

```text
subject_definitions:
<Subject 1> — C01, a nineteen-year-old woman in a navy student tunic and white collar, with two long braids tied in faded red string; her appearance comes from <Picture 1>.
<Subject 2> — C03, a stooped elderly ferryman in faded indigo clothing with a straw hat and rain cape; his appearance comes from <Picture 2>.
<Subject 3> — S02, a wet wooden ferry pier with a mooring stone and a tilted lantern pole in morning fog; its layout and materials come from <Picture 3>.

summary:
[reference generation] <Subject 1> pauses beside <Subject 2> at the end of <Subject 3>, protecting her suitcase before boarding.

retention_analysis:
<Subject 1> (appears in [Shot 1], [Shot 2]): fully_preserved - the braids, student uniform and worn suitcase retain their referenced appearance.
<Subject 2> (appears in [Shot 1]): fully_preserved - the stooped build, indigo clothing and straw hat retain their referenced appearance.
<Subject 3> (appears in [Shot 1], [Shot 2]): fully_preserved - the wet planks, mooring stone and lantern pole retain their referenced layout.

detailed_description:
The target video uses semi-realistic painterly rendering with soft blended edges and visible brush texture, a muted ink-blue and cold gray-green palette, and soft misty morning light matching the cast and art references.
[Shot 1] A medium static shot establishes <Subject 3>, the wet wooden pier receding from the foreground into pale fog as water moves under its pilings. <Subject 1>, the slim young woman in a navy student tunic with two red-tied braids, stands at the left edge of the gangplank hugging her worn leather suitcase. <Subject 2>, the stooped old ferryman in an indigo jacket and straw hat, waits at the right near the mooring stone and coils a rope with a soft rasp.
[Shot 2] At 00:03.000, the camera cuts to a close-up and makes a slow push in on <Subject 1> (S1) (voice: 19-year-old female, light breathy soprano, mid-to-high pitch, thin chest support, narrow dynamic range) at the left side of <Subject 3>; she tightens her arms around the suitcase and says in a quiet, guarded tone, barely above the river noise, <d>[Chinese] 不劳烦。它跟我。</d> The leather gives a faint creak as her grip firms.

overall_soundscape:
Low river wash and fog-muted wind continue throughout the segment.

non_diegetic_music:
N/A
```

### 定义与首次出现

- 本段连接的角色和场景各定义一个 `<Subject N>`，编号从 1 连续；角色状态与来源图须匹配真实资产。定义行保留 **`<Subject N> — <ID>, …`**，如角色 `C01`、场景 `SC01` 或《渡口》的原生场景 `S02`，不能只剩描述或人名。画布导入靠 ID 查找实体。
- 首次在镜头里出现时，把标签直接写进描述，同时交代其可见特征、画面位置和当前动作；环境写布局、位置和正在发生的变化。后续继续用同一标签，不追加 `On screen: <Subject …>` 尾句。
- 每个 `[Shot k]` 都要写出画内每个人进镜时的姿态和正在做的动作，即从上一镜延续下来的状态（`continuity.actionStates` 的 `in`）。例如仍坐在船头、还跪在地上、还被绑在桅杆上，或者手里还握着桨。视频模型只读 h3Prompt，`frame`、`continuity` 和尾帧图都到不了它那里；角色设定图是站姿，所以漏写的姿态会被画成站着。特写只需写脸上看得出的姿态，即倒吊和躺着。姿态在镜头之间变了，正文写新姿态，`frame` 和 `continuity` 同步改。
- 图片仅提供身份参考时，在 Subject 定义内引用 `<Picture N>`，不另写 Picture 定义。图片实际担任首帧、关键帧或尾帧锚点时，才独立定义 Picture，并在正文与 retention 中记录用途。
- 导入按画布实际参考顺序重排编号，所有引用随之改号，逐标签 retention 行、关系和说明应原样保留。

### summary 与承接尾帧

以方括号任务前缀开头，用已定义标签简述本段动作与引用用途。普通设定图写 `[reference generation]`，具体帧锚点加 `keyframe completion`。直接接续源视频用 `video continuation`，直接修改源视频用 `video editing`；复用音频信号用 `audio reuse`，仅参考音色或节奏用 `audio reference`。多种关系用 ` + ` 合并，每种只写一次。

同一场的第二段起，每段都承接紧邻的上一段，`continuesFrom` 写上一段段号。画布连接的是上一段尾帧图片，因此保留导入所需的段号 Subject，同时定义具体 Picture 锚点：

```text
<Subject 4> — E01-01, the preceding segment's last-frame state; opening positions and light come from <Picture 4>.
<Picture 4> is the opening-frame anchor for [Shot 1], using the last frame of E01-01.
summary:
[reference generation + keyframe completion] The opening uses <Picture 4> to preserve the visible state defined by <Subject 4> before the action continues.
retention_analysis:
<Subject 4> (appears in [Shot 1]): fully_preserved - the opening keeps the previous segment's positions and lighting.
<Picture 4> ([Shot 1] first frame): fully_preserved - its composition anchors the opening before movement resumes.
```

`[Shot 1]` 自然写明从 `<Picture 4>` 起幅，并直接使用 `<Subject 4>` 的位置与动作状态，画内每个人的姿态仍要用文字写出：尾帧只拍到上一段最后一个镜头里的人，而且只起引导作用。开场景别和机位就是上一段最后一个镜头的，起幅画面里看得见的人写进 Shot 1 并列进首镜 `characters`；要换构图或换人，就在 Shot 1 里运镜过去，或在 `[Shot 2]` 切。每场第一段不写 `continuesFrom`，也不凭空增加尾帧引用。

### retention_analysis 每个标签一行

只为 `subject_definitions` 独立定义的标签写行，不为单纯标明来源的 Picture 另写行。每行注明实际使用镜头、固定关系标记和具体说明，不合成总述，不写 `(Sx)`。

可见引用使用 `fully_preserved`、`partially_preserved`、`attribute_transfer` 或 `weak_reference`；音频引用使用 `fully_copy`、`partially_copy`、`reference` 或 `weak_reference`。标记服从已定义用途，不将新动作误写成身份参考损失。这里记录参考关系，开场钩子写在镜头动作中。

## 说话人与台词

引用角色每次开口写 **`<Subject N> (Sx)`**，画外说话仍保留同一绑定。非引用角色、旁白、广播或其他独立人声用稳定身份描述加 `(Sx)`。剧本里 `speaker` 是 `extra` 的台词属于没有角色卡的人，写成 `a second attendant (S3) says` 这类描述，不能挂到某个角色的 `<Subject N>` 上，否则视频会用那个角色的脸和声音说这句；`--ref2va` 校验会拦。按本段实际首次发声顺序从 S1 编号，之后每次复用；音频定义里的音色绑定也使用这个编号。笑声、呼吸写清人物来源并沿用其编号，风雨、碰撞等环境或物件声音不分配说话人编号。纯复用音轨内部的歌词提示若没有独立说话人，以 `<Audio N>` 为声源。

**每段都写声音。** 每段单独生成，视频模型只读这一段的提示词，不知道上一段谁是什么嗓子；没写就按画面猜，一位老者短促的呵斥会被配成尖细的女声。所以引用角色在本段第一次开口之前（画外音和心声也算），把角色卡 `voice.prompt` 的第一句逐字写进来，也就是年龄性别、音色和音区这些声音身份。常见写法是紧跟 `(Sx)` 写 `(voice: …)`，同一段里再开口写 `in the same … voice`。`--ref2va` 校验会查。`voice.prompt` 后面的音量、语速和默认情绪不照抄，换成这一句的说法。

**每句写表演。** 每句台词在 `<d>` 前写清这一句看得见的表演和声音的说法：脸上的表情、手上的动作、视线落在哪、身体靠近还是退开，再加情绪和语气。依据剧本这句的 `delivery` 和拉片里这句的表演，例如 `her eyes drop to his reaching hand, she pulls the suitcase tighter against her chest and says in a quiet, guarded tone` 或 `he scrubs his eyes with the back of his wrist, then wails, strained and breathless`。只写 `in a teasing tone` 这类语调，模型就只会让人站着念词；也不能只写 `says`。一句里情绪有转折（先惊喜、再吃痛），把转折写出来；听者的反应写在这句之后。两人对话时，第二个开口的人同样绑定自己的 `<Subject N> (Sx)` 并写上声音；能让他出现在画面里就别放画外，画外的短句最容易配错声音。

画外音和心声的英文句式固定为：

```text
<Subject 1> (S1) says in an off-screen voiceover, <d>[Chinese] 谁都别碰它。</d> while her lips remain completely closed.
A calm narrator (S2) says in an off-screen voiceover, <d>[English] The crossing has begun.</d> while the visible woman's lips remain completely closed.
```

台词逐字写进认领它的本镜，不能因为在本段别的镜头出现过就算通过。画面可见文字用英文双引号原样引用。跨切点连续对白、结尾截断对白按实际情况使用 `<scenetrans>`、`<cutoff>` 并描述衔接，不主动截短剧本。

## 声音的分工

与具体画面同步的脚步、碰撞、铃响、衣物摩擦、呼吸、笑声、台词和剧内音乐，写进 `detailed_description` 的对应镜头，交代声源与动作的关系。画面动作改变时检查该镜的声音是否仍然匹配。

`overall_soundscape` 只概括贯穿本段的环境底声，如河水、风或室内底噪，不写某镜某秒的动作声，不重复对白。`non_diegetic_music` 的值精确为 `N/A`；背景配乐由用户在后期统一添加，人物听得到的音乐留在相应镜头。

## 关键帧对齐格式（兼容已有导出包）

逐镜真实分镜图已挂载时继续用原格式，单镜固定首句与多镜对齐行由结构推导，秒数改变后同步：

```text
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 2) aligns with the 3.00-second mark of the target video.

integrated_multimodal_description:
[Shot 1] The target video uses semi-realistic painterly rendering with soft blended edges and visible brush texture, a muted ink-blue and cold gray-green palette, and soft misty morning light matching the cast and art references. A medium static shot starts from <Picture 1>; the young woman waits beside the pier with her suitcase.
[Shot 2] At 00:03.000, the camera cuts to <Picture 2> and makes a slow push in as she tightens her grip; the leather creaks softly.

overall_soundscape: Low river wash continues throughout the segment.

non_diegetic_music: N/A
```

单镜段保留 I2VA 对齐句：`For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.` 中文兼容模式仍用 `参考图与目标视频的对齐——` / `整体视听描述：` / `[镜头 k] 于 00:0X.XXX，` / `非叙事配乐：N/A`。

每格 frame 与对应 Picture 的构图、人物位置和起始状态须一致。参考图与文字冲突时先核实并修正；格式校验不能证明资产存在或实际成片质量。
