# 三个案例交付

以下仅为方案与样例；未执行校验、上传、Bot、画布或生成调用。已读指定六份技能及 storyboard/schema、storyboard/frame、script/schema 引用。命令中的 B 指指定 baseline/skills，文件名指待交接文件，路径执行前须替换。

## 1. 30秒科幻救援

按1集×30秒、借节奏原创处理，不继承原角色。现有证据只能支持8/12/10秒的时间骨架，不能声称完整保留原片运镜、声音与内部节奏。

四拍：①[0,8)救援员守在气闸前，等待失联舱信号；②[8,14)发现氧气将尽，手动接通备用气路；③[14,20)舱门开启，扶出被困技师；④[20,30)两人在平台等待接驳灯亮起，确认获救。②③属于同一12秒段内的叙事节拍，不要求14秒切镜。

映射示例（自定义规划记录，非源账本或调度请求）：

```json
[
  {"sourceShot":"S001","sourceInterval":[0,8],"targetShot":"N01","targetInterval":[0,8],"evidence":"用户提供已看图观察：黑白服女子望向门口","adaptation":"原创救援员等待气闸信号","motionVerified":false},
  {"sourceShot":"S002","sourceInterval":[8,20],"targetShot":"N02","targetInterval":[8,20],"evidence":"仅本地路径，无图片回执","adaptation":"原创接气与救出两拍","visualStatus":"unverified"},
  {"sourceShot":"S003","sourceInterval":[20,30],"targetShot":"N03","targetInterval":[20,30],"evidence":"用户提供已看图观察：屋顶女子、猫、纸形物体","adaptation":"原创平台接驳收束","identity":"老六未确认"}
]
```

下一步顺序：1.取得当前项目、revision及证据索引；2.可用时以真实 path/evidence_index/evidence_id 调用 sys_os_view_image，补看S002及相邻帧；无工具则请求图片附件；3.看短片、听音频核验边界和节奏，再单独记录观察与真实回执；4.据此修订新分镜时长。不得编造回执、把猫认作老六，或删除shot_ids绕过派发门。

状态：原创四拍可讨论；用户报告已看2镜、未核验1镜，本轮实际看图0镜。源片忠实节奏及源镜派发被证据不足阻断，原创设计可继续。

## 2. 同步前交接

结论：暂不可同步。各自PASS不能证明同一版本闭环；C02合并影响角色卡、台词归属、同框人数及参考图绑定。

顺序计划：

1. 对五文件计算当前SHA256，登记校验输入、时间与工具版本；这些新hash不能追溯证明昨天的来源。结构化检查所有C02引用，并检查C01合并后的性格、声线、对白与画面是否一致；同时核对场景、光照、道具及节拍覆盖。art也需复核，不能默认最新。
2. 用同批文件重跑以下检查；本轮没有实际文件，不能宣称结果。

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath outline.json,cast.json,art.json,script.json,storyboard.json
node <B>/novel-outline/scripts/novel-outline.mjs validate outline.json
node <B>/novel-characters/scripts/novel-characters.mjs validate cast.json source.txt --lang zh
node <B>/novel-art/scripts/novel-art.mjs validate art.json --cast cast.json
node <B>/novel-script/scripts/novel-script.mjs validate script.json --outline outline.json --art art.json
node <B>/novel-storyboard/scripts/novel-storyboard.mjs validate storyboard.json --script script.json --outline outline.json --cast cast.json --no-log
```

3. 可用时先调用只读 seedance_read_canvas，记录revision、节点ID、连线、资产绑定，产出待新增/更新/保留差异表。Agent仅质检：若委托seedance_agent_message，须先确认其契约支持严格只读，明确task仅返回问题、shot_ids用真实ID、generation_allowed=false；禁生成不等于禁改画布，不支持只读则跳过Agent。
4. 由文件负责人完成合并后的下游修订，再重检并冻结hash。当前不调用seedance_edit_canvas，也不提交生成。

状态：交接阻断；缺实际文件、历史依赖记录、画布快照和资产状态。原文缺失时引文校验也须标未执行。平台在线不代表工具可用或已有额度授权。

## 3. 12秒无切停顿

保留独立短片节奏：一个段、一个cut、12秒、无台词。按现有剧本姿态固定中景，维持场景参考光向；只保留自然呼吸与环境声，不添加反转、对白或切镜。

结构片段（beats暂按本场第1拍示意，交接时绑定真实范围）：

```json
{"params":{"maxSegmentSeconds":15,"minCutSeconds":2,"maxCutSeconds":12},"segment":{"id":"E01-01","sceneIndex":1,"cuts":[{"beats":[1,1],"seconds":12,"size":"medium","camera":"Static Shot","frame":"cinematic film still, medium shot, preserve the approved scene composition and lighting"}]}}
```

顺序：1.核对实际节拍、人物道具与总时长；2.用上述参数运行分镜validate（带真实script，--no-log），列出H3相关未满足项；3.交付独立Seedance待绑定清单，不把H3 export冒充Seedance包；4.未来确认上传接口后上传参考图，用真实回执asset_id替换占位，获得生成授权才提交。

```json
{"kind":"planning-only-not-api-payload","target":"Seedance","durationSeconds":12,"continuous":true,"dialogue":[],"prompt":"固定中景，连续12秒不切镜；保持剧本既定姿态与场景光线，仅自然呼吸，环境声，无对白、无配乐。","reference":{"localPath":"<用户场景图路径>","asset_id":null},"generation_allowed":false}
```

脚本可查节拍覆盖、秒数范围、引用和提示词字面规则；剧本动作默认按2.5秒/拍估算，不能验证真实停顿时长。爽点/钩子门若与本片冲突，注明不适用，不补假情节。分镜仍有H3专属门，export产出H3提示词及Picture清单，不能验证Seedance请求、上传绑定、模型时长支持或最终画面连续性；镜头配方门未挂载则跳过。

状态：长镜头方案可交接，示例非完整PASS文件；资产未上传、Seedance契约未核验、生成未授权，不能投产。
