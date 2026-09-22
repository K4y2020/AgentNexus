---
name: cine-camera-evidence
version: 1.0.0
description: |
  影视运镜判例与动作视听参考库：从真实生产语料中检索机位、景别、焦段、运镜与动作原子的组合规律。
  为分镜设计（cine-storyboard）与提示词优化提供专业视听语料支撑，涵盖 CQC、冷兵器、机甲、飞车等跨题材动作原子库。
  零外部依赖，本地 Node 18+ 毫秒级 SQLite/FTS5 检索。
  Use when asked to 运镜参考、找机位、机位参考、镜头参考、动作原子、camera prompt evidence。
allowed-tools:
  - Read
  - Write
  - Bash
  - Glob
triggers:
  - cine-camera-evidence
  - 运镜参考
  - 镜头参考
  - 机位参考
  - 动作原子
  - 分镜知识库
  - camera evidence
  - shot pattern
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

## cine-camera-evidence

### 1. 技能定位与核心价值

给 Cine 的剧本（`cine-script`）与分镜（`cine-storyboard`）提供**真实工业界视听提示词判例与镜头调度语法**参考。
检索库沉淀了来自公开生产语料的 26,000+ 条真实生成提示词，并经过去噪、打分与多维属性标引。

**核心原则：借鉴视听句式与运镜节奏，严禁照搬实体设定与剧情台词。**

---

### 2. 命令行检索入口

在当前工作区直接调用零依赖 Node 脚本执行检索：

```bash
# 1. 基础关键词与运镜筛选 (毫秒级响应)
node examples/cine/camera-evidence/cli/query.mjs --query "推镜" --motion push-in --limit 3

# 2. 自然语言分镜意图解析与动作原子检索
node examples/cine/camera-evidence/cli/query.mjs --brief "低机位推镜，主角在暴雨中拔刀力劈" --atoms

# 3. 按景别、机位与焦段组合检索
node examples/cine/camera-evidence/cli/query.mjs --shot close-up --angle low-angle --focal-band wide --limit 5

# 4. JSON 格式输出 (供脚本或 Agent 管道自动化消费)
node examples/cine/camera-evidence/cli/query.mjs --brief "特工侧步切入闪避，折腕夺枪" --atoms --format json
```

---

### 3. 支持的镜头与视听维度

- **运镜类型 (`--motion`)**：
  `push-in` (推镜), `pull-out` (拉远), `pan-left` (左摇), `pan-right` (右摇), `tilt-up` (仰摇), `tilt-down` (俯摇), `orbit` (环绕), `tracking` (跟拍), `static` (固定), `handheld` (手持呼吸感), `dolly-zoom` (滑动变焦), `crane` (升降), `aerial` (航拍)
- **景别分类 (`--shot`)**：
  `extreme-close-up` (大特写), `close-up` (特写), `medium-close-up` (中近景), `medium-shot` (中景), `full-shot` (全景/全身), `wide-shot` (大远景/广角), `extreme-wide-shot` (大全景)
- **机位视角 (`--angle`)**：
  `eye-level` (平视), `low-angle` (仰视/低机位), `high-angle` (俯视/高机位), `overhead` (垂直顶拍), `dutch-angle` (倾斜/荷兰角)
- **焦段范围 (`--focal-band`)**：
  `wide` (<=35mm 广角冲击力), `normal` (36-60mm 人眼自然透视), `portrait` (61-100mm 浅景深人物特写), `telephoto` (101mm+ 空间压缩与远摄)
- **动作题材领域 (`detectActionDomain`)**：
  `wuxia_cold_weapon` (冷兵器武打), `modern_cqc` (近身格斗/特工夺枪), `car_chase` (飞车追逐/过弯甩尾), `mech_scifi` (机甲科幻/光束折跃), `magic_fantasy` (奇幻魔法/奥术法阵)

---

### 4. 与 cine-storyboard 的协同

在制作分镜表或编写 H3 提示词时：
1. 先根据分镜节拍（Beat）的叙事冲突，用 `--brief` 检索判例和动作原子；
2. 提取判例中的**镜头动作时序句式**（例如：“起幅固定平视中景，随动作爆发快速切入低机位推近特写”）；
3. 结合场景参考卡与角色状态卡，将该运镜结构填入 `storyboard.json` 的 `cuts[].frame` 与 H3 视频提示词段落中。
