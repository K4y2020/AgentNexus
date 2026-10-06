# 案例答复

以下为方案与待执行计划；本次仅读取指定六份技能及相关引用，未运行校验、联网、调用 Bot、修改画布或生成素材。R 指指定 optimized-skills 绝对目录，D 指未来独立制作目录；命令中的占位路径需替换。

## 1. 30秒科幻救援改编

采用 adaptation：保留已给的8/12/10秒时段，重建人物与救援剧情。只能承诺时长骨架；运动、对白、声画重音未核验，不能称完整复现原片节奏。以下均为原创提案。

1. [0,8)：救援员停在气闸外，发现舱内求救灯闪烁，建立救援目标。
2. [8,14)：气闸失压，救援员将供氧管接入舱内。
3. [14,20)：同一镜头内等待压力回升，舱内伸出的手握住扶栏。
4. [20,30)：塔顶撤离平台，两名宇航员站稳；救援员收起信标，留下脱险后的余韵。

三镜承载四拍，14秒处不新增剪切。S001仅保留“穿黑白服装的女子望向门口”的观察；“发现求救”是新剧情。S002仍未核验，不据路径推断内容，中段明确独立原创。S003仅保留“女子在屋顶，旁边有猫和纸形物体”；不认定老六身份，不把纸形物体命名为原片信标。

映射草案，每行对应一个新切；叙事功能均属改编解释，非原片事实：

```json
[
  {"ep":1,"segment_id":"E01-01","cut":1,"scene_index":1,"beats":[1,1],"kind":"adapted","source_shot_ids":["S001"],"narrative_function":"朝门口的视线引导注意","creative_change":"新设救援员与气闸求救灯，8秒"},
  {"ep":1,"segment_id":"E01-02","cut":1,"scene_index":2,"beats":[1,2],"kind":"new","source_shot_ids":[],"narrative_function":"救援操作与等待","creative_change":"独立原创12秒；只借已给时段，不声称重建S002"},
  {"ep":1,"segment_id":"E01-03","cut":1,"scene_index":3,"beats":[1,1],"kind":"adapted","source_shot_ids":["S003"],"narrative_function":"高处空间作为收尾","creative_change":"新设塔顶撤离平台与两名宇航员，10秒"}
]
```

下一步顺序：①绑定来源项目，以 `sys_os_view_image` 核验S002及相邻证据帧，工具不可用则等待图片附件；播放片段核对剪切、运动和声音。②运行 `python R/film-analysis/pipeline/handoff.py --source-project <绑定项目>`，保留未知项，不编造回执。③方案确认后依次形成 outline→cast/art→script→storyboard，映射落到真实节拍；切长配置覆盖8—12秒。④原生校验后检查交接。

状态：创意草案完成；来源节奏核验、真实来源版本与回执、实际制作文件尚缺，不能宣称交接通过。

## 2. 同步前交接检查

结论：暂停同步。五份独立 validate 通过不能证明共同版本有效；无旧 pins 无法追溯昨天消费的上游。

有序计划：

1. 获取五份文件的只读快照，用 `Get-FileHash -Algorithm SHA256 -LiteralPath <文件>` 逐份计算当前字节哈希；这不是历史版本证明。
2. 逐字段核查 C02→C01：cast 的身份、别名与关系；script 的出场、说话人及合并后是否自说自答；storyboard 的画内人物、节拍覆盖和参考资产。art 的角色禁名、场景、光照、道具关联也复核。形成“问题/受影响ID/证据/修正建议”清单。
3. Cine在独立修订副本处理影响，按顺序运行：

```text
node R/novel-outline/scripts/novel-outline.mjs validate D/outline.json
node R/novel-characters/scripts/novel-characters.mjs validate D/cast.json <来源文本> --lang zh
node R/novel-art/scripts/novel-art.mjs validate D/art.json --cast D/cast.json
node R/novel-script/scripts/novel-script.mjs validate D/script.json --outline D/outline.json --art D/art.json
node R/novel-storyboard/scripts/novel-storyboard.mjs validate D/storyboard.json --script D/script.json --outline D/outline.json --cast D/cast.json --no-log
python R/film-analysis/pipeline/handoff.py D/production.json
```

4. 最后一条执行前，复核完成再将真实哈希及全部直接上游 pins 写入 sidecar，并检查逐切 mapping；不靠刷新 pins 消除过期。原生文件不加合同字段。
5. 后续允许连接时，仅 `seedance_read_canvas` 获取绑定项目、revision与节点/资产ID，在只读快照上出差异单。当前不调用 `seedance_edit_canvas`、`seedance_agent_message` 或 `submit_generation`，`generation_allowed=false`。泛用Agent无强制只读隔离，不能充当安全QA入口。

状态：同步阻塞，未实跑；缺文件快照、来源文本、版本重建及资产核验。共享检查成功也仅为 handoff_validated，不是制作验收。未来更新须带新读到的 expected_revision，冲突重读；本次不修改画布。

## 3. Seedance的12秒停顿镜头

保留单段单镜12秒、无对白，不添加钩子或强制切镜。沿用剧本人物站位和场景光照，固定中景，完整保留等待及自然呼吸；只用环境底声，不另添剧情。以下为局部结构示例，人物/节拍编号须与实际剧本对齐：

```json
{
  "params":{"maxSegmentSeconds":15,"minCutSeconds":12,"maxCutSeconds":12},
  "episodes":[{"ep":1,"segments":[{"id":"E01-01","sceneIndex":1,"cuts":[{"beats":[1,1],"seconds":12,"size":"medium","camera":"Static Shot","characters":["C01"],"props":[],"frame":"Medium shot, cinematic film still. Preserve the approved character blocking, environment and lighting.","note":"连续12秒，无剪切、对白或新增动作"}]}]}]
}
```

计划：①读取剧本及实际参考图，核对节拍和资产来源。② `seed <script.json> --eps 1` 后填上述结构；③运行案例2的分镜 validate，交付诊断及JSON/Markdown草稿。④待真实Seedance适配器可用，核验时长与参考图契约，通过已存在的上传能力取得 asset_id 再绑定；获得生成授权后才提交。

现有校验能查节拍覆盖、枚举、引用、计划时长和台词容量；剧本动作按 actionSeconds 统一估时，不能验证真实停顿长度。独立12秒单动作样例可设 actionSeconds=12，多镜项目不能全局改值凑数。

状态：结构方案可用；缺 h3Prompt 的原生校验不能称全过，短剧门不兼容保留诊断，未挂镜头库则配方门跳过。现有 export 仅支持H3，Seedance投产导出 blocked，不伪填H3字段或发明 provider 参数。上传能力/真实asset_id、生成授权均缺；本地路径可供核验后渲染，不能冒充画布资产。脚本不能证明Seedance兼容、画面连续、实际时长或视觉质量，须生成后看图看片验收。
