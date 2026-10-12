# 画风预设

出图风格默认跟原片一致（拉片记录的 `visual_style`，production seed 已写进 `style`）；用户可以另指定。没有原片记录时默认 `semi-realistic`。

预设定义在 `scripts/cine-characters.mjs` 的 `STYLE_PRESETS` 里，跑
`node scripts/cine-characters.mjs styles` 可以把整段打出来直接用。

| id | 说明 |
| --- | --- |
| `photoreal` | 写实：真人实拍质感，墙面布料干净无噪点 |
| `semi-realistic` | 半写实厚涂。没有原片记录时的默认；旧名 `realistic`（0.14.0 移除） |
| `ghibli` | 吉卜力式手绘赛璐璐动画 |

## ⚠️ 换风格是整套换，不是只换一句「画风」

每个预设自带五块，**必须整块取用，不要混搭**：

| 块 | 作用 |
| --- | --- |
| `render` | 渲染方式那句 |
| `surface` | 皮肤、眼睛、头发、布料怎么处理 |
| `lighting` | 光照 |
| `negative` | 反向提示词 |
| `tags` | 风格标签 |

**写实两档与吉卜力的 `negative` 几乎是相反的**：

- `photoreal` 与 `semi-realistic` **绝不能**禁 `photorealistic` / `3d render`——一边要真实感一边禁真实感是自相矛盾的，它们禁的是「假」（塑料皮肤、无毛孔娃娃脸、完全对称的脸）
- `photoreal` **必须**禁 `painterly` / `brush strokes`——写了绘画感，视频模型会在墙面皮肤上画出笔触，看起来像噪点
- `ghibli` **必须**禁 `photorealistic` / `3d render` / `visible pores`——写实的那些细节在这里全是反效果

`surface` 同理。写实要毛孔、皮下散射、根根分明的碎发、布料织纹；吉卜力明确要
**无毛孔、无皮肤纹理、成簇的发丝、平涂无织纹的布料**。把写实那段带进吉卜力，
出来就是个四不像。

`validate` 会检查这一条：风格与反向提示词搞反了直接报错。

## 光照的差别

- `photoreal` 与 `semi-realistic` **分区打光**：左栏半身像给方向主光 + 环境遮蔽（要体积），右侧三视图
  和细节条平光正交（要抠图和量比例）
- `ghibli` **全图平光**：均匀日光 + 单层柔和阴影。平光本来就是这个风格的一部分，
  不需要分区

## 版面不随风格变

16:9 三区、34%/66% 比例、人物比例协调、细节让位不是人物让位——这些是**版面规则**，
三个风格都一样。变的只有渲染质感。

## 加新预设

往 `STYLE_PRESETS` 里加一个键，五块写全，`label` 给齐三语。自测会检查完整性。
