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

### 同一种节点，按交付目标选提示词

- 人像与含三视图的设定板都是 `image_prompt`，不是两种卡片类型。
  默认“角色设定/三视图”使用 `image.sheet`；仅明确的肖像需求用 `image.prompt`。
  不把半身像 prompt 中的 `Three-quarter view` 当成 three views，也不拼接两种互相冲突的构图指令。
- 创建或更新时，将完整 `image.sheet` 放进节点 `prompt`，不是只留在 JSON/brief。
  用 `seedance_read_canvas` 全量详情回读，核对节点 prompt 与本次 sheet 一致；16:9 仅是宽高比。
- 已有角色设定卡时，原卡、prompt、图片和模型设置保持不动。另建同类型 `image_prompt` 卡，
  标题为“角色名 · 三视图”，prompt 使用完整 `image.sheet`，data.turnaroundSourceNodeId 填原角色卡 ID。
  通过 `connect` 建立“原角色卡 -> 三视图卡”的 `references` 连线。不要把原图 activeOutputRef 复制给新卡冒充成图。
  原卡无图或人像未确认时先明确缺失项，不把纯文字连线当图片参考。无原卡时可以直接建立三视图卡。
  同一角色已有对应三视图卡时复用该三视图卡，更新带 expected_revision，禁止重试重复创建。
  V3 的统一生成入口解析真实参考图并传给 workers，Cine 只指定三视图节点与创作提示词，不自行组装参考图参数。
  模型不支持参考图时必须停止，不能丢参考图或擅自换模型。
- 预检和提交都用 `generation_kind="image"`、`production_stage="cast"`、
  `production_pointer="/characters/0/image/sheet"`（索引按实际角色）。
  `production_dir` 可直接含 `cast.json` 或唯一的 `<title>-cast.json`，工具原地读取，不复制改名。
  角色图只校验角色资料和来源文本，不要求补 `outline.json` 或整集剧情质量门；多个候选文件需先明确选用资料，禁止猜测。
  `source_text` 指向真实的已批准文本。预检失败先按错误修复，禁止把错误说成缺少生图授权。
- 更新卡片和预检不提交生图；提交需已有范围授权，不重复索取同一授权，也不默认授权包含额外重试。
- 交付前重新读取画布：节点 `status=draft` 不代表没图；`activeOutputRef` 存在表示有关联输出，
  不证明它符合当前 prompt。实际查看成图，核验正/侧/背三个全身视角、身份服装一致和无遮挡比例。
  本地 HTML 缺 `images/<slug>-sheet.png` 只能称“本地报告缺图”，不能称“画布仍是占位”。

### 最短工具流程

1. 加载本 skill，一次读取角色 JSON、目标节点完整信息；可用 `shot_ids=[实际 node_id]` 缩小回读范围。
2. 已有角色设定卡则保持不动，另建三视图卡并连接原卡作为参考；已有匹配三视图卡则更新它。
   核对返回的 verified node，通常不必再读整个画布。
3. 模型未指定时用 `seedance_read_canvas(action="models")` 查询 V3 生成目录，一次确认选择。
   节点已有 `data.model` 则复用。目录注册不等于账号登录、额度或 worker 健康，不承诺免费。
4. 已有生成授权则直接 `submit_generation`，工具自动校验角色 JSON、来源、节点 prompt 和模型。
   只有用户要求先检查不出图时才单独 `validate_generation`。无需手动 check 后再多次预检。
5. 用返回的 job.id 调 `seedance_read_canvas(action="job", job_id=...)`，遵循 poll_after_seconds，
   等待结束后实际看图验收；失败只修正明确输入，不扫描代码、猜接口或现场写脚本。
   terminal=true 后停止轮询。全部目标任务结束后，一次验收并交付；本地报告导出或链接检查失败单独说明，不拖住图片交付。
   画布摘要的 output_url/latest_job 来自 V3 统一解析。activeOutputRef 缺失不等于缺图，禁止手工补字段或猜 revision 来“修好状态”。

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

任务成功后，通过 `seedance_edit_canvas(action="export_image", job_id="<真实任务ID>", output_path="outputs/images/<slug>-sheet.png")` 取得本地图片，再用返回的 path 调用 `sys_os_view_image`。无须也不得扫描硬盘寻找 hash 文件或猜 API 地址。格式不匹配时按工具返回的实际扩展名处理，不能把 JPEG 字节冒充 PNG。

用现有 `slug` 命令生成安全文件名。`render` 查找 `images/<slug>-sheet.png`；只在本次输出目录放来源与上游 pins 已核对且视觉验收过的图像。旧文件存在不代表当前成图；缺失就渲染占位。透明背景须核对实际 provider 能力，不作支持承诺。
