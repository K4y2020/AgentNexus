#!/usr/bin/env node
// cine-storyboard 自测：不调模型、不花额度，只验确定性逻辑。
// 原则与仓库里其他 skill 一致：每道质量门都要有击穿用例——
// 证明它真的会拦，不是一个永远为真的假测试。

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { artworkMedium } from '../../_shared/cine-common.mjs';
import {
  CAMERA_MOVES,
  castView,
  continuityReport,
  DEFAULT_PARAMS,
  DEFAULT_STYLE,
  STYLE_PRESETS,
  exportPack,
  H3_I2VA_LINE,
  SHOT_SIZES,
  computeStats,
  cutStarts,
  expandScript,
  GATE_LOG,
  gateLogEntries,
  gateReport,
  identifySubject,
  entityTerms,
  ref2vaReferenceProblems,
  subjectDefinitions,
  summarizeGateLog,
  h3AlignmentLine,
  h3CutSlices,
  h3CutTime,
  h3Remainder,
  loadRecipes,
  parseCardFields,
  paramsOf,
  recipeDrift,
  renderHtml,
  renderMarkdown,
  seedFromScript,
  segSeconds,
  slug,
  validateStoryboard,
} from './cine-storyboard.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const FIXTURE = JSON.parse(readFileSync(join(here, '../examples/渡口-storyboard.json'), 'utf8'));
// 同一集的关键帧模式版本（首帧对齐行，Picture k = 第 k 镜的分镜图）：独立导出投产包用
const KEYFRAMES = JSON.parse(readFileSync(join(here, '../references/test-fixtures/渡口-storyboard-keyframes.json'), 'utf8'));
const SCRIPT = JSON.parse(readFileSync(join(here, '../../cine-script/examples/渡口-script.json'), 'utf8'));
const OUTLINE = JSON.parse(readFileSync(join(here, '../../cine-outline/examples/渡口-outline.json'), 'utf8'));
const CAST = JSON.parse(readFileSync(join(here, '../../cine-characters/examples/渡口-cast.json'), 'utf8'));
const ART = JSON.parse(readFileSync(join(here, '../../cine-art/examples/渡口-art.json'), 'utf8'));
const CTX = { script: SCRIPT, outline: OUTLINE, cast: CAST, art: ART };

let passed = 0;
function ok(cond, label) {
  assert.ok(cond, label);
  passed += 1;
}
function eq(actual, expected, label) {
  assert.equal(actual, expected, `${label} — 期望 ${expected}，实际 ${actual}`);
  passed += 1;
}
const clone = (x) => structuredClone(x);
const gate = (doc, id, ctx = CTX) => gateReport(doc, ctx).find((g) => g.id === id);

/* ---------------- expandScript ---------------- */

const expanded = expandScript(SCRIPT);
eq(expanded.size, 6, '剧本六集全部展开');
const e1 = expanded.get(1);
eq(e1.scenes.length, 2, '第 1 集两场');
eq(e1.scenes[0].beats.length, 13, '第 1 场 13 拍');
eq(e1.scenes[1].beats.length, 22, '第 2 场 22 拍');
eq(e1.scenes[0].beats[0].kind, 'action', '第 1 拍是动作');
eq(e1.scenes[0].beats[0].seconds, 2.5, '动作按 2.5 秒计');
eq(e1.scenes[0].beats[2].speaker, 'C03', '台词带说话人');
eq(e1.targetSeconds, 120, '目标秒数带出来');
eq(expandScript(null).size, 0, '空剧本不崩');

/* ---------------- H3 骨架推导 ---------------- */

eq(h3CutTime(3), '00:03.000', '切点时刻格式 分:秒.毫秒');
eq(h3CutTime(6.5), '00:06.500', '半秒切点');
eq(h3CutTime(65), '01:05.000', '过分钟进位');
eq(cutStarts([{ seconds: 3 }, { seconds: 4 }, { seconds: 3 }]).join(','), '0,3,7', '切点 = 前面分镜秒数累计');
eq(h3AlignmentLine([{ seconds: 5 }]), H3_I2VA_LINE, '默认英文：单分镜的段用官方 I2VA 固定句式');
eq(h3AlignmentLine([{ seconds: 5 }], 'zh'), '目标视频在 0.00 秒处完全参照图 1（来自镜头 1）。', 'zh 模式有中文 I2VA 句式');
{
  const line = h3AlignmentLine([{ seconds: 3 }, { seconds: 4 }]);
  ok(line.startsWith('How the reference pictures align with the target video — '), '默认英文：多分镜用官方对齐句式');
  ok(line.includes('Picture 1 (from Shot 1) aligns with the 0.00-second mark'), '主分镜图钉 0.00 秒');
  ok(line.includes('Picture 2 (from Shot 2) aligns with the 3.00-second mark'), '子分镜图钉自己的切点');
  ok(h3AlignmentLine([{ seconds: 3 }, { seconds: 4 }], 'zh').includes('图 2（来自镜头 2）对齐目标视频 3.00 秒处'), 'zh 模式对齐句式可用');
}
{
  const slices = h3CutSlices('integrated_multimodal_description:\n[Shot 1] aa\n[Shot 2] bb\n\noverall_soundscape: x', 2);
  ok(slices[0].includes('aa') && !slices[0].includes('bb'), '[Shot k] 切片互不越界');
  ok(slices[1].includes('bb') && !slices[1].includes('x'), '切片不吃到声景字段');
  const zh = h3CutSlices('整体视听描述：\n[镜头 1] aa\n[镜头 2] bb\n\n整体音景：x', 2, 'zh');
  ok(zh[0].includes('aa') && zh[1].includes('bb'), 'zh 模式按 [镜头 k] 切片');
  const ref2va = h3CutSlices('subject_definitions:\na\nsummary:\nb\nretention_analysis:\nc\ndetailed_description:\n[Shot 1] aa\n[Shot 2] At 00:03.000, bb\noverall_soundscape: x\nnon_diegetic_music: y', 2);
  ok(ref2va[0].includes('aa') && !ref2va[0].includes('bb'), 'Ref2VA 从 detailed_description 按镜头切片');
  ok(ref2va[1].includes('bb') && !ref2va[1].includes('overall_soundscape'), 'Ref2VA 镜头切片不吃到声景字段');
}
eq(segSeconds({ cuts: [{ seconds: 3 }, { seconds: 4.5 }] }), 7.5, '段秒数 = 分镜求和');

/* ---------------- computeStats ---------------- */

const stats = computeStats(FIXTURE, SCRIPT);
eq(stats.totals.segments, 10, '样例十段');
eq(stats.totals.cuts, 34, '样例三十四个分镜');
eq(stats.totals.seconds, 119, '总秒数');
eq(stats.totals.targetSeconds, 120, '目标秒数');
ok(stats.totals.avgCutSeconds >= 3 && stats.totals.avgCutSeconds <= 4, '平均一切 3 秒左右——短剧节奏');
eq(stats.batches.length, 2, '两个生成批次（S02 浓雾清晨 / S01 晨雾）');
ok(stats.batches[0].segments.length + stats.batches[1].segments.length === 10, '批次覆盖全部段');
eq(stats.dialogue.length, 19, '第 1 集 19 句台词全部对到段和切');
ok(stats.dialogue.every((d) => /^E01-\d{2}$/.test(d.segment) && d.cut >= 1), '对齐单带段号和切序');
eq(stats.episodes[0].withLines, 10, '台词段计数——本样例每段都带台词，纯画面收在分镜级');
eq(paramsOf({}).maxSegmentSeconds, DEFAULT_PARAMS.maxSegmentSeconds, '默认段上限 15 秒');
eq(paramsOf({}).maxCutSeconds, 15, '默认单镜可覆盖完整 H3 生成段');
eq(paramsOf({ params: { maxSegmentSeconds: 20 } }).maxCutSeconds, 20, '单镜上限跟随提供商生成段上限');
eq(paramsOf({ params: { maxSegmentSeconds: 8 } }).maxCutSeconds, 8, '较短的生成段同时限制单镜');
eq(paramsOf({ params: { maxCutSeconds: 4 } }).maxCutSeconds, 4, '分镜上限可调');

/* ---------------- 台词必须落在认领它的镜头 ---------------- */
{
  const dialogueScript = {
    params: { charsPerSecond: 4.5, actionSeconds: 2.5 },
    episodes: [{ ep: 1, targetSeconds: 4, scenes: [{
      characters: ['C01', 'C02'], props: [], flow: [
        { line: 'A line', speaker: 'C01' },
        { line: 'B line', speaker: 'C02' },
      ],
    }] }],
  };
  const dialogueBoard = {
    promptLang: 'en',
    params: { minCutSeconds: 2, maxCutSeconds: 5, maxSegmentSeconds: 15, tolerance: 0.15 },
    episodes: [{ ep: 1, segments: [{
      id: 'E01-01', sceneIndex: 1,
      cuts: [
        { beats: [1, 1], seconds: 2, size: 'medium', camera: 'Static Shot', characters: ['C01'], frame: 'medium shot of C01 speaking', continuity: {} },
        { beats: [2, 2], seconds: 2, size: 'close', camera: 'Static Shot', characters: ['C02'], frame: 'close-up of C02 speaking', continuity: {} },
      ],
      h3Prompt: `${h3AlignmentLine([{ seconds: 2 }, { seconds: 2 }])}\nintegrated_multimodal_description:\n[Shot 1] medium shot of C01 speaking <d>[Chinese] B line </d>\n[Shot 2] At 00:02.000, close-up of C02 speaking <d>[Chinese] A line </d>\noverall_soundscape: room tone\nnon_diegetic_music: N/A`,
    }] }],
  };
  const dialogueGate = gateReport(dialogueBoard, { script: dialogueScript }).find((g) => g.id === 'h3-dialogue');
  ok(!dialogueGate.ok && dialogueGate.detail.includes('本镜 h3Prompt'), '台词错放到别的镜头会被逐镜拦截');
}

/* ---------------- 质量门：全绿基线 ---------------- */

ok(gateReport(FIXTURE, CTX).every((g) => g.ok), '样例带全部上游全部门通过');
eq(gateReport(FIXTURE, CTX).length, 32, '三十二道门');

/* ---------------- 连续性 A/B：旧模式放过，新合同指出断点 ---------------- */
{
  const bad = {
    continuityContractVersion: 1,
    episodes: [{
      ep: 1,
      segments: [{
        id: 'E01-01',
        sceneIndex: 1,
        cuts: [
          {
            characters: ['C01', 'C02'],
            props: ['P01'],
            continuity: {
              axisId: 'axis-01',
              screenPositions: { C01: 'left', C02: 'right' },
              eyelineTarget: 'C02',
              motionVector: 'left_to_right',
              propStates: { P01: 'on_table' },
              actionStates: { C01: { in: 'idle', out: 'reaching' }, C02: { in: 'watching', out: 'watching' } },
              emotionStates: { C01: { in: 'breath_held', out: 'reaching' }, C02: { in: 'watchful', out: 'watchful' } },
              establishing: true,
            },
          },
          {
            characters: ['C01', 'C02'],
            props: ['P01'],
            continuity: {
              axisId: 'axis-01',
              screenPositions: { C01: 'right', C02: 'left' },
              eyelineTarget: 'C02',
              motionVector: 'right_to_left',
              propStates: { P01: 'in_C01_hand' },
              actionStates: { C01: { in: 'standing', out: 'holding' }, C02: { in: 'watching', out: 'watching' } },
              emotionStates: { C01: { in: 'focused', out: 'holding' }, C02: { in: 'watchful', out: 'watchful' } },
            },
          },
        ],
      }],
    }],
  };
  const legacy = gateReport({ ...bad, continuityContractVersion: undefined }, {}).find((g) => g.id === 'continuity');
  ok(legacy.ok && legacy.detail.includes('跳过'), 'A：旧分镜没有连续性合同，兼容模式明确跳过');
  const strict = continuityReport(bad);
  const reasons = strict.advisories.join('；');
  ok(strict.enabled && strict.issues.length === 0 && strict.advisories.length >= 3, 'B：位置跳变、道具跳变和反向运动缺原因只提示，不拦截');
  ok(reasons.includes('positionChangeReason'), 'B：指出人物位置变化原因缺失');
  ok(reasons.includes('propChangeReason'), 'B：指出道具状态变化原因缺失');
  ok(reasons.includes('directionChangeReason'), 'B：指出运动方向变化原因缺失');
  const reasonGate = gateReport(bad, {}).find((g) => g.id === 'continuity-reasons');
  ok(reasonGate?.ok && reasonGate.advisory && reasonGate.detail.includes('positionChangeReason'), 'B：缺原因作为提示项列出，不算未过');
  const fixed = structuredClone(bad);
  Object.assign(fixed.episodes[0].segments[0].cuts[1].continuity, {
    positionChangeReason: 'C01 与 C02 在上一镜结束后完成换位',
    propChangeReason: 'C01 在镜内接过 P01',
    actionCarryReason: 'C01 从伸手动作连续完成抓取',
    emotionCarryReason: 'C01 看到 P01 后从屏息转为专注并完成抓取',
    directionChangeReason: '人物转身后反向走回桌边',
  });
  ok(continuityReport(fixed).advisories.length === 0, 'B+：明确写出变化原因后提示消失');

  const eyeBreak = structuredClone(bad);
  eyeBreak.episodes[0].segments[0].cuts[1].characters = ['C01'];
  delete eyeBreak.episodes[0].segments[0].cuts[1].continuity.actionStates.C02;
  eyeBreak.episodes[0].segments[0].cuts[1].continuity.eyelineTarget = 'offscreen';
  const eyeIssues = continuityReport(eyeBreak);
  ok(eyeIssues.advisories.join('；').includes('eyelineBreakReason'), 'B：视线目标未在下一镜兑现时提示说明切断原因');

  const sideOff = structuredClone(bad);
  sideOff.episodes[0].segments[0].cuts[0].continuity.eyelineTarget = 'offscreen_left';
  ok(!continuityReport(sideOff).detail.includes('不在本镜人物/道具中'), 'B：看向画外一侧（offscreen_left）是合法视线目标');
  sideOff.episodes[0].segments[0].cuts[0].continuity.eyelineTarget = 'window';
  ok(continuityReport(sideOff).detail.includes('可填本镜人物/道具 id、camera、offscreen'), 'B：非法视线目标的报错列出可填的值');

  const proseGap = structuredClone(bad);
  proseGap.episodes[0].segments[0].cuts[0].frame =
    'medium shot of C01 seizing C02 wrist while C02 looks toward C01';
  proseGap.episodes[0].segments[0].cuts[0].continuity.eyelineTarget = null;
  proseGap.episodes[0].segments[0].cuts[0].continuity.actionStates = {
    C01: { in: 'idle', out: 'idle' },
    C02: { in: 'idle', out: 'idle' },
  };
  const proseIssues = continuityReport(proseGap);
  ok(proseIssues.detail.includes('actionStates 全为 idle→idle'), 'B：画面动态动作不能被 idle 账本掩盖');
  ok(proseIssues.detail.includes('eyelineTarget 为空'), 'B：画面明确视线不能被空目标掩盖');

  const proseFixed = structuredClone(proseGap);
  Object.assign(proseFixed.episodes[0].segments[0].cuts[0].continuity, {
    eyelineTarget: 'C01',
    actionStates: {
      C01: { in: 'reaching', out: 'gripping_wrist' },
      C02: { in: 'standing', out: 'looking_at_C01' },
    },
  });
  const proseFixedIssues = continuityReport(proseFixed).detail;
  ok(!proseFixedIssues.includes('actionStates 全为 idle→idle')
    && !proseFixedIssues.includes('eyelineTarget 为空'), 'B+：账本与画面动作/视线对齐后不再触发新增门');
}

/* ---------------- 角色状态合同 ---------------- */

{
  const script = clone(SCRIPT);
  script.stateContractVersion = 1;
  for (const ep of script.episodes) for (const scene of ep.scenes) {
    scene.characterStates = Object.fromEntries(scene.characters.map((id) => [id, 'default']));
  }
  const board = clone(FIXTURE);
  board.stateContractVersion = 1;
  for (const ep of board.episodes) for (const seg of ep.segments) for (const cut of seg.cuts) {
    cut.characterStates = Object.fromEntries(cut.characters.map((id) => [id, 'default']));
  }
  ok(gate(board, 'character-states', { ...CTX, script }).ok, '逐镜 default 状态与剧本一致时通过');
  const first = board.episodes[0].segments[0].cuts[0];
  first.characterStates[first.characters[0]] = 'home_morning';
  const g = gate(board, 'character-states', { ...CTX, script });
  ok(!g.ok && g.detail.includes('状态应为 default'), '分镜状态与剧本场次不一致会被拦截');
}
{
  const gates = gateReport(FIXTURE, {});
  ok(gates.every((g) => g.ok), '不带上游也通过（对账门跳过）');
  ok(gates.find((g) => g.id === 'coverage').detail.includes('跳过'), '跳过要明说，不静默');
}

/* ---------------- 质量门：逐门击穿 ---------------- */

// coverage — 没人认领 / 重复认领 / 区间不合法 / 整场没分镜 / 顺序倒退
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].cuts[3].beats = [5, 5]; // 第 4 拍失去认领
  const g = gate(doc, 'coverage');
  ok(!g.ok, '有节拍没人认领被拦');
  ok(g.detail.includes('没人认领'), '点名到拍');
}
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].cuts[3].beats = [3, 5]; // 第 3 拍被两切认领
  ok(gate(doc, 'coverage').detail.includes('重复认领'), '重复认领点得出切号');
}
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].cuts[0].beats = [1, 99];
  ok(!gate(doc, 'coverage').ok, '节拍区间越界被拦');
}
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments = doc.episodes[0].segments.filter((s) => s.sceneIndex !== 1);
  ok(gate(doc, 'coverage').detail.includes('整场没有分镜'), '整场空白被拦');
}
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[1].sceneIndex = 2; // E01-03 变成场次倒退
  ok(!gate(doc, 'coverage').ok, '场次顺序穿插被拦');
}
// segment-cap
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].cuts[0].seconds = 15; // 段总秒数 27
  const g = gate(doc, 'segment-cap');
  ok(!g.ok, '段超 15 秒被拦');
  ok(g.detail.includes('E01-01'), '点名到段');
}
// cut-length
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].cuts[0].seconds = 1;
  ok(!gate(doc, 'cut-length').ok, '分镜短于 2 秒被拦');
}
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].cuts[0].seconds = 6;
  const g = gate(doc, 'cut-length');
  ok(g.ok, '6 秒镜头不因短剧节奏被迫切开');
  doc.episodes[0].segments[0].cuts[0].seconds = 16;
  ok(!gate(doc, 'cut-length').ok, '单镜仍不能超过当前生成段上限');
}
{
  const doc = clone(FIXTURE);
  doc.params = { maxCutSeconds: 3 };
  ok(!gate(doc, 'cut-length').ok, '上限收紧到 3 秒后原样例不再达标');
}
// dialogue-fit
{
  const doc = clone(FIXTURE);
  const seg = doc.episodes[0].segments.find((s) => s.id === 'E01-05');
  seg.cuts[0].seconds = 4; // 台词 4.4 秒
  const g = gate(doc, 'dialogue-fit');
  ok(!g.ok, '台词装不进分镜被拦');
  ok(g.detail.includes('E01-05#1'), '点名到切');
}
// ep-duration
{
  const doc = clone(FIXTURE);
  for (const s of doc.episodes[0].segments) for (const c of s.cuts) c.seconds = Math.min(5, c.seconds + 2);
  ok(gate(doc, 'ep-duration').detail.includes('超'), '写超总时长被拦');
}
{
  const doc = clone(FIXTURE);
  for (const s of doc.episodes[0].segments) for (const c of s.cuts) c.seconds = Math.max(2, c.seconds - 2);
  ok(gate(doc, 'ep-duration').detail.includes('欠'), '写欠总时长被拦');
}
// crowd
{
  const doc = clone(FIXTURE);
  const cut = doc.episodes[0].segments.find((s) => s.id === 'E01-09').cuts[2];
  cut.characters = ['C01', 'C02', 'C03', 'C04'];
  ok(!gate(doc, 'crowd').ok, '四人同框无拆解说明被拦');
  cut.note = '全景交代后立刻切正反打';
  ok(gate(doc, 'crowd').ok, '带拆解说明就放行');
}
// segment-id
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[3].id = 'E01-99';
  const g = gate(doc, 'segment-id');
  ok(!g.ok, '断号被拦');
  ok(g.detail.includes('E01-04'), '报出应有的段号');
}
// size-phrase
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].cuts[0].frame = 'a foggy pier at dawn, cinematic';
  ok(!gate(doc, 'size-phrase').ok, '分镜图提示词缺景别短语被拦');
  const canvas = { ...CTX, ref2va: true };
  ok(gate(doc, 'size-phrase', canvas).ok && gate(doc, 'frame-size-phrase', canvas)?.advisory,
    '画布模式下分镜图提示词到不了视频模型：缺景别短语只提示');
}
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].cuts[0].size = '大远景';
  ok(!gate(doc, 'size-phrase').ok, '景别不在枚举里被拦');
}
// camera-phrase
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].cuts[0].camera = '推';
  ok(!gate(doc, 'camera-phrase').ok, '运镜不在 H3 词表里被拦');
}
{
  const doc = clone(FIXTURE);
  const seg = doc.episodes[0].segments[0];
  seg.h3Prompt = seg.h3Prompt.replace('a tracking shot follows her', 'the camera follows her');
  const g = gate(doc, 'camera-phrase');
  ok(!g.ok, '运镜词没写进自己的 [Shot k] 段落被拦');
  ok(g.detail.includes('E01-01#1'), '点名到切');
}
{
  // 固定镜头的段落又写镜头在扫、在推：视频模型拿到的是自相矛盾的指令
  const doc = clone(FIXTURE);
  const seg = doc.episodes[0].segments[0];
  ok(seg.cuts[1].camera === 'Static Shot' && gate(doc, 'camera-phrase').ok, '样例的固定镜头照过');
  seg.h3Prompt = seg.h3Prompt.replace('Fog drifts between its pilings.', 'The camera slowly sweeps right along the pilings.');
  const g = gate(doc, 'camera-phrase');
  ok(!g.ok && g.detail.includes('E01-01#2') && g.detail.includes('camera slowly sweeps'), '固定镜头写了镜头移动被拦并点名');
}
{
  const doc = clone(FIXTURE);
  const seg = doc.episodes[0].segments[0];
  seg.h3Prompt = seg.h3Prompt.replace('Fog drifts between its pilings.', 'The camera holds while fog drifts between its pilings and a gull tracks the water.');
  ok(gate(doc, 'camera-phrase').ok, '镜头停住、画里的东西在动不算矛盾');
}
// h3-structure — 对齐指令由分镜结构推导，逐字对账
{
  const doc = clone(KEYFRAMES);
  doc.episodes[0].segments[0].cuts[0].seconds = 4; // 时长改了、提示词没跟着改
  ok(gate(doc, 'h3-structure').detail.includes('对不上'), '分镜秒数一改，旧对齐指令立刻对不上');
}
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].cuts[0].seconds = 4;
  ok(gate(doc, 'h3-structure').detail.includes('[Shot 2] At 00:04.000,'), 'Ref2VA：分镜秒数一改，切点时刻立刻对不上');
}
{
  const doc = clone(FIXTURE);
  const seg = doc.episodes[0].segments[0];
  seg.h3Prompt = seg.h3Prompt.replace('[Shot 2] At 00:03.000,', '[Shot 2] At 00:03.500,');
  const g = gate(doc, 'h3-structure');
  ok(!g.ok, '切点时刻和分镜秒数累计不一致被拦');
  ok(g.detail.includes('切点时刻'), '报出哪一处时刻错了');
}
{
  const doc = clone(KEYFRAMES);
  const seg = doc.episodes[0].segments[0];
  seg.h3Prompt = seg.h3Prompt.replace('overall_soundscape:', 'ambient_sound:');
  ok(gate(doc, 'h3-structure').detail.includes('核心字段'), '三字段缺失被拦');
}
{
  const doc = clone(FIXTURE);
  const seg = doc.episodes[0].segments[0];
  seg.h3Prompt = seg.h3Prompt.replace('retention_analysis:', 'retention:');
  ok(gate(doc, 'h3-structure').detail.includes('Ref2VA 六个字段'), 'Ref2VA 字段缺失被拦');
}
// 背景配乐由用户后期添加；关键帧和 Ref2VA 共用此生产门。
for (const fixture of [FIXTURE, KEYFRAMES]) {
  for (const value of ['Soft orchestral score.', 'None', '无', 'n/a', 'N/A.', 'N/A\nExtra instructions.', '']) {
    const doc = clone(fixture);
    doc.episodes[0].segments[0].h3Prompt = doc.episodes[0].segments[0].h3Prompt.replace(/non_diegetic_music:[\s\S]*$/, `non_diegetic_music: ${value}`);
    ok(gate(doc, 'h3-structure').detail.includes('背景配乐'), `配乐值「${value}」被生产门拦住`);
    ok(validateStoryboard(doc, CTX).some((p) => p.includes('背景配乐')), 'validate 同样拒绝片段配乐');
  }
  for (const suffix of ['\nnon_diegetic_music: N/A', '\n非叙事配乐：N/A']) {
    const doc = clone(fixture);
    doc.episodes[0].segments[0].h3Prompt += suffix;
    ok(gate(doc, 'h3-structure').detail.includes('背景配乐'), '重复或中英混写配乐字段被拦');
  }
  for (const replacement of ['No music field.', 'inline non_diegetic_music: N/A']) {
    const doc = clone(fixture);
    doc.episodes[0].segments[0].h3Prompt = doc.episodes[0].segments[0].h3Prompt.replace('non_diegetic_music: N/A', replacement);
    ok(gate(doc, 'h3-structure').detail.includes('背景配乐'), '缺少独立配乐字段被拦');
  }
  const doc = clone(fixture);
  doc.episodes[0].segments[0].h3Prompt = doc.episodes[0].segments[0].h3Prompt.replace('non_diegetic_music: N/A', 'non_diegetic_music:\n  N/A \r\n');
  ok(gate(doc, 'h3-structure').ok, 'N/A 字段值允许前后空白和换行');
}
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].h3Prompt = doc.episodes[0].segments[0].h3Prompt.replace('[Shot 1]', '[Shot 1] A portable radio plays a waltz that the characters can hear.');
  ok(gate(doc, 'h3-structure').ok, '镜头中的剧内声源音乐保留，片段配乐仍为 N/A');
}
{
  const doc = clone(FIXTURE);
  doc.promptLang = 'zh';
  for (const seg of doc.episodes[0].segments) {
    seg.h3Prompt = seg.h3Prompt.replace(/\[Shot (\d+)\]/g, '[镜头 $1]').replace(/\] At ([\d:.]+),/g, '] 于 $1，');
  }
  ok(gate(doc, 'h3-structure').ok, '中文 Ref2VA 保持英文配乐字段与 N/A');
}
{
  const doc = clone(KEYFRAMES);
  doc.promptLang = 'zh';
  for (const seg of doc.episodes[0].segments) {
    seg.h3Prompt = seg.h3Prompt.replace(h3AlignmentLine(seg.cuts), h3AlignmentLine(seg.cuts, 'zh'))
      .replace('integrated_multimodal_description:', '整体视听描述：')
      .replace('overall_soundscape:', '整体音景：')
      .replace('non_diegetic_music:', '非叙事配乐：')
      .replace(/\[Shot (\d+)\]/g, '[镜头 $1]').replace(/\] At ([\d:.]+),/g, '] 于 $1，');
  }
  ok(gate(doc, 'h3-structure').ok, '中文关键帧提示词使用中文配乐字段与 N/A');
}
// h3-dialogue
{
  const doc = clone(FIXTURE);
  const seg = doc.episodes[0].segments[0];
  seg.h3Prompt = seg.h3Prompt.replace('上船喽——过河的抓紧，雾要变天。', 'the ferryman calls out.');
  const g = gate(doc, 'h3-dialogue');
  ok(!g.ok, '台词没进 <d> 块被拦');
  ok(gate(doc, 'h3-dialogue', {}).detail.includes('跳过'), '没给剧本时本门跳过并明说');
}
// h3-lang — 语言与设定双向对账（默认英文 = 官方口径）
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].h3Prompt += ' 浓雾弥漫。';
  ok(!gate(doc, 'h3-lang').ok, '英文提示词在 <d> 台词之外混中文被拦');
}
{
  const doc = clone(FIXTURE);
  doc.promptLang = 'zh';
  ok(!gate(doc, 'h3-lang').ok, '设定中文、正文却是英文被拦——语言开关双向都管');
}
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].h3Prompt = doc.episodes[0].segments[0].h3Prompt.replace('the old ferryman squatting', '老周 squatting');
  ok(!gate(doc, 'prompt-no-names').ok, '英文模式下 H3 提示词里的人名被拦');
}
{
  const doc = clone(FIXTURE);
  doc.promptLang = 'zh';
  doc.episodes[0].segments[0].h3Prompt += ' 老周站在船头。';
  ok(gate(doc, 'prompt-no-names').ok, '中文模式 H3 提示词人名放行——身份靠分镜图锚定');
}
eq(h3Remainder('a <d>[Chinese] 你好</d> b "营业中" c'), 'a   b   c', 'h3Remainder 剔除 <d> 块与画面文字');
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].h3Prompt = doc.episodes[0].segments[0].h3Prompt.replace('[Chinese]', '[English]');
  ok(!gate(doc, 'h3-lang').ok, 'Chinese dialogue cannot carry an English language tag');
}
for (const block of ['<d>The ferry is ready.</d>', '<d>[Chinese] The ferry is ready.</d>', '<d>[English] </d>']) {
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].h3Prompt += `\n${block}`;
  ok(!gate(doc, 'h3-lang').ok, 'dialogue needs a matching language label and nonempty text');
}
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].h3Prompt += '\n<d>[English] The ferry is ready.</d>';
  ok(gate(doc, 'h3-lang').ok, 'English dialogue accepts its English language tag');
}
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].h3Prompt = doc.episodes[0].segments[0].h3Prompt.replace('overall_soundscape:', '<Subject 1> (S3) says: <d>[English] The ferry is ready.</d>\noverall_soundscape:');
  ok(!gate(doc, 'h3-dialogue').ok, 'invented dialogue is rejected even when it appears nowhere in the script');
}
// prompt-english
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].cuts[0].frame = 'extreme wide shot 渡口的浓雾清晨';
  ok(!gate(doc, 'prompt-english').ok, '分镜图提示词混中文被拦');
}
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].cuts[0].frame = '  ';
  ok(!gate(doc, 'prompt-english').ok, '空分镜图提示词被拦');
}
// prompt-no-names
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].cuts[0].frame += ' 沈知微 standing on the pier';
  ok(!gate(doc, 'prompt-no-names').ok, '分镜图提示词出现角色名被拦');
}
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].cuts[1].frame += ' 老伯 squatting'; // cast 里的别名
  ok(!gate(doc, 'prompt-no-names').ok, '角色别名也拦');
}
// refs
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].cuts[0].characters = ['C05'];
  ok(!gate(doc, 'refs').ok, '不在该场的人物被拦');
}
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].cuts[0].props = ['P02'];
  ok(!gate(doc, 'refs').ok, '不在该场的道具被拦');
}
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].sceneIndex = 9;
  ok(!gate(doc, 'refs').ok, '不存在的场次被拦');
}

// style-phrase — 同剧分镜图画风不许漂
{
  eq(DEFAULT_STYLE, 'semi-realistic', '默认半写实');
  ok(STYLE_PRESETS['semi-realistic'].phrase && STYLE_PRESETS.ghibli.phrase, '预设带风格短语');
  const doc = clone(FIXTURE);
  doc.style = '油画';
  ok(!gate(doc, 'style-phrase').ok, '不在预设里的风格被拦');
}
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].cuts[0].frame = doc.episodes[0].segments[0].cuts[0].frame.replace(STYLE_PRESETS['semi-realistic'].phrase, 'cinematic image');
  const g = gate(doc, 'style-phrase');
  ok(!g.ok, '分镜图提示词缺风格短语被拦');
  ok(g.detail.includes('E01-01#1'), '点名到切');
}
{
  const doc = clone(FIXTURE);
  doc.style = 'ghibli';
  ok(!gate(doc, 'style-phrase').ok, '换成吉卜力后写实短语不再达标——换风格是整批换');
}

/* ---------------- 镜头配方卡库（可选挂载） ---------------- */
/*
 * 受限 frontmatter 解析是本 skill 自己写的（刻意不 import shot-recipes.mjs，
 * 两个 skill 谁没有谁都能跑），所以解析器、加载器、门三层都要有断言。
 */

{
  const card = parseCardFields(`---
id: demo-card
name: 演示卡
name_en: Demo Card
category: dialogue
cuts: [2, 3]
sizes: [medium, close]
cameras: [Static Shot, Push In]
must_phrases: [over-the-shoulder, blurred foreground shoulder]
---

## 意图

正文一概不读。
`);
  eq(card.id, 'demo-card', '受限解析取到 id');
  eq(card.name_en, 'Demo Card', '英文卡名也是机器字段');
  eq(card.cuts.join(','), '2,3', '行内数组里的整数转成数字');
  eq(card.must_phrases.length, 2, '必备短语按逗号切开');
  eq(card.category, undefined, '门用不到的字段一概不收');
  eq(parseCardFields('没有 frontmatter'), null, '没有 frontmatter 就不是卡片');
  eq(parseCardFields('---\nname: 无 id\n---\n'), null, '没有 id 就不是卡片');
}

const CARDS = loadRecipes(join(here, '../references/test-fixtures/shot-recipes'));
eq(CARDS.size, 3, '最小卡片夹具三张全读出');
ok(CARDS.get('ots-shot-reverse').must_phrases.includes('over-the-shoulder'), '真实卡片的必备短语读得出来');
eq(CARDS.get('ots-shot-reverse').cuts[0], 2, '真实卡片的格数下限读得出来');
eq(loadRecipes(join(here, '../不存在的目录')).size, 0, '目录不存在不崩');

const SHOTS = { ...CTX, recipes: CARDS };
// 合规引用：两格连排的过肩正反打，必备短语逐条进 frame
const withRecipe = () => {
  const doc = clone(FIXTURE);
  const cuts = doc.episodes[0].segments.find((s) => s.id === 'E01-05').cuts;
  for (const i of [0, 1]) {
    cuts[i].recipe = 'ots-shot-reverse';
    cuts[i].frame += ', over-the-shoulder framing with a blurred foreground shoulder';
  }
  return doc;
};

// 跳过条件是「没给 --shots」，不是「没有 cut 带 recipe」
{
  const g = gate(FIXTURE, 'shot-recipe');
  ok(g.ok, '没挂卡库本门通过');
  ok(g.detail.includes('跳过'), '跳过要明说，不静默');
}
{
  // 夹具本身有两处真实引用（E01-06#1 hands-tell / E01-09#1 insert-beat），
  // 所以「全篇没引用」这一条要拿剥掉 recipe 的副本来试
  const bare = JSON.parse(JSON.stringify(FIXTURE));
  for (const s of bare.episodes.flatMap((e) => e.segments)) for (const c of s.cuts) delete c.recipe;
  const g = gate(bare, 'shot-recipe', SHOTS);
  ok(g.ok, '挂了卡库但全篇没引用配方也算通过');
  eq(g.detail, '本批分镜没有引用配方', '没引用同样明说，不静默');
}
{
  // 样例即规范：夹具里真的挂了配方，而且挂了之后这道门是过的
  const refs = FIXTURE.episodes.flatMap((e) => e.segments).flatMap((s) => s.cuts).filter((c) => c.recipe);
  eq(refs.length, 2, '夹具有两处真实配方引用');
  const g = gate(FIXTURE, 'shot-recipe', SHOTS);
  ok(g.ok, '夹具的配方引用全过');
  eq(g.detail, '', '全过不留备注');
}
{
  const g = gate(withRecipe(), 'shot-recipe', SHOTS);
  ok(g.ok, '合规引用全过');
  eq(g.detail, '', '全过不留备注');
}
// 击穿一：id 不在卡库
{
  const doc = withRecipe();
  doc.episodes[0].segments.find((s) => s.id === 'E01-05').cuts[0].recipe = 'no-such-card';
  const g = gate(doc, 'shot-recipe', SHOTS);
  ok(!g.ok, '引用不存在的配方被拦');
  ok(g.detail.includes('E01-05#1') && g.detail.includes('不在配方库里'), '点名到段号#切序');
}
// 击穿二：必备短语没进 frame
{
  const doc = withRecipe();
  const cuts = doc.episodes[0].segments.find((s) => s.id === 'E01-05').cuts;
  cuts[1].frame = cuts[1].frame.replace('blurred foreground shoulder', 'soft foreground');
  const g = gate(doc, 'shot-recipe', SHOTS);
  ok(!g.ok, '必备短语没进分镜图提示词被拦');
  ok(g.detail.includes('E01-05#2'), '点名到切');
  ok(g.detail.includes('过肩正反打') && g.detail.includes('blurred foreground shoulder'), '配方名 + 缺的短语原文——与 shot-recipes 的 check 措辞一致');
}
{
  const doc = withRecipe();
  const cuts = doc.episodes[0].segments.find((s) => s.id === 'E01-05').cuts;
  cuts[0].frame = cuts[0].frame.replace('over-the-shoulder', 'Over-The-Shoulder');
  ok(gate(doc, 'shot-recipe', SHOTS).ok, '短语判定两边小写化，大小写不影响');
}
// 击穿三：多格配方的连排长度不够
{
  const doc = withRecipe();
  delete doc.episodes[0].segments.find((s) => s.id === 'E01-05').cuts[1].recipe;
  const g = gate(doc, 'shot-recipe', SHOTS);
  ok(!g.ok, '两格配方只挂一格被拦');
  ok(g.detail.includes('E01-05#1') && g.detail.includes('要 2 格连排'), '多格配方靠连续同 recipe 的分镜表达');
}
// 建议景别／运镜不设门，只在报告里提示偏离
{
  const doc = withRecipe();
  const cut = doc.episodes[0].segments.find((s) => s.id === 'E01-05').cuts[0];
  cut.size = 'extreme-wide';
  ok(gate(doc, 'shot-recipe', SHOTS).ok, '建议景别偏离不设门——配方是语汇不是法条');
  const d = recipeDrift(cut, CARDS.get('ots-shot-reverse'));
  eq(d.sizes.join(' / '), 'medium / close', '偏离时报出建议景别');
  eq(d.cameras.length, 0, '运镜没偏离就不报');
  eq(recipeDrift(cut, null).sizes.length, 0, '没有卡片就没有偏离可言');
}
// recipe 不进结构检查——照 note 这个可选字段的先例办
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].cuts[0].recipe = 'no-such-card';
  eq(validateStoryboard(doc, CTX).length, 0, '不挂卡库时 recipe 不进结构检查');
}

/* ---------------- 门失败累积 ---------------- */

// 每次 validate 的结果本来跑完就没了，「模型最常违反哪条规则」只能靠印象。
// 纯函数 + CLI 负责 IO，所以这里不落盘也能验。
{
  const gates = [
    { id: 'cut-length', label: '每个分镜 2–5 秒', ok: false, detail: 'E01-01#1 9 秒' },
    { id: 'coverage', label: '节拍全覆盖', ok: true, detail: '' },
    { id: 'segment-cap', label: '每段 ≤ 15 秒', ok: false, detail: 'E01-01 共 21 秒' },
  ];
  const rows = gateLogEntries(gates, { doc: 'x.json', at: 'T0' });
  eq(rows.length, 3, '一次运行记一条 run + 每条失败一行');
  eq(rows[0].kind, 'run', '第一行是运行记录');
  eq(rows[0].gates, 3, 'run 记下这次跑了几道门');
  eq(rows[0].failed, 2, 'run 记下这次挂了几道');
  ok(rows.slice(1).every((r) => r.kind === 'fail'), '其余都是失败记录');
  ok(rows.every((r) => r.at === 'T0' && r.doc === 'x.json'), '时间与文档名逐行带上');
  eq(gateLogEntries([], {}).length, 0, '没有门就不写任何东西');

  const all = ['cut-length', 'coverage', 'segment-cap', 'refs'];
  const sum = summarizeGateLog([...rows, ...gateLogEntries(gates, { doc: 'y.json', at: 'T1' })], all);
  eq(sum.runs, 2, '统计跑过几次');
  eq(sum.cleanRuns, 0, '统计全过几次');
  eq(sum.fails, 4, '统计累计失败条数');
  eq(sum.ranked[0].gate, 'cut-length', '按失败次数排序，最常响的在前');
  eq(sum.ranked[0].count, 2, '同一道门跨运行累加');
  ok(sum.ranked[0].samples.length >= 1, '带上 detail 样本，供人看有没有该设而没设的门');
  eq(sum.silent.join(','), 'coverage,refs', '从没响过的门列出来——可能是死门，也可能规则已被内化');
  eq(summarizeGateLog([], all).silent.length, 4, '零日志时所有门都算没响过');
  eq(summarizeGateLog([null, 'x', { kind: 'run', failed: 0 }], all).runs, 1, '坏行跳过不炸');
}
eq(GATE_LOG, '.gates.jsonl', '日志文件名固定');

/* ---------------- exportPack（H3 投产包） ---------------- */

{
  const pack = exportPack(KEYFRAMES, SCRIPT, { imageExists: () => false });
  eq(pack.files.length, 11, '十段 prompt.md + 一份 manifest');
  ok(pack.files.some((f) => f.path === 'E01-01/prompt.md'), '每段一个文件夹里的 prompt.md');
  const p01 = pack.files.find((f) => f.path === 'E01-01/prompt.md');
  ok(p01.content.startsWith('# E01-01 · H3 提示词'), 'prompt.md 带标题');
  ok(p01.content.includes('Picture 1 = f1.png（**首帧**，钉 0.00 秒）'), '明确指定哪个文件是首帧');
  ok(p01.content.includes('Picture 4 = f4.png（钉 10.00 秒）'), '每张图的切点秒数写明');
  ok(p01.content.includes('---\n\nHow the reference pictures align'), '分隔线以下是 h3Prompt 原样（官方英文口径）');
  ok(!JSON.stringify(pack).includes('recipe'), '配方是创作期语汇，H3 投产包里没有它的位置');
  const m = pack.manifest.find((x) => x.segment === 'E01-01');
  eq(m.pictures.join(','), 'E01-01/f1.png,E01-01/f2.png,E01-01/f3.png,E01-01/f4.png', 'Picture 序 = 文件夹里的 f1..fn');
  eq(m.cutStarts.join(','), '0,3,6,10', 'manifest 带切点时刻表');
  eq(m.missing.length, 4, '缺图逐张标注');
  ok(pack.missingTotal > 0, '缺图总数上报');
}
{
  const pack = exportPack(FIXTURE, SCRIPT, { imageExists: () => true, dir: 'out' });
  eq(pack.missingTotal, 0, '图齐了就没有缺图标注');
  ok(pack.files.some((f) => f.path === 'out/manifest.json'), '--out 改导出目录');
  ok(pack.files.some((f) => f.path === 'out/E01-01/prompt.md'), '段文件夹跟着 --out 走');
}

/* ---------------- validateStoryboard 结构检查 ---------------- */

eq(validateStoryboard(FIXTURE, CTX).length, 0, '样例零违规');
ok(validateStoryboard(null).length > 0, 'null 不崩');
ok(validateStoryboard({}).some((p) => p.includes('source')), '缺 source 报出来');
ok(validateStoryboard({ source: 'x', episodes: [] }).some((p) => p.includes('episodes')), '空 episodes 报出来');
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0] = { id: 'E01-01' };
  const problems = validateStoryboard(doc, CTX);
  ok(problems.some((p) => p.includes('sceneIndex')), '缺 sceneIndex 报出来');
  ok(problems.some((p) => p.includes('h3Prompt')), '缺 H3 提示词报出来');
  ok(problems.some((p) => p.includes('没有分镜')), '缺 cuts 报出来');
}
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].cuts[0] = { beats: [1, 1] };
  const problems = validateStoryboard(doc, CTX);
  ok(problems.some((p) => p.includes('seconds')), '分镜缺秒数报出来');
  ok(problems.some((p) => p.includes('frame')), '分镜缺分镜图提示词报出来');
}
{
  const doc = clone(FIXTURE);
  doc.episodes.push(clone(doc.episodes[0]));
  ok(validateStoryboard(doc, CTX).some((p) => p.includes('重复')), '重复集号报出来');
}

/* ---------------- seed ---------------- */

const seeded = seedFromScript(SCRIPT);
eq(seeded.source, '渡口', 'seed 带剧名');
{
  // 分镜画风跟人物卡走；旧名 realistic 写成新名，句子不变
  const old = seedFromScript(SCRIPT, null, { cast: { style: 'realistic' } });
  eq(old.style, 'semi-realistic', 'seed 把人物卡旧名写成新名');
  eq(old.styleSentence, STYLE_PRESETS['semi-realistic'].sentence, '半写实风格句不变');
  const photo = seedFromScript(SCRIPT, null, { cast: { style: 'photoreal' } });
  eq(photo.style, 'photoreal', 'seed 继承写实画风');
  eq(photo.styleSentence, STYLE_PRESETS.photoreal.sentence, 'seed 写入写实风格句');
  eq(artworkMedium(STYLE_PRESETS.photoreal.sentence).join(','), 'live-action', '写实风格句只有实拍一种媒介');
  ok(!/film[\s-]+still/i.test(STYLE_PRESETS.photoreal.sentence), '写实风格句不写 film still');
  ok(artworkMedium(STYLE_PRESETS.photoreal.phrase).join(',') === 'live-action', '写实分镜图短语是实拍媒介');
}
eq(seeded.episodes.length, 6, 'seed 全六集');
eq(seeded.episodes[0].segments.length, 0, 'segments 留空给模型切');
eq(seeded.episodes[0].seedScenes.length, 2, '工作底稿带两场');
eq(seeded.episodes[0].seedScenes[0].beats.length, 13, '底稿带全部节拍');
ok(seeded.episodes[0].seedScenes[0].beats[0].seconds > 0, '每拍带秒数');
eq(seedFromScript(SCRIPT, [2, 3]).episodes.map((e) => e.ep).join(','), '2,3', '--eps 区间过滤');
eq(seedFromScript({}).episodes.length, 0, '空剧本不崩');

ok(seeded.episodes.flatMap((ep) => ep.seedScenes).flatMap((scene) => scene.beats)
  .every((beat) => !['start', 'end', 'startSec', 'endSec', 'timeRangeSec'].some((key) => key in beat)),
  '预算秒数不展开成逐动作表演区间');
{
  const doc = clone(FIXTURE);
  const segment = doc.episodes[0].segments[0];
  segment.h3Prompt += '\nShe hesitates, then hands it over. At 00:02.000 the requested bell sounds.';
  const changedBudget = clone(SCRIPT);
  changedBudget.params = { ...changedBudget.params, actionSeconds: 99 };
  const exported = exportPack(doc, changedBudget);
  const prompt = exported.files.find((file) => file.path === `${segment.id}/prompt.md`);
  eq(prompt.content.split('\n---\n\n')[1], segment.h3Prompt + '\n',
    '导出原样保留自然表演和显式同步事件，不把估时写进正文');
  assert.deepEqual(exported.manifest[0].cutStarts, cutStarts(segment.cuts));
}

/* ---------------- slug / 枚举 ---------------- */

eq(slug('渡口'), '渡口', '中文原样');
eq(slug('  '), 'storyboard', '空名兜底');
ok(Object.values(SHOT_SIZES).every((s) => s.zh && s.phrase), '景别枚举带中文名与英文短语');
ok(Object.keys(CAMERA_MOVES).length >= 18, '运镜词表覆盖 H3 全部动作类型');
ok(CAMERA_MOVES['Static Shot'] === '固定' && CAMERA_MOVES['Push In'] === '推', '运镜词表中英对照');

/* ---------------- render markdown ---------------- */

const md = renderMarkdown(FIXTURE, CTX);
ok(md.includes('# 渡口 · 分镜（第 1 集）'), 'md 标题');
ok(md.includes('### E01-01 · 渡口栈桥（浓雾清晨）'), 'md 段头带场景名与光照');
ok(md.includes('H3 视频提示词'), 'md 带逐段 H3 提示词');
ok(md.includes('subject_definitions:') && md.includes('<Subject 4> — S02'), 'Ref2VA 的参考定义完整可复制');
ok(md.includes('[Shot 2] At 00:03.000,'), '切点时刻原样进 md');
ok(md.includes('老周'), 'md 说话人显示名字');
ok(md.includes('生成批次单') && md.includes('配音对齐单'), 'md 带两张工单');
ok(renderMarkdown(FIXTURE, { script: SCRIPT }).includes('C03'), '不给 outline 退回裸 ID');

/* ---------------- render html ---------------- */

const html = renderHtml(FIXTURE, CTX);
ok(html.includes('<!doctype html>'), 'html 完整文档');
ok(!/src="http|href="http|@import|url\(http/.test(html), '零外部资源');
ok(html.includes('分镜节奏带'), '01 分镜节奏带');
ok(html.includes('分集分镜表'), '02 分集分镜表');
ok(html.includes('生成批次单'), '03 生成批次单');
ok(html.includes('配音对齐单'), '04 配音对齐单');
ok(html.includes('✓ 质量门 32 / 32'), '页眉徽章全绿');
ok(html.includes('class="rseg"'), '节奏带按段分组（粗分隔）');
ok(html.includes('#seg-E01-01'), '节奏带段可跳转');
ok(html.includes('主分镜图 · #1 未生成'), '主分镜图缺图时显示占位不装有');
ok(html.includes('#2 未生成'), '子分镜图缺图有小占位');
// 主分镜图区：无图时每切各占一整行提示词卡 + 复制按钮（PR 核心目标）
{
  const nCuts = FIXTURE.episodes.reduce((n, e) => n + e.segments.reduce((m, s) => m + s.cuts.length, 0), 0);
  ok((html.match(/class="frame ph fcell"/g) ?? []).length === nCuts, '无图时每切都是整宽提示词卡');
  const c0 = FIXTURE.episodes[0].segments[0].cuts;
  ok(html.includes(`data-copy="${c0[0].frame}"`), '主分镜格复制按钮带该切 frame 原文');
  ok(html.includes(`data-copy="${c0[1].frame}"`), '子分镜格复制按钮同样带 frame 原文');
}
ok(html.includes('class="shots clip"'), '段卡区默认截断');
ok(html.includes('展开全部段'), '每集自带展开按钮');
ok(html.includes('H3 提示词'), '段卡带 H3 提示词面板');
ok(html.includes('class="duo"'), '分镜列表与提示词面板五五分栏');
ok(html.includes('static shot'), '英文提示词正文进面板');
ok(html.includes('detailed_description'), '官方骨架字段进面板');
ok(html.includes('[Shot 2] At'), '逐镜换行的结构化正文进面板');
ok(html.includes('分镜图提示词'), '每个分镜带分镜图提示词复制按钮');
ok(html.includes('id="lightbox"'), '点图放大');
ok(html.includes('渡口-storyboard.json'), '导出文件名');
ok(html.includes('批次 01'), '批次卡编号');
ok(html.includes('@media print'), '打印样式');
ok(html.includes('老周'), 'html 里 ID 换成名字');
{
  const withImg = renderHtml(FIXTURE, { ...CTX, imageExists: () => true });
  ok(withImg.includes('"E01-01/f1.png"'), '主分镜图从段文件夹读');
  ok(withImg.includes('"E01-01/f2.png"'), '子分镜图同样从段文件夹读');
  ok(!withImg.includes('未生成'), '有图时不再显示占位');
  ok(!withImg.includes('class="frame ph fcell"'), '图出全时不再走整宽提示词卡');
  ok(withImg.includes('class="subs"'), '图出全时保留子分镜条');
  ok(withImg.includes('class="subf"'), '子分镜条用小缩略图');
}
// 病灶横幅
{
  const doc = clone(FIXTURE);
  doc.episodes[0].segments[0].cuts[0].seconds = 6;
  const h = renderHtml(doc, CTX);
  ok(h.includes('class="galert"'), '有门未过时页顶挂病灶横幅');
  ok(h.includes('gatepill fail'), '徽章翻红');
}
// XSS：模型数据全部过 esc
{
  const doc = clone(FIXTURE);
  doc.source = '<script>alert(1)</script>';
  doc.episodes[0].segments[0].note = '<img src=x onerror=alert(1)>';
  const h = renderHtml(doc, { script: SCRIPT });
  ok(!h.includes('<script>alert(1)</script>'), '标题被转义');
  ok(!h.includes('<img src=x'), 'note 被转义');
  ok(h.includes('\\u003c'), '内嵌 JSON 的 < 转成 \\u003c，防 </script 截断');
}

/* ---------------- 报告界面语言（--lang，与 promptLang 独立） ---------------- */

{
  const en = renderHtml(FIXTURE, { ...CTX, lang: 'en' });
  ok(en.includes('<html lang="en">'), 'en 报告的 html lang 属性跟着语言走');
  ok(en.includes('Export JSON'), 'en 界面：导出按钮英文');
  ok(en.includes('Quality gates 32 / 32'), 'en 界面：页眉徽章英文');
  ok(en.includes('Cut rhythm strip'), 'en 界面：节奏带节标题英文');
  ok(en.includes('Segment cards'), 'en 界面：分镜表节标题英文');
  ok(en.includes('Generation batches'), 'en 界面：批次节标题英文');
  ok(en.includes('Audio alignment'), 'en 界面：配音对齐节标题英文');
  ok(en.includes('master frame'), 'en 界面：主分镜图占位标签英文');
  ok(!en.includes('导出 JSON'), 'en 界面不残留中文导出按钮');
  ok(!en.includes('生成批次单'), 'en 界面不残留中文批次标题');
  ok(!en.includes('配音对齐单'), 'en 界面不残留中文对齐标题');
  ok(en.includes('subject_definitions:'), 'en 界面下 H3 提示词数据原样不动');
  ok(en.includes('[Shot 2] At 00:03.000,'), 'en 界面下切点时刻数据原样不动');
}
{
  const enMd = renderMarkdown(FIXTURE, { ...CTX, lang: 'en' });
  ok(enMd.includes('# 渡口 · Storyboard (Episode 1)') && enMd.includes('Audio alignment'), 'en markdown 标题与节标题英文');
}
{
  const zhAgain = renderHtml(FIXTURE, CTX);
  ok(zhAgain.includes('<html lang="zh">') && zhAgain.includes('导出 JSON'), '默认仍是中文界面');
}
{
  const doc = clone(FIXTURE);
  doc.lang = 'en';
  ok(renderHtml(doc, CTX).includes('Export JSON'), 'JSON 顶层 lang 字段可选定界面语言');
  ok(renderHtml(doc, { ...CTX, lang: 'zh' }).includes('导出 JSON'), 'ctx.lang（--lang）优先于 JSON 的 lang 字段');
}
{
  let threw = false;
  try {
    renderHtml(FIXTURE, { ...CTX, lang: 'ja' });
  } catch (e) {
    threw = /zh \/ en/.test(e.message);
  }
  ok(threw, '非法界面语言抛错并点名内置 zh / en');
}

// 质量门面板是报告的一部分：英文界面下门标签也要翻译（阈值由门自己算，原样保留）
{
  const gateEn = renderHtml(FIXTURE, { ...CTX, lang: 'en' });
  ok(gateEn.includes('Every cut 2–15s'), 'EN 报告的质量门标签翻译且阈值原样保留');
  ok(!gateEn.includes('每个分镜 2–5 秒'), 'EN 报告不再出现中文门标签');
  ok(gateEn.includes('no recipe card library mounted'), 'EN 报告的跳过说明也翻译');
  ok(gateEn.includes('--ref2va not given (keyframe export mode) — gate skipped (treated as passing)'), 'EN 报告明确翻译 Ref2VA 兼容模式跳过');
  const gateEnStrict = renderHtml(FIXTURE, { ...CTX, ref2va: true, lang: 'en' });
  for (const label of [
    'Ref2VA: one shared style sentence before [Shot 1]',
    'Ref2VA: first reference use bound inside descriptive prose',
    'Ref2VA: referenced vocal sources bound as <Subject N> (Sx)',
    "Ref2VA: the first sentence of each speaker's cast voice prompt before their first line in every segment",
    'Ref2VA: stable vocal-source IDs in first-event order',
    'Ref2VA: task-prefixed summary, including keyframe completion for a preceding tail frame',
    'Ref2VA: one retention line per label with shots, relationship and explanation',
    'Ref2VA: synchronized sounds in their shots; only continuous ambience overall',
    'Ref2VA: exact voiceover phrase and immediate closed-lips clause',
    'Ref2VA: video, frames and used cast/art references share the same artwork medium',
  ]) {
    const escaped = label.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    ok(gateEn.includes(escaped) && gateEnStrict.includes(escaped), `EN HTML 在兼容与 Ref2VA 模式都翻译投产门：${label}`);
  }
}

/* ---------------- 报告里的「配方」列（偏离只提示，不设门） ---------------- */

{
  const doc = withRecipe();
  const rmd = renderMarkdown(doc, SHOTS);
  ok(rmd.includes('| 配方 |'), 'md 分镜表有配方列');
  ok(rmd.includes('| 过肩正反打 |'), 'md 显示卡名，没偏离就不带 ≠');
  ok(renderMarkdown(doc, { ...SHOTS, lang: 'en' }).includes('| Recipe |'), 'en md 的配方列表头英文');
  const rhtml = renderHtml(doc, SHOTS);
  ok(rhtml.includes('class="cut-rc">过肩正反打</span>'), 'html 分镜行有配方标签');
  ok(!rhtml.includes('≠'), '没偏离就不出 ≠ 上标');
}
{
  const doc = withRecipe();
  doc.episodes[0].segments.find((s) => s.id === 'E01-05').cuts[0].size = 'extreme-wide';
  const rhtml = renderHtml(doc, SHOTS);
  ok(rhtml.includes('<sup title="配方建议景别 medium / close——只提示不设门">≠</sup>'), '偏离加 ≠ 上标，建议值写进 title');
  ok(renderMarkdown(doc, SHOTS).includes('过肩正反打 ≠（配方建议景别 medium / close——只提示不设门）'), 'md 没有 title，建议值直接写进格子');
  ok(renderHtml(doc, { ...SHOTS, lang: 'en' }).includes('Recipe suggests size medium / close — advisory, not gated'), 'en 报告的偏离提示英文');
  ok(gate(doc, 'shot-recipe', SHOTS).ok, '偏离在报告里提示，但门照过——门的信用比数量重要');
}
{
  const doc = withRecipe();
  ok(renderMarkdown(doc, CTX).includes('| ots-shot-reverse |'), '不挂卡库时配方列退回裸 id');
  ok(renderMarkdown(FIXTURE, CTX).includes('| — |'), '没引用配方的切在配方列写 —');
}
/* ---------------- Ref2VA 参考对账（画布模式 --ref2va） ---------------- */
{
  const REF = { ...CTX, ref2va: true };
  const skipped = gate(FIXTURE, 'h3-references');
  ok(skipped.ok && skipped.detail.includes('--ref2va'), '不开 --ref2va 时参考对账门跳过并说明');

  // 真实事故：画布连了角色三视图和场景图，提示词却写首帧对齐行，场景一次没提
  const legacy = gate(KEYFRAMES, 'h3-references', REF);
  ok(!legacy.ok && legacy.detail.includes('Ref2VA 六段式'), '画布模式下首帧对齐格式被拦');
  ok(gateReport(FIXTURE, REF).every((g) => g.ok), '样例（Ref2VA）在画布模式下全部门通过');
  eq(validateStoryboard(FIXTURE, REF).length, 0, '完整渡口样例通过 Ref2VA 生产 validate');

  // 每段单独生成：角色卡的声音不写进这一段，模型就按画面猜嗓子（老者的短促呵斥被配成尖细女声）
  const unvoiced = (index) => {
    const doc = clone(FIXTURE);
    const seg = doc.episodes[0].segments[index];
    seg.h3Prompt = seg.h3Prompt.replace(/ \(voice: [^)]*\)/, '');
    return gate(doc, 'h3-speaker-voice', REF);
  };
  const ferryman = unvoiced(0);
  ok(!ferryman.ok && ferryman.detail.includes('E01-01.dialogue1') && ferryman.detail.includes('要写的声音「75-year-old male, gravelled husky bass-baritone'), '这一段没写说话人的角色卡声音被拦，并给出要写的声音');
  ok(!ferryman.detail.includes('E01-02'), '只拦漏写的那一段');
  ok(!unvoiced(9).ok, '画外音同样要写声音');
  const aggregateRetention = clone(FIXTURE);
  aggregateRetention.episodes[0].segments[0].h3Prompt = aggregateRetention.episodes[0].segments[0].h3Prompt
    .replace(/retention_analysis:[\s\S]*?detailed_description:/, 'retention_analysis:\nAll references are retained exactly.\n\ndetailed_description:');
  ok(validateStoryboard(aggregateRetention, REF).some((problem) => problem.includes('[h3-retention-per-line]')), '生产 validate 明确拒绝一行总述的保留分析并报告规则 ID');
  eq(validateStoryboard(aggregateRetention, CTX).length, 0, '不开 --ref2va 保持原有兼容生产检查');

  // 把 E01-01（场景 S02；角色 C01、C03、C04）改成 Ref2VA
  const defs = [
    '<Subject 1> — C01, a young woman in a navy student tunic with two red-tied braids; face, hair and costume come from <Picture 1>.',
    '<Subject 2> — C03, the weathered old ferryman; appearance comes from <Picture 2>.',
    '<Subject 3> — C04, the middle-aged peddler; appearance comes from <Picture 3>.',
    '<Subject 4> — S02, the foggy wooden pier; layout, materials and light come from <Picture 4>.',
  ];
  const toRef2va = (h3, subjectLines = defs, uses = true) => {
    let body = h3.slice(h3.indexOf('integrated_multimodal_description:')).replace('integrated_multimodal_description:', 'detailed_description:');
    body = body.replace(`[Shot 1] ${FIXTURE.styleSentence}`, `${FIXTURE.styleSentence}\n[Shot 1]`);
    if (uses) {
      body = body
        .replace('\n[Shot 2]', ' <Subject 1> runs past <Subject 4>.\n[Shot 2]')
        .replace('\n[Shot 4]', ' <Subject 2> calls out.\n[Shot 4]')
        .replace('\n\noverall_soundscape:', ' <Subject 3> hurries on.\n\noverall_soundscape:');
    }
    const retention = subjectLines.map((line) => `${line.match(/<Subject \d+>/)[0]} (appears in [Shot 1]): fully_preserved - the visible reference appearance is retained.`);
    return `subject_definitions:\n${subjectLines.join('\n')}\nsummary: [reference generation] A girl reaches a fogbound pier as the ferry calls.\nretention_analysis:\n${retention.join('\n')}\n${body}`;
  };
  const withSeg1 = (h3) => {
    const doc = clone(KEYFRAMES);
    doc.episodes[0].segments[0].h3Prompt = h3;
    return doc;
  };
  const seg1 = KEYFRAMES.episodes[0].segments[0];
  const good = withSeg1(toRef2va(seg1.h3Prompt));
  const goodGate = gate(good, 'h3-references', REF);
  ok(!goodGate.detail.includes('E01-01'), 'Ref2VA 定义了场景和全部出镜角色并用上：本段通过');
  ok(gate(good, 'h3-structure', REF).ok, 'Ref2VA 版本仍过 H3 结构门');
  ok(gate(good, 'camera-phrase', REF).ok && gate(good, 'h3-dialogue', REF).ok, 'Ref2VA 版本运镜和台词门照过');
  ok(gate(good, 'prompt-no-names', REF).ok, 'Subject 写 ID 不触发英文禁人名');

  const noScene = gate(withSeg1(toRef2va(seg1.h3Prompt, defs.slice(0, 3)).replace(/ <Subject 4>/g, '').replace(' and <Subject 4>', '')), 'h3-references', REF);
  ok(noScene.detail.includes('E01-01 画布上这段连了场景 S02 的参考图'), '漏定义场景被拦（本次事故）');

  const unused = gate(withSeg1(toRef2va(seg1.h3Prompt, defs, false)), 'h3-references', REF);
  ok(unused.detail.includes('<Subject 4>（场景 S02）在 detailed_description 里一次都没用上'), '定义了场景但正文没用上被拦');

  const noId = [...defs];
  noId[3] = '<Subject 4> — the foggy wooden pier; layout comes from <Picture 4>.';
  ok(gate(withSeg1(toRef2va(seg1.h3Prompt, noId)), 'h3-references', REF).detail.includes('<Subject 4> 要写明它是哪个角色、场景或哪一段的尾帧'), '定义不写 ID 被拦');

  const noPic = [...defs];
  noPic[1] = '<Subject 2> — C03, the weathered old ferryman.';
  ok(gate(withSeg1(toRef2va(seg1.h3Prompt, noPic)), 'h3-references', REF).detail.includes('<Subject 2> 的定义里要引用它的来源图'), '定义不引用 <Picture N> 被拦');

  const extra = [...defs, '<Subject 5> — C02, a young man; appearance comes from <Picture 5>.'];
  const extraGate = gate(withSeg1(toRef2va(seg1.h3Prompt, extra).replace(' <Subject 3> hurries on.', ' <Subject 3> hurries on. <Subject 5> waits.')), 'h3-references', REF);
  ok(extraGate.detail.includes('<Subject 5> 是角色 C02，但画布上这一段不会连它的参考图'), '定义本段不连的角色被拦');

  const twice = [...defs];
  twice[2] = '<Subject 3> — C03 again; appearance comes from <Picture 3>.';
  ok(gate(withSeg1(toRef2va(seg1.h3Prompt, twice)), 'h3-references', REF).detail.includes('<Subject 2> 和 <Subject 3> 都是角色 C03'), '两个 Subject 同一实体被拦');

  // 导入端同一规则：没 id 的角色卡按名字挂到大纲 ID；C1 不命中 C10；两个实体共用的词不算
  const view = castView(CAST, OUTLINE);
  ok(view.characters.some((c) => c.id === 'C01' && c.name === '沈知微'), '无 id 的角色卡按名字挂到大纲 ID');
  const terms = entityTerms({ characters: [{ id: 'C1', name: 'Ann' }, { id: 'C10', name: 'Bo' }] }, { scenes: [{ id: 'S1', name: 'Ann' }] });
  eq(identifySubject('C10 in the rain', terms), 'char:C10', 'ID 整词匹配：C1 不命中 C10');
  eq(identifySubject('Ann by the door', terms), null, '两个实体共用的名字谁也不代表');
  eq(subjectDefinitions('subject_definitions:\n<Subject 1> — a\n<Subject 2> — b\nsummary: x').size, 2, 'subject 定义行按编号解析');
  ok(ref2vaReferenceProblems('X', 'subject_definitions:\n<Subject 2> — C1 from <Picture 1>\nsummary: s\ndetailed_description: <Subject 2>', ['char:C1'], terms).some((p) => p.includes('从 1 起连续编号')), 'Subject 编号不从 1 起被拦');
}

/* ---------------- 承接上一段的尾帧（continuesFrom） ---------------- */
{
  // 画布上一段生成完，Seedance 把它的最后一帧挂到下一段：承接段的提示词必须把它定义成 Subject
  const REF = { ...CTX, ref2va: true };
  const withSeg2 = (edit) => {
    const doc = clone(FIXTURE);
    edit(doc.episodes[0].segments[1], doc);
    return doc;
  };
  eq(FIXTURE.episodes[0].segments[1].continuesFrom, 'E01-01', '样例同场第二段承接上一段尾帧');

  const missing = gate(withSeg2((seg) => { seg.h3Prompt = seg.h3Prompt.replace(/\n<Subject 5> — E01-01,[^\n]*/, ''); }), 'h3-references', REF);
  ok(missing.detail.includes('E01-02 画布上这段连了上一段 E01-01 的尾帧 的参考图'), '承接段没定义尾帧 Subject 被拦');

  ok(!gate(FIXTURE, 'h3-references', REF).detail.includes('E01-02'), '尾帧定义成 Subject 并在 [Shot 1] 用上：本段通过');
  ok(gate(FIXTURE, 'continuity', REF).ok, '承接紧挨着的同场上一段：连续性门通过');

  const later = gate(withSeg2((seg) => {
    seg.h3Prompt = seg.h3Prompt.replace('the last frame of <Subject 5>, ', '').replace('\n[Shot 3]', ' Echoing <Subject 5>.\n[Shot 3]');
  }), 'h3-references', REF);
  ok(later.detail.includes('要在 [Shot 1] 里用上'), '尾帧只在后面的镜头用上被拦');

  const stray = gate(withSeg2((seg) => { delete seg.continuesFrom; }), 'h3-references', REF);
  ok(stray.detail.includes('是上一段 E01-01 的尾帧，但画布上这一段不会连它的参考图'), '不承接却定义尾帧 Subject 被拦');

  const unlinked = gate(withSeg2((seg) => { delete seg.continuesFrom; }), 'same-scene-continues-from', REF);
  ok(!unlinked.ok && unlinked.detail.includes('E01-02') && unlinked.detail.includes('应为「E01-01」'), '同场第二段不承接被拦，报错写明该接哪一段');
  ok(gate(withSeg2((seg) => { delete seg.continuesFrom; }), 'same-scene-continues-from').ok, '关键帧导出模式不要求承接');

  const wrongId = gate(withSeg2((seg) => { seg.continuesFrom = 'E01-05'; }), 'continuity', REF);
  ok(!wrongId.ok && wrongId.detail.includes('只能写紧挨着的上一段 E01-01'), 'continuesFrom 写的不是上一段被拦');

  const crossScene = clone(FIXTURE);
  crossScene.episodes[0].segments[3].continuesFrom = 'E01-03';
  const cross = gate(crossScene, 'continuity', REF);
  ok(!cross.ok && cross.detail.includes('换了场次（1 → 2）'), '跨场承接被拦');
}

{
  // 用户没定时长：每集总时长按剧本预估（编剧定长）对账
  const untimed = clone(SCRIPT);
  for (const ep of untimed.episodes) delete ep.targetSeconds;
  const ctx = { ...CTX, script: untimed };
  ok(gate(FIXTURE, 'ep-duration', ctx).ok, '没定目标时样例按剧本预估通过');
  const st = computeStats(FIXTURE, untimed);
  ok(st.totals.scriptLength && st.totals.targetSeconds > 100 && st.totals.targetSeconds < 110, '统计换成剧本预估');
  const doc = clone(FIXTURE);
  for (const s of doc.episodes[0].segments) for (const c of s.cuts) c.seconds = Math.max(2, c.seconds - 2);
  ok(gate(doc, 'ep-duration', ctx).detail.includes('剧本预估'), '没定目标时写欠按剧本预估拦');
  ok(renderMarkdown(FIXTURE, ctx).includes('剧本预估'), 'MD 每集写剧本预估');
}

console.log(`✓ ${passed} 项自测全部通过`);
