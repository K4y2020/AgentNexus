# 角色设定卡同步 · Seedance V3 画布

## 直接画布调用契约

跨 skill 交接以 [production-handoff.md](../../film-analysis/references/production-handoff.md) 为权威，独立使用仍可只交设定和提示词。
Cine 负责制作，V3 提供画布和生成 workers：
1. 用 `seedance_read_canvas` 读取当前 Topic 绑定项目、节点和版本。
2. 用 `seedance_edit_canvas` 创建/更新卡片并连接实际参考资产。画布引用需要已确认上传的资产和真实返回 ID，本地路径不等于远程资产；上传/引用不可用就阻塞，不虚构上传工具。更新/删除必须带刚读取的 `expected_revision`，冲突先重读再协调；删除另需 `confirm: true`。版本保护不应被宣称覆盖工具未支持的所有操作。
3. 明确授权覆盖目标、数量和重试后，才用 `action="submit_generation"`、`generation_allowed=true` 提交。提交不等于完成，可能计费。
4. 核对返回资产与当前任务、上游 pins 的对应关系，将实际取得的图像落到下述本地路径并实际看图。远程 asset ID/URL 不能冒充 renderer 本地图像路径。
5. 图像不可用、缺参考或调用失败就保留 missing/blocked；可继续交结构化草稿，不用旧文件或路径存在宣称成图，也不把 native validator 通过当 production PASS。

不使用 codex `$imagegen`、shell codex 或任何 fallback。工具不可用时不自动切换生成服务。
V3 Agent 的定位仅为 QA；现有 `seedance_agent_message` 没有代码级只读隔离，因此不经该工具制作，也不发泛用消息声称安全 QA。强制只读 QA 实现前，在本地/只读画布快照做 QA；视觉结论必须来自实际查看的图像。

## 每个角色一张图

一张横构图，内部左右分栏：

```
┌──────────┬────────────────────────────┐
│          │   正视    侧视    背视       │
│  半身像   │                            │
│ （证件照） ├────────────────────────────┤
│  面部基准  │  细节 · 细节 · 细节 · 细节   │
│   ~34%   │                            │
└──────────┴────────────────────────────┘
                    16:9
```

提示词字段 `image.sheet`，落到 `./images/<slug>-sheet.png`。

左栏的半身像是**面部设计的基准**，右栏三视图的脸照着它画。提示词里要明确要求两边一致，否则一张图里会出现两个长相。

---

## 画风与视觉验收

同批角色使用已确认的风格预设；需要统一风格时，通过画布引用已验收的首张设定图。白底、分区光照、脸部一致性和比例都需要实际看图检查。

## 背景：白底

设定图一律**纯白背景**。理由有三个：抠图干净、印出来是设定表该有的样子、在深色报告里也能读。

### 分区光照

设定表要平光（抠图、量比例），写实要方向光（体积感）。两者矛盾，所以**分区解决**：左栏半身像给柔和方向主光 + 环境遮蔽，右侧三视图和细节条保持平光正交。提示词里是两句独立的 `LIGHTING IN THE LEFT ZONE ONLY` / `LIGHTING IN THE RIGHT ZONES`，不要合并成一句全局光照。

### 比例 ⚠️

这个版面最容易崩的就是比例——模型为了把细节条塞进去，会把三个全身像压扁或拉长。提示词里已经写死了 `PROPORTIONS ARE CRITICAL`、`no stretching, squashing or foreshortening`、`the detail studies give way, not the figures`。**拿到图先量一眼三个全身像是不是等高、头身比正不正常。**

### 左栏的收口 ⚠️

模型默认会把半身像的两侧肩膀裁掉、底边做成圆角或渐隐晕影，看着很别扭。提示词里必须显式禁掉：肩膀完整、两侧留空、底边齐平直切。这条不写就一定会出问题。

### 面部一致性 ⚠️

一张图里出现两个长相是这个版面最容易出的问题——左栏画一个人、右栏画另一个人。提示词里必须写死 `must match the bust portrait exactly — same features, same hairstyle, same expression`。拿到图先扫一眼两边是不是同一个人，不是就标失败；仅在已有重试授权范围内重生成。`image.sheet` 的提示词里已经写死了 `plain pure white background`，不要改成灰底或场景背景。

## 文件与复用

用现有 `slug` 命令生成安全文件名。`render` 查找 `images/<slug>-sheet.png`；只在本次输出目录放来源与上游 pins 已核对且视觉验收过的图像。旧文件存在不代表当前成图；缺失就渲染占位。透明背景须核对实际 provider 能力，不作支持承诺。
