# 案例1

采用 adaptation：单集30秒，科幻救援，人物全部重设。先保留8/12/10秒的区间骨架；现有证据不足以确认原片内部剪切、运动及声音节奏，不能称已完整保留节奏。以下为原创改编提案，映射中的叙事功能属于新片设计。

1. [0,8)：救援员停在气闸前，观察门缝透出的求救灯光，建立等待。
2. [8,14)：救援员接通应急电源，舱门仍不响应，拉长受阻感。
3. [14,20)：改用手动泄压，门终于打开，救援员接住伤员。
4. [20,30)：两人抵达屋顶撤离点，展开反光救援膜，等待接驳机；最后停留在相互扶持的身影。

来源账保留：S001仅“穿黑白服装的女子望向门口”；S002无图片回执，观察留空；S003仅“女子在屋顶，旁边有猫和纸形物体”。均不据此认定老六身份、对白或动作。猫不继承为新人物，救援膜也不是对原物体的辨认。

映射样例：拟定剧本三场，节拍数1/2/1；第二场两切各6秒。不是已验证的完整产物。

```json
[
  {"ep":1,"segment_id":"E01-01","cut":1,"scene_index":1,"beats":[1,1],"kind":"adapted","source_shot_ids":["S001"],"narrative_function":"等待与观察","creative_change":"门口改为气闸；求救灯原创"},
  {"ep":1,"segment_id":"E01-02","cut":1,"scene_index":2,"beats":[1,1],"kind":"adapted","source_shot_ids":["S002"],"narrative_function":"救援受阻","creative_change":"来源未核验草稿，仅借时间位置；接电情节原创，交接阻塞"},
  {"ep":1,"segment_id":"E01-02","cut":2,"scene_index":2,"beats":[2,2],"kind":"adapted","source_shot_ids":["S002"],"narrative_function":"救出伤员","creative_change":"来源未核验草稿；泄压与伤员原创，交接阻塞"},
  {"ep":1,"segment_id":"E01-03","cut":1,"scene_index":3,"beats":[1,1],"kind":"adapted","source_shot_ids":["S003"],"narrative_function":"撤离后的等待","creative_change":"屋顶转为撤离点，人物与救援膜原创"}
]
```

顺序计划：①取得绑定项目当前修订与证据索引；②用 `sys_os_view_image(path,evidence_index,evidence_id)` 补看S002，核对真实回执，复查相邻帧并播放片段/音频核验节奏；工具缺失则请求图片/片段，保持未核验；③运行 `python <K>/film-analysis/pipeline/handoff.py --source-project <绑定项目>`；④依次完成outline→cast/art→script→storyboard及映射，再原生校验、共享检查。

状态：改编草稿可用；来源节奏核验与带S002的交接受阻。本轮仅据题目证据，无新增看图回执。

# 案例2

状态：暂缓同步。五份各自validate通过不能证明使用同一上游；缺历史hash也不等于永久失效。以今日大纲重新复核后建立基线。

顺序计划：①只读取得五文件与今日合角决定；②逐项检查C02残留、C01性格/声线/关系，剧本说话人和同场角色，分镜人数、节拍、参考连线；同时复核art的人名禁词、场景/光照/道具；不能机械替换ID；③输出“受影响ID、证据、建议修正”，由Cine修订候选文件；④针对当前上游重跑：

```text
node <K>/novel-outline/scripts/novel-outline.mjs validate <outline>
node <K>/novel-characters/scripts/novel-characters.mjs validate <cast> <原文> --lang zh
node <K>/novel-art/scripts/novel-art.mjs validate <art> --cast <cast>
node <K>/novel-script/scripts/novel-script.mjs validate <script> --outline <outline> --art <art>
node <K>/novel-storyboard/scripts/novel-storyboard.mjs validate <storyboard> --script <script> --outline <outline> --cast <cast> --no-log
Get-FileHash -Algorithm SHA256 -LiteralPath <各文件实际路径>
python <K>/film-analysis/pipeline/handoff.py <production.json>
```

⑤复核完成才把真实hash及直接上游pins写sidecar，包含来源/映射（适用时），不改五份schema；⑥后续获准联网时 `seedance_read_canvas` 核对绑定项目、revision、节点和真实资产ID，仅制作差异清单，不调用edit。QA绑定文件hash及画布revision，在只读快照进行；`seedance_agent_message` 无强制只读隔离，不能用于此次QA。

阻塞：当前文件、历史依据及画布快照未提供，检查尚未执行。保持 `generation_allowed=false`，不submit；本任务不修改现有画布。共享成功仅为 `handoff_validated`。

# 案例3

保留12秒单段单切、固定中景、无台词。人物维持剧本规定的位置和停顿，允许自然呼吸；不添加情节动作。环境声沿用剧本，未指定则待定，无台词不自动等于静音。

顺序计划：①核对剧本场次及连续节拍、实际参考图；②seed该集，按原生结构填一段一切，认领真实节拍范围；设置现有 `params.minCutSeconds=12`、`maxCutSeconds=12`、`maxSegmentSeconds=15`，不填写假h3Prompt；③用上述分镜validate命令记录诊断，再render评审草稿；④交付以下中立计划，等待Seedance适配器及真实上传/引用能力。

```json
{
  "status":"planning-only",
  "provider":"Seedance",
  "duration_seconds":12,
  "continuous":true,
  "cut_times":[],
  "prompt":"固定中景，连续十二秒不切镜。沿用剧本人物、场景、光照和姿态，完整保留停顿与自然呼吸，不新增情节动作，无台词。",
  "reference_local_path":null,
  "reference_note":"题目未给路径字面值；后续填用户已有真实本地图，不冒充已看图",
  "remote_asset_id":null,
  "generation_allowed":false,
  "pending_checks":["实际剧本节拍绑定","参考图视觉核验","上传返回真实ID","Seedance接口及12秒能力","生成授权","实际成片时长与连续性QA"]
}
```

脚本能查节拍覆盖、引用、配置时长、台词容量及提示词结构；不能证明停顿表演、参考图一致性、真实成片时长或Seedance兼容。短剧钩子/风格门不适用则留诊断。缺H3字段的原生校验不能报全通过；现有export只出H3包，Seedance可执行导出blocked，不编造provider参数。参考图未上传，远程引用亦阻塞；无需生成即可交付此计划。

以上均为未执行计划。`<K>`为 `U:/AI/MultiAgent/agentnexus/.codex-tmp/cine-workflow-ab/round2-skills`，其他尖括号为待绑定路径。本轮只读六份SKILL及相关引用，仅写本文件；无网络、生成或Bot调用。
