#!/usr/bin/env node
// cine-storyboard — deterministic helpers for the cine-storyboard skill (分镜).
// Zero npm dependencies on purpose: the skill must work in any directory
// without an npm install. Node 18+ stdlib plus ../../_shared/cine-common.mjs.

import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { basename, join, resolve } from 'node:path';
import { embedJson, esc, flag, gateTextFor, i18nLookup, mdHead, mdRow, r1, readJson, runCli, safeStem } from '../../_shared/cine-common.mjs';

/* ------------------------------------------------------------------ */
/* 常量                                                                */
/* ------------------------------------------------------------------ */
/*
 * AI 短剧的前提刻在骨子里，三层结构也由此而来：
 *
 *   段（segment）＝ 一次视频生成调用，上限就是模型单段时长（默认 15 秒）
 *   分镜（cut）  ＝ 段内的一次剪切，2–5 秒——短剧观众的注意力节奏
 *   分镜图       ＝ 每个分镜一张关键帧：第 1 个分镜的是主分镜图（钉在
 *                  0.00 秒），其余是子分镜图（各钉在自己的切点时刻）
 *
 * 一段的画面由这串分镜图 + 一条 H3 提示词共同控制：多图对齐指令
 * 把每张图钉在对应秒数上，[Shot k] 的切点时刻和分镜秒数逐一对账。
 * 多切一刀的成本几乎为零，所以不心疼分镜数量，只守节奏。
 */

export const DEFAULT_PARAMS = {
  minSegmentSeconds: 4,  // 视频模型单段生成下限（秒，如 AutoDL H3 限制 ≥4 秒）
  maxSegmentSeconds: 15, // 视频模型单段生成上限（秒）
  minCutSeconds: 2,      // 单个分镜下限
  maxCutSeconds: 5,      // 单个分镜上限——3 秒左右是短剧的呼吸
  maxOnScreen: 3,        // 单个分镜同框人数上限，超了必须带拆解说明
  tolerance: 0.15,       // 每集总时长对剧本目标的容差
};

export function paramsOf(doc) {
  return { ...DEFAULT_PARAMS, ...(doc?.params ?? {}) };
}

/** 景别枚举：英文短语必须出现在该分镜的分镜图提示词里。 */
export const SHOT_SIZES = {
  'extreme-wide': { zh: '大远景', phrase: 'extreme wide shot' },
  wide: { zh: '全景', phrase: 'wide shot' },
  medium: { zh: '中景', phrase: 'medium shot' },
  close: { zh: '特写', phrase: 'close-up' },
  'extreme-close': { zh: '大特写', phrase: 'extreme close-up' },
};

/** 运镜枚举：直接用 H3 官方词表，原样写进该分镜的 [Shot k] 段落。 */
export const CAMERA_MOVES = {
  'Static Shot': '固定',
  'Push In': '推',
  'Pull Out': '拉',
  'Zoom In': '变焦推',
  'Zoom Out': '变焦拉',
  'Pan Left': '左摇',
  'Pan Right': '右摇',
  'Truck Left': '左移',
  'Truck Right': '右移',
  'Tilt Up': '仰摇',
  'Tilt Down': '俯摇',
  'Pedestal Up': '升',
  'Pedestal Down': '降',
  'Arc Shot': '环绕',
  'Tracking Shot': '跟拍',
  'Shake Slightly': '轻微晃动',
  'Shake Strongly': '强烈晃动',
  'POV': '主观视角',
  'Roll Clockwise': '顺旋',
  'Roll Counterclockwise': '逆旋',
};

/** 固定镜头的段落里不该出现「镜头在动」的写法，否则视频模型拿到的是自相矛盾的指令。 */
const CAMERA_MOVING = /\bcamera\s+(?:[a-z]+ly\s+)?(?:pans?|sweeps?|tracks?|follows?|pushes|pulls?|dolly|dollies|tilts?|cranes?|glides?|arcs?|orbits?|zooms?|swings?|whips?|drifts?|moves?|trucks?|travels?|circles?|rises?|lowers?|descends?|tightens?|widens?|creeps?|slides?|rolls?)\b|镜头(?:缓缓|慢慢|缓慢|快速|迅速|随之|随即|逐渐|一路)?(?:推进|推近|拉远|拉开|摇向|摇到|横摇|横移|跟随|跟着|上升|下降|环绕|旋转|甩向)/i;

/** 分镜图风格预设：与 cine-characters / cine-art 同名对齐（realistic / ghibli）。
 *  短语必须出现在每条分镜图提示词里——同一部剧的分镜图不许画风漂。 */
export const STYLE_PRESETS = {
  realistic: {
    zh: '半写实厚涂', phrase: 'semi-realistic painterly',
    sentence: 'The target video uses a semi-realistic painterly style with visible brush texture, grounded proportions and softly layered light matching the cast and art references.',
  },
  ghibli: {
    zh: '吉卜力手绘', phrase: 'hand-painted anime',
    sentence: 'The target video uses a hand-painted anime style with soft watercolour shading, readable shapes and gentle painted edges matching the cast and art references.',
  },
};
export const DEFAULT_STYLE = 'realistic';

const CJK = /[㐀-鿿぀-ヿ가-힯]/;

/* ------------------------------------------------------------------ */
/* H3 提示词的确定性骨架                                                 */
/* ------------------------------------------------------------------ */
/*
 * 结构由 H3 官方规范（h3-prompt-writing skill）定死，而且对齐指令和
 * 切点时刻都能从分镜结构推导出来——所以逐字设门，一个字符都不许漂。
 */

export const H3_I2VA_LINE =
  'For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.';
export const H3_FIELDS = ['integrated_multimodal_description:', 'overall_soundscape:', 'non_diegetic_music:'];

/** 骨架 token 按语言取：默认英文（官方规范口径）；'zh' 整条中文（只保留 <d>[Chinese] 和 (S1) 两个模型级 token）。 */
export const H3_TOKENS = {
  zh: {
    i2va: '目标视频在 0.00 秒处完全参照图 1（来自镜头 1）。',
    alignHead: '参考图与目标视频的对齐——',
    alignItem: (k, t) => `图 ${k}（来自镜头 ${k}）对齐目标视频 ${t} 秒处`,
    alignTail: '。',
    fields: ['整体视听描述：', '整体音景：', '非叙事配乐：'],
    shot: (k) => `[镜头 ${k}]`,
    cutMark: (k, time) => `[镜头 ${k}] 于 ${time}，`,
  },
  en: {
    i2va: H3_I2VA_LINE,
    alignHead: 'How the reference pictures align with the target video — ',
    alignItem: (k, t) => `Picture ${k} (from Shot ${k}) aligns with the ${t}-second mark of the target video`,
    alignTail: '.',
    fields: H3_FIELDS,
    shot: (k) => `[Shot ${k}]`,
    cutMark: (k, time) => `[Shot ${k}] At ${time},`,
  },
};

/** 段内切点时刻表：[0, c1, c1+c2, …]（不含结尾）。 */
export function cutStarts(cuts) {
  const starts = [];
  let t = 0;
  for (const c of cuts ?? []) {
    starts.push(r1(t));
    t += c?.seconds ?? 0;
  }
  return starts;
}

/** [Shot k] 的切点时刻格式：00:03.000（分:秒.毫秒）。 */
export function h3CutTime(t) {
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  const ms = Math.round((t - Math.floor(t)) * 1000);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${String(ms).padStart(3, '0')}`;
}

/**
 * 首行对齐指令：单分镜的段用 I2VA 固定句式；多分镜的段把每张分镜图
 * 钉在自己的切点秒数上。整行由分镜结构推导，validate 逐字对账。
 */
export function h3AlignmentLine(cuts, lang = 'en') {
  const tk = H3_TOKENS[lang] ?? H3_TOKENS.zh;
  if (!cuts || cuts.length <= 1) return tk.i2va;
  const starts = cutStarts(cuts);
  const parts = cuts.map((c, i) => tk.alignItem(i + 1, starts[i].toFixed(2)));
  return `${tk.alignHead}${parts.join(lang === 'en' ? '; ' : '；')}${tk.alignTail}`;
}

/** 台词/画面文字之外的部分——H3 要求它全英文，人名也只许出现在 <d> 里。 */
export function h3Remainder(prompt) {
  return String(prompt ?? '')
    .replace(/<d>[\s\S]*?<\/d>/g, ' ')
    .replace(/"[^"\n]*"/g, ' ');
}

/** 把 h3Prompt 的描述正文按 [镜头 k] / [Shot k] 切成每个分镜自己的段落。 */
export function h3CutSlices(prompt, cutCount, lang = 'en') {
  const tk = H3_TOKENS[lang] ?? H3_TOKENS.zh;
  const h3 = String(prompt ?? '');
  const ref2va = h3.includes('subject_definitions:');
  const bodyStart = h3.indexOf(ref2va ? 'detailed_description:' : tk.fields[0]);
  const bodyEnd = h3.indexOf(ref2va ? 'overall_soundscape:' : tk.fields[1]);
  if (bodyStart < 0) return [];
  const body = h3.slice(bodyStart, bodyEnd < 0 ? undefined : bodyEnd);
  const slices = [];
  for (let k = 1; k <= cutCount; k++) {
    const a = body.indexOf(tk.shot(k));
    if (a < 0) {
      slices.push(null);
      continue;
    }
    const b = body.indexOf(tk.shot(k + 1));
    slices.push(body.slice(a, b < 0 ? undefined : b));
  }
  return slices;
}

/* ------------------------------------------------------------------ */
/* Ref2VA 参考对账                                                       */
/* ------------------------------------------------------------------ */
/*
 * 画布上的视频卡只连角色三视图和场景概念图，没有逐镜关键帧。导入画布时按
 * <Subject N> 定义里写明的 ID / 名字 / 别名认出它是哪张参考图，再按画布顺序
 * 重排 <Picture N>——下面的规则与导入端逐条一致，这里过了导入才对得上。
 */

const SUBJECT_LINE = /<Subject (\d+)> — ([^\n]+)/g;
const SUBJECT_TOKEN = /<Subject\s*(\d+)>/g;
const PICTURE_TOKEN = /<Picture\s*\d+>/;

/** subject_definitions 段里的 <Subject N> — 定义，按编号。 */
export function subjectDefinitions(h3) {
  const text = String(h3 ?? '');
  const start = text.indexOf('subject_definitions:');
  const end = text.indexOf('summary:');
  const defs = new Map();
  if (start < 0 || end < 0) return defs;
  for (const m of text.slice(start, end).matchAll(SUBJECT_LINE)) defs.set(Number(m[1]), m[2]);
  return defs;
}

/**
 * 角色卡挂到大纲 ID 上（与导入端 _resolve_cast 同一规则）：卡自带的 ID 是大纲 ID
 * （或没有大纲）就直接用；否则按名字、再按名字加别名唯一对上一个大纲角色；
 * 对不上或两张卡抢一个 ID 的都不算。
 */
export function castView(cast, outline) {
  const word = (v) => (typeof v === 'string' ? v.trim().toLowerCase() : '');
  const outlineChars = (outline?.characters ?? []).filter((c) => word(c?.id));
  const outlineIds = new Set(outlineChars.map((c) => word(c.id)));
  const byName = new Map();
  const byWord = new Map();
  const put = (table, key, id) => {
    if (!key) return;
    if (!table.has(key)) table.set(key, new Set());
    table.get(key).add(id);
  };
  for (const c of outlineChars) {
    put(byName, word(c.name), c.id);
    for (const w of [c.name, ...(c.aliases ?? [])]) put(byWord, word(w), c.id);
  }
  const lookup = (table, words) => new Set(words.flatMap((w) => [...(table.get(word(w)) ?? [])]));
  const claims = new Map();
  for (const card of cast?.characters ?? []) {
    if (!card || typeof card !== 'object') continue;
    const own = word(card.id);
    let target = null;
    if (own && (!outlineChars.length || outlineIds.has(own))) {
      target = outlineChars.find((c) => word(c.id) === own)?.id ?? card.id;
    } else if (outlineChars.length) {
      let hits = lookup(byName, [card.name]);
      if (!hits.size) hits = lookup(byWord, [card.name, ...(card.aliases ?? [])]);
      if (hits.size === 1) target = [...hits][0];
    }
    if (!target) continue;
    if (!claims.has(target)) claims.set(target, []);
    claims.get(target).push(card);
  }
  return {
    characters: [...claims].filter(([, cards]) => cards.length === 1).map(([id, [card]]) => ({ ...card, id })),
  };
}

/** 项目里每个角色、场景的 ID / 名字 / 别名；两个实体共用的词谁也不代表，长词优先。 */
export function entityTerms(cast, art) {
  const owners = new Map();
  const claim = (term, key) => {
    if (typeof term !== 'string' || term.trim().length < 2) return;
    const t = term.trim().toLowerCase();
    if (!owners.has(t)) owners.set(t, new Set());
    owners.get(t).add(key);
  };
  for (const c of cast?.characters ?? []) {
    if (!c?.id) continue;
    for (const term of [c.id, c.name, ...(c.aliases ?? [])]) claim(term, `char:${c.id}`);
  }
  for (const sc of art?.scenes ?? []) {
    if (!sc?.id) continue;
    for (const term of [sc.id, sc.name, ...(sc.aliases ?? [])]) claim(term, `scene:${sc.id}`);
  }
  return [...owners]
    .filter(([, keys]) => keys.size === 1)
    .map(([term, keys]) => [term, [...keys][0]])
    .sort((a, b) => b[0].length - a[0].length);
}

const escapeRe = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** 一行 <Subject N> 定义写明的唯一实体（char:C01 / scene:S02）；没写或写了两个就是 null。 */
export function identifySubject(desc, terms) {
  let rest = String(desc ?? '').toLowerCase();
  const found = new Set();
  for (const [term, key] of terms) {
    // ASCII 的 ID / 名字按整词匹配：C1 不能命中 C10
    const re = /^[a-z0-9_-]+$/.test(term)
      ? new RegExp(`(?<![a-z0-9])${escapeRe(term)}(?![a-z0-9])`, 'g')
      : new RegExp(escapeRe(term), 'g');
    if (re.test(rest)) {
      found.add(key);
      rest = rest.replace(re, ' ');
    }
  }
  return found.size === 1 ? [...found][0] : null;
}

/**
 * 画布模式下一段的参考对账：本段连的场景图和每个出镜角色的三视图都要有一行
 * <Subject N> 定义（写明 ID、引用来源 <Picture N>），并在 detailed_description 里用上。
 */
export function ref2vaReferenceProblems(sid, h3, required, terms) {
  const problems = [];
  const text = String(h3 ?? '');
  if (!text.includes('subject_definitions:')) {
    problems.push(`${sid} 画布上这段只连角色三视图和场景概念图：h3Prompt 必须用 Ref2VA 六段式（subject_definitions 开头），不能写首帧对齐行把设定图当成镜头画面`);
    return problems;
  }
  const defs = subjectDefinitions(text);
  if (defs.size === 0) {
    problems.push(`${sid} subject_definitions 里没有「<Subject N> — …」定义行`);
    return problems;
  }
  const nums = [...defs.keys()].sort((a, b) => a - b);
  if (nums.some((n, i) => n !== i + 1)) problems.push(`${sid} <Subject N> 要从 1 起连续编号`);
  const bodyStart = text.indexOf('detailed_description:');
  const bodyEnd = text.indexOf('overall_soundscape:');
  const body = bodyStart < 0 ? '' : text.slice(bodyStart, bodyEnd < 0 ? undefined : bodyEnd);
  const used = new Set([...body.matchAll(SUBJECT_TOKEN)].map((m) => Number(m[1])));
  const firstShot = new Set([...(body.match(/\[(?:Shot|镜头) 1\][^\n]*/)?.[0] ?? '').matchAll(SUBJECT_TOKEN)].map((m) => Number(m[1])));
  const label = (key) => {
    const [kind, id] = key.split(':');
    return kind === 'frame' ? `上一段 ${id} 的尾帧` : `${kind === 'scene' ? '场景' : '角色'} ${id}`;
  };
  const defined = new Map();
  for (const [num, desc] of defs) {
    const key = identifySubject(desc, terms);
    if (!key) {
      problems.push(`${sid} <Subject ${num}> 要写明它是哪个角色、场景或哪一段的尾帧（写 ID，如 C01、S02、E01-01）——导入画布时靠它对上参考图`);
      continue;
    }
    if (defined.has(key)) problems.push(`${sid} <Subject ${defined.get(key)}> 和 <Subject ${num}> 都是${label(key)}`);
    defined.set(key, num);
    if (!required.includes(key)) problems.push(`${sid} <Subject ${num}> 是${label(key)}，但画布上这一段不会连它的参考图`);
    if (!PICTURE_TOKEN.test(desc)) problems.push(`${sid} <Subject ${num}> 的定义里要引用它的来源图 <Picture N>`);
    if (!used.has(num)) problems.push(`${sid} <Subject ${num}>（${label(key)}）在 detailed_description 里一次都没用上`);
    else if (key.startsWith('frame:') && !firstShot.has(num)) {
      problems.push(`${sid} <Subject ${num}>（${label(key)}）要在 [Shot 1] 里用上：这一段从它接着开始`);
    }
  }
  for (const key of required) {
    if (!defined.has(key)) problems.push(`${sid} 画布上这段连了${label(key)} 的参考图，subject_definitions 里没有定义它`);
  }
  return problems;
}

/* ------------------------------------------------------------------ */
/* 剧本节拍展开                                                          */
/* ------------------------------------------------------------------ */
/*
 * 与 cine-script 相同的计秒规则，这里刻意重新实现而不是跨目录
 * import——每个 skill 必须自包含、可以单独拷走。参数从 script.json
 * 的 params 里读，两边天然一致。
 */

const SCRIPT_DEFAULTS = { charsPerSecond: 4.5, actionSeconds: 2.5 };
const lineChars = (line) => String(line ?? '').replace(/\s+/g, '').length;

/** 把 script.json 展开成分镜要认领的节拍清单：ep → scenes → beats。 */
export function expandScript(script) {
  const p = { ...SCRIPT_DEFAULTS, ...(script?.params ?? {}) };
  const eps = new Map();
  for (const ep of script?.episodes ?? []) {
    const scenes = (ep?.scenes ?? []).map((sc, i) => ({
      sceneIndex: i + 1,
      sceneId: sc.sceneId,
      lighting: sc.lighting ?? '',
      characters: sc.characters ?? [],
      characterStates: sc.characterStates ?? {},
      props: sc.props ?? [],
      beats: (sc.flow ?? []).map((b, j) => {
        const isLine = typeof b?.line === 'string';
        return {
          n: j + 1,
          kind: isLine ? 'line' : 'action',
          seconds: r1(isLine ? lineChars(b.line) / p.charsPerSecond : p.actionSeconds),
          speaker: isLine ? b.speaker : undefined,
          delivery: isLine ? (b.delivery ?? '') : undefined,
          text: isLine ? b.line : b.action,
        };
      }),
    }));
    eps.set(ep.ep, { ep: ep.ep, targetSeconds: ep.targetSeconds, scenes });
  }
  return eps;
}

export const segSeconds = (segment) => r1((segment?.cuts ?? []).reduce((n, c) => n + (c?.seconds ?? 0), 0));

/* ------------------------------------------------------------------ */
/* Cinematic audit (read-only)                                          */
/* ------------------------------------------------------------------ */

const AUDIT_RULES = [
  'style-consistency', 'video-no-film-still', 'no-background-music',
  'dialogue-speaker-visible', 'one-speaker-per-cut', 'same-scene-continues-from',
  'no-internal-labels', 'consistent-character-naming',
  'h3-style-before-shot1', 'h3-subject-inline-binding', 'h3-referenced-speaker-binding',
  'h3-vocal-source-ids', 'h3-nonverbal-vocal-cues', 'h3-summary-task-prefix', 'h3-retention-per-line',
  'h3-synchronized-sound-placement', 'h3-voiceover-exact-format',
];
const AUDIT_REF_RULES = AUDIT_RULES.filter((rule) => rule.startsWith('h3-'));
const auditText = (value) => typeof value === 'string' ? value.trim() : '';
const auditWord = (value) => auditText(value).toLowerCase();
const auditVo = (beat) => auditWord(beat?.speaker) === 'vo' ||
  /\b(?:v\.?o\.?|voice[ -]?over)\b|画外音|内心|独白|旁白|心声/i.test(auditText(beat?.delivery));
const auditVoPrompt = (text) => /\b(?:off[ -]screen(?:\s+voice[ -]?over)?|voice[ -]?over|v\.?o\.?)\b|画外音|旁白|心声|内心独白/i.test(text);
const AUDIT_INTERNAL_LABEL = /(?<![a-z0-9_])(?:extra|unclear|non_story|speaker[_-]\d+|person[_-]\d+|[PC]\d+)(?![a-z0-9_])/gi;

function auditLabels(text) {
  return [...text.matchAll(AUDIT_INTERNAL_LABEL)].filter((match) => {
    if (match[0].toLowerCase() !== 'extra') return true;
    const before = text.slice(0, match.index);
    const after = text.slice(match.index + match[0].length);
    return /^[\s]*$/.test(before + after) || /[<\[]\s*$/.test(before) ||
      /^\s*(?:[:：>\]]|[㐀-鿿]|(?:says|speaks|asks|replies|opens|closes|looks|walks|enters|leaves|holds)\b)/i.test(after);
  }).map((match) => match[0]);
}

function auditMedium(text) {
  const value = h3Remainder(text)
    .replace(/\b(?:framed|composed|posed|cropped)\s+(?:like|as)\s+(?:an?\s+)?(?:ID|passport|portrait)\s+photograph\b/gi, '')
    .replace(/\b(?:no|not|without|avoid)\s+(?:photorealistic|photographic|live[ -]action|3d\s+render(?:ing)?)\b/gi, '');
  const families = [];
  if (/\blive[ -]action\b|\bphoto(?:realistic|graph(?:ic|y)?)\b|真人实拍|照片写实/i.test(value)) families.push('live-action');
  if (/\banime\b|\bghibli\b|吉卜力|手绘动画/i.test(value)) families.push('anime');
  else if (/\bcartoon\b|\b2d\s+animation\b|卡通|二维动画/i.test(value)) families.push('cartoon');
  else if (/\bpainterly\b|\bhand[ -]painted\b|\b(?:oil|watercolou?r)\s+paint(?:ing|ed)\b|绘画风|油画|水彩/i.test(value)) families.push('painted');
  if (/\b3d\s+(?:render|animation)\b|三维动画/i.test(value)) families.push('3d');
  return families;
}

function auditMusic(prompt) {
  const match = /(?:non_diegetic_music:|非叙事配乐：)([\s\S]*)/.exec(prompt);
  return match ? match[1].trim() : '';
}

function auditPromptBody(prompt) {
  // IDs are required in Ref2VA definitions; they are metadata, not creative prose.
  const summary = prompt.indexOf('summary:');
  return prompt.includes('subject_definitions:') && summary >= 0 ? prompt.slice(summary) : prompt;
}

function auditDialogueVo(shot, line) {
  let previousEnd = 0;
  for (const match of shot.matchAll(/<d>\[[^\]]+\]\s*([\s\S]*?)\s*<\/d>/g)) {
    const marked = auditVoPrompt(shot.slice(previousEnd, match.index));
    previousEnd = match.index + match[0].length;
    if (match[1].trim() === line && marked) return true;
  }
  return false;
}

function auditSections(prompt) {
  const headers = [...prompt.matchAll(/(?:^|\n)\s*(subject_definitions|summary|retention_analysis|detailed_description|overall_soundscape|non_diegetic_music):/g)];
  return new Map(headers.map((header, i) => [header[1], prompt.slice(header.index + header[0].length, headers[i + 1]?.index).trim()]));
}

function auditDialogueEvents(body) {
  const events = [];
  let previousEnd = 0;
  for (const match of body.matchAll(/<d>(?:\[[^\]]+\])?\s*([\s\S]*?)\s*<\/d>/g)) {
    const prefix = body.slice(previousEnd, match.index);
    const sentence = prefix.split(/(?<=[.!?])\s+|\[Shot \d+\](?:\s+At \d+:\d+\.\d+,)?\s*/).at(-1).trim().replace(/\s+/g, ' ');
    const end = match.index + match[0].length;
    events.push({ prefix: sentence, line: match[1].trim(), after: body.slice(end), index: match.index });
    previousEnd = end;
  }
  return events;
}

function auditVocalEvents(body) {
  const events = auditDialogueEvents(body);
  // Keep offsets while excluding quoted dialogue from non-verbal cue matching.
  const prose = body.replace(/<d>[\s\S]*?<\/d>/g, (block) => ' '.repeat(block.length));
  const cues = /\b(?:cheers?|cheering|laughs?|laughing|laughter|sobs?|sobbing|gasps?|gasping|sighs?|sighing|coughs?|coughing)\b/gi;
  for (const cue of prose.matchAll(cues)) {
    const before = prose.slice(0, cue.index).split(/[.!?\n]/).at(-1);
    if (/\b(?:no|not|without|silent|silently)\s+(?:an?\s+)?(?:audible\s+)?$/i.test(before)) continue;
    const subject = [...before.matchAll(/<Subject \d+>/g)].at(-1);
    const ids = [...before.matchAll(/\(S\d+(?:,S\d+)*\)/g)];
    if (!subject && !ids.length && !/\b(?:he|she|his|her|woman|man|worker|narrator|person|child|speaker)\b/i.test(before)) continue;
    const prefix = subject ? before.slice(subject.index) : before;
    events.push({ prefix: prefix.trim(), line: null, after: '', index: cue.index, cue: cue[0] });
  }
  return events.sort((a, b) => a.index - b.index);
}

function auditRefFormat(segment, prompt, { evaluated, issue, skip }, speakerSubjects, lang, extras = {}) {
  const sid = segment.id ?? '?';
  if (lang !== 'en' || !prompt.includes('subject_definitions:')) {
    const reason = lang !== 'en' ? 'Official Ref2VA guide specifies English sections; non-English prompt mode is unsupported by this format audit' : 'Not a Ref2VA prompt; official full-reference format was not evaluated';
    for (const rule of AUDIT_REF_RULES) skip(rule, `${sid}.h3Prompt`, reason);
    return;
  }
  const sections = auditSections(prompt);
  const body = sections.get('detailed_description') ?? '';
  const definitions = [...(sections.get('subject_definitions') ?? '').matchAll(/^\s*(<(Subject|Picture|Video|Audio) \d+>)([^\n]*)/gm)];
  const labels = new Map(definitions.map((match) => [match[1], match[2]]));
  const summaryRule = 'h3-summary-task-prefix';
  evaluated(summaryRule);
  const prefix = /^\[([^\]]+)\]\s*\S/.exec(sections.get('summary') ?? '');
  const taskTypes = new Set(['keyframe completion', 'reference generation', 'video editing', 'video continuation', 'audio reuse', 'audio reference']);
  const tasks = prefix?.[1].split(/\s*\+\s*/) ?? [];
  if (!tasks.length || tasks.some((task) => !taskTypes.has(task)) || new Set(tasks).size !== tasks.length) {
    issue(summaryRule, `${sid}.summary`, 'Summary must start with a bracketed combination of official task types', { prefix: prefix?.[1] ?? null });
  }
  if (segment.continuesFrom != null && !tasks.includes('keyframe completion')) {
    issue(summaryRule, `${sid}.summary`, 'A preceding segment tail-frame anchor requires keyframe completion in the task prefix', { continuesFrom: segment.continuesFrom });
  }

  const retentionRule = 'h3-retention-per-line';
  const retention = sections.get('retention_analysis') ?? '';
  const entries = [...retention.matchAll(/^\s*(<(Subject|Picture|Video|Audio) \d+>)([^\n]*)/gm)];
  if (definitions.length !== labels.size) {
    evaluated(retentionRule);
    issue(retentionRule, `${sid}.subject_definitions`, 'Each reference label must have one standalone definition');
  }
  if (!labels.size) skip(retentionRule, `${sid}.subject_definitions`, 'No standalone reference definitions can be parsed; label coverage cannot be determined');
  for (const [label, type] of labels) {
    evaluated(retentionRule);
    const lines = entries.filter((entry) => entry[1] === label);
    const allowed = type === 'Audio' ? ['fully_copy', 'partially_copy', 'reference', 'weak_reference'] : ['fully_preserved', 'partially_preserved', 'attribute_transfer', 'weak_reference'];
    const relation = lines.length === 1 ? /^([\s\S]*?)\b(fully_preserved|partially_preserved|attribute_transfer|weak_reference|fully_copy|partially_copy|reference)\b\s*[:\-–—]?\s*(\S[\s\S]*)$/.exec(lines[0][3]) : null;
    const shotRefs = [...(relation?.[1] ?? '').matchAll(/\[Shot (\d+)\]/g)];
    const hasShots = !['Subject', 'Picture'].includes(type) || shotRefs.length > 0;
    const knownShots = shotRefs.every((shot) => body.includes(shot[0]));
    if (!relation || !allowed.includes(relation[2]) || /<(?:Subject|Picture|Video|Audio) \d+>/.test(relation[1]) || !hasShots || !knownShots || /\(S\d+(?:,S\d+)*\)/.test(lines[0]?.[3] ?? '')) {
      issue(retentionRule, `${sid}.retention_analysis.${label}`, 'Each standalone label needs one line, a role-appropriate relationship marker and explanation; visible subject/frame entries must identify existing shots and retention has no speaker IDs', { label, lines: lines.map((entry) => entry[0].trim()) });
    }
  }
  for (const entry of entries.filter((entry) => !labels.has(entry[1]))) {
    evaluated(retentionRule);
    issue(retentionRule, `${sid}.retention_analysis.${entry[1]}`, 'Retention entry has no standalone definition; provenance-only labels do not need their own entry', { line: entry[0].trim() });
  }

  const soundRule = 'h3-synchronized-sound-placement';
  const soundscape = sections.get('overall_soundscape') ?? '';
  evaluated(soundRule);
  if (!soundscape) issue(soundRule, `${sid}.overall_soundscape`, 'Overall soundscape is missing or empty');
  else if (/\[Shot \d+\]|\b(?:At\s+)?\d{1,2}:\d{2}(?:\.\d+)?\b|\bat\s+\d+(?:\.\d+)?\s*(?:seconds?|s)\b|\b(?:synchronized|on impact|at (?:the |that )?(?:exact )?(?:moment|instant))\b|<d>/i.test(soundscape)) {
    issue(soundRule, `${sid}.overall_soundscape`, 'Shot/time-bound events or dialogue belong in the corresponding detailed-description shot', { soundscape });
  } else if (/\b(?:rings? once|slams?|crash(?:es)?|clicks? once|bangs? once|footsteps?|laughter|door (?:closure|closing)|structural rumble|ominous drone|immediately after|as soon as|transitions? into)\b|\brustl(?:e|ing)\b[^.!?\n]{0,50}\b(?:loose cash|banknotes|tailored fabrics|clothing|garments)\b/i.test(soundscape)) {
    issue(soundRule, `${sid}.overall_soundscape`, 'Discrete sound events belong in their shot; overall soundscape contains only continuous ambience', { soundscape });
  }

  if (!body) {
    for (const rule of ['h3-style-before-shot1', 'h3-subject-inline-binding', 'h3-referenced-speaker-binding', 'h3-vocal-source-ids', 'h3-voiceover-exact-format']) {
      skip(rule, `${sid}.detailed_description`, 'Detailed description is unavailable; this format check cannot be evaluated');
    }
    return;
  }
  const styleRule = 'h3-style-before-shot1';
  evaluated(styleRule);
  const shot1 = body.indexOf('[Shot 1]');
  const opening = shot1 >= 0 ? body.slice(0, shot1).trim() : '';
  const proseOpening = opening.replace(/<(?:Subject|Picture|Video|Audio) \d+>/g, '').replace(/\(S\d+(?:,S\d+)*\)/g, '').trim();
  if (shot1 < 0 || !/[a-z]{2}/i.test(proseOpening) || /^N\/A[.!]?$/i.test(proseOpening)) {
    issue(styleRule, `${sid}.detailed_description`, 'Ref2VA needs English opening prose before [Shot 1]', { opening });
  } else if ((proseOpening.match(/[.!?](?:\s|$)/g) ?? []).length !== 1) {
    issue(styleRule, `${sid}.detailed_description`, 'Use one complete reference-derived style sentence before [Shot 1]', { opening });
  }

  const bindingRule = 'h3-subject-inline-binding';
  for (const [label, type] of labels) {
    if (type !== 'Subject') continue;
    evaluated(bindingRule);
    const first = body.indexOf(label);
    const before = first >= 0 ? body.slice(0, first).split(/(?<=[.!?])\s+|\[Shot \d+\]/).at(-1) : '';
    const after = first >= 0 ? body.slice(first + label.length).split(/[.!?]\s+|\[Shot \d+\]/)[0] : '';
    const clause = (before + label + after).replace(/\[Shot \d+\]|<(?:Subject|Picture|Video|Audio) \d+>|\(S\d+(?:,S\d+)*\)/g, '').replace(/[^a-z\s]/gi, ' ').trim();
    const labelList = /\b(?:on\s+screen|visible\s+(?:subjects|references))\s*:[^.!?]*$/i.test(before) || /^(?:(?:and|in|on|with|at|from|the|screen|visible|subjects|references)\s*)*$/i.test(clause);
    if (first < 0 || labelList) issue(bindingRule, `${sid}.detailed_description.${label}`, 'First label use must occur in descriptive prose, rather than a detached subject list', { label, first_clause: first < 0 ? null : before + label + after });
  }

  const events = auditVocalEvents(body);
  const subjectIds = new Map();
  const idSubjects = new Map();
  const vocalIds = new Set();
  let dialogueCount = 0;
  let cueCount = 0;
  for (const event of events) {
    // Non-verbal cues (sobs, laughter …) stay advisory: the official guide numbers speech and singing.
    const cue = event.cue != null;
    const location = cue ? `${sid}.vocalcue${++cueCount}` : `${sid}.dialogue${++dialogueCount}`;
    const sourceRule = cue ? 'h3-nonverbal-vocal-cues' : 'h3-vocal-source-ids';
    const speakerRule = cue ? 'h3-nonverbal-vocal-cues' : 'h3-referenced-speaker-binding';
    const ids = [...event.prefix.matchAll(/\(S\d+(?:,S\d+)*\)/g)];
    const bound = [...event.prefix.matchAll(/(<Subject \d+>)\s+(\(S\d+(?:,S\d+)*\))/g)];
    const audioLabels = [...event.prefix.matchAll(/<Audio \d+>/g)].map((match) => match[0]);
    const copiedAudioCue = !/<Subject \d+>|\b(?:narrator|woman|man|person|character|child|speaker)\b/i.test(event.prefix) && /\b(?:phrase|cue|lyric|track|soundtrack|reaches|plays)\b/i.test(event.prefix) && audioLabels.some((label) => entries.some((entry) => entry[1] === label && /\b(?:fully_copy|partially_copy)\b/.test(entry[3])));
    evaluated(sourceRule);
    if ((!ids.length || ids.some((match) => /\bS0\b/.test(match[0]))) && !copiedAudioCue) {
      issue(sourceRule, location, cue
        ? `Non-verbal cue "${event.cue}" has no vocal source ID`
        : 'Independent dialogue/singing/voice-over sources require IDs starting at S1; only cues inside a directly copied audio track may use Audio alone',
      { source_clause: event.prefix, line: event.line, ...(cue ? { cue: event.cue } : {}) });
    }
    for (const id of ids.flatMap((match) => match[0].match(/S\d+/g))) {
      if (vocalIds.has(id)) continue;
      const expectedId = `S${vocalIds.size + 1}`;
      if (id !== expectedId) issue('h3-vocal-source-ids', location, 'New vocal sources must be numbered in their first-event order starting at S1', { expected: expectedId, actual: id });
      vocalIds.add(id);
    }
    const expected = speakerSubjects.get(event.line);
    if (!cue && extras.lines?.has(event.line) && !expected?.size) {
      // An unnamed extra voiced through a cast reference renders as that character's face and voice.
      evaluated(speakerRule);
      const borrowed = bound.filter((match) => extras.subjects?.has(match[1])).map((match) => match[1]);
      if (borrowed.length) {
        issue(speakerRule, location, `The script gives this line to an unnamed extra, but it is voiced as ${borrowed.join(', ')}, a cast reference: describe the extra as their own speaker with a new (Sx), or give the line to that character in the script`, { source_clause: event.prefix, line: event.line });
      }
    }
    const subjectMentioned = /^\s*<Subject \d+>/.test(event.prefix) || bound.length > 0;
    if (subjectMentioned || expected?.size) {
      evaluated(speakerRule);
      if (!bound.length || (expected?.size === 1 && !bound.some((match) => expected.has(match[1])))) {
        issue(speakerRule, location, cue
          ? `Non-verbal cue "${event.cue}" from a referenced subject is not written as <Subject N> (Sx)`
          : 'A referenced speaker must be written as <Subject N> (Sx) in the clause introducing this dialogue',
        { source_clause: event.prefix, expected_subjects: expected ? [...expected] : null, ...(cue ? { cue: event.cue } : {}) });
      } else if (expected?.size > 1) skip(speakerRule, location, 'The same dialogue words map to multiple reference subjects; speaker semantics are ambiguous');
      for (const match of bound) {
        if (subjectIds.has(match[1]) && subjectIds.get(match[1]) !== match[2]) issue('h3-vocal-source-ids', location, 'A referenced vocal source changes its speaker ID', { subject: match[1], previous: subjectIds.get(match[1]), current: match[2] });
        subjectIds.set(match[1], match[2]);
        for (const id of match[2].match(/S\d+/g)) {
          if (idSubjects.has(id) && idSubjects.get(id) !== match[1]) issue('h3-vocal-source-ids', location, 'Distinct referenced vocal sources share one speaker ID', { id, previous_subject: idSubjects.get(id), current_subject: match[1] });
          idSubjects.set(id, match[1]);
        }
      }
    }
    if (event.line !== null && /voice[ -]?over|\bv\.?o\.?\b|inner monologue|画外音|旁白|心声|内心独白/i.test(event.prefix)) {
      evaluated('h3-voiceover-exact-format');
      if (!event.prefix.includes('says in an off-screen voiceover') || !/^\s*while\s+[^.!?]*\blips remain completely closed\b/.test(event.after.replace(/\s+/g, ' '))) {
        issue('h3-voiceover-exact-format', location, 'Each voice-over requires exact says in an off-screen voiceover and an immediately following while ... lips remain completely closed clause', { source_clause: event.prefix, following_text: event.after.split(/\n/)[0] });
      }
    }
  }
}

/** A continuing segment opens on the previous last frame, so its first cut must be able to show it. */
function auditOpening(previous, segment, issue) {
  const last = (previous.cuts ?? []).at(-1);
  const first = (segment.cuts ?? [])[0];
  if (!last || !first) return;
  const describe = (cut) => [cut.size, (cut.characters ?? []).join('/') || 'no characters', cut.camera].filter(Boolean).join(' · ');
  const evidence = { previous_last: describe(last), first: describe(first) };
  const opening = new Set(first.characters ?? []);
  if ((last.characters ?? []).length && !last.characters.some((id) => opening.has(id))) {
    issue('same-scene-continues-from', `${segment.id}#1`, `The opening frame is ${previous.id}'s last frame, but the first cut shows none of its characters: start [Shot 1] on them, list them in the cut, and move the camera to the new subject`, evidence);
  } else if (last.camera === 'Static Shot' && first.camera === 'Static Shot' && last.size !== first.size) {
    issue('same-scene-continues-from', `${segment.id}#1`, `A static opening keeps ${previous.id}'s last framing: use its shot size or move the camera`, evidence);
  }
}

/** Read-only audit; selected rules also supply production validation gates. */
export function auditStoryboard(board, ctx = {}) {
  const issues = [];
  const skips = [];
  const evaluated_checks = AUDIT_RULES.map((id) => ({ id, evaluated: 0, skipped: 0, issues: 0 }));
  const rules = new Map(evaluated_checks.map((rule) => [rule.id, rule]));
  const evaluated = (rule) => { rules.get(rule).evaluated += 1; };
  const issue = (rule, location, detail, evidence = {}) => {
    issues.push({ rule, location, detail, evidence });
    rules.get(rule).issues += 1;
  };
  const skip = (rule, location, reason) => {
    skips.push({ rule, location, reason });
    rules.get(rule).skipped += 1;
  };
  const expanded = ctx.script ? expandScript(ctx.script) : null;
  const characters = castView(ctx.cast, ctx.outline).characters;
  const identityChars = characters.length ? characters : (ctx.outline?.characters ?? []);
  const characterIds = new Set(identityChars.map((c) => c.id));
  const nameOwners = new Map();
  for (const character of identityChars) {
    const outlined = (ctx.outline?.characters ?? []).find((c) => c.id === character.id);
    for (const term of [character.id, character.name, ...(character.aliases ?? []), outlined?.name, ...(outlined?.aliases ?? [])]) {
      if (!auditText(term)) continue;
      const word = auditWord(term);
      if (!nameOwners.has(word)) nameOwners.set(word, new Set());
      nameOwners.get(word).add(character.id);
    }
  }
  const resolveSpeaker = (speaker) => {
    const matches = nameOwners.get(auditWord(speaker));
    return matches?.size === 1 ? [...matches][0] : null;
  };
  const usage = new Map();
  const noteName = (id, name, location) => {
    if (!usage.has(id)) usage.set(id, new Map());
    const forms = usage.get(id);
    if (!forms.has(name)) forms.set(name, []);
    forms.get(name).push(location);
  };
  const prose = [];
  const collect = (location, value, names = true) => {
    if (typeof value === 'string') prose.push({ location, text: value, names });
  };
  const styleRule = 'style-consistency';
  let styleOpening = null;
  for (const [label, doc] of [['cast', ctx.cast], ['art', ctx.art]]) {
    if (!doc) skip(styleRule, label, `Missing ${label}.json; reference style metadata was not compared`);
    else {
      evaluated(styleRule);
      if ((doc.style ?? DEFAULT_STYLE) !== (board?.style ?? DEFAULT_STYLE)) {
        issue(styleRule, `${label}.style`, 'Reference and storyboard style IDs differ', {
          reference: doc.style ?? DEFAULT_STYLE, storyboard: board?.style ?? DEFAULT_STYLE,
        });
      }
    }
  }
  for (const episode of ctx.script?.episodes ?? []) {
    const epLabel = `script.E${String(episode.ep).padStart(2, '0')}`;
    collect(`${epLabel}.hook`, episode.hook, false);
    collect(`${epLabel}.cliff`, episode.cliff, false);
    for (const [sceneIndex, scene] of (episode.scenes ?? []).entries()) {
      for (const [beatIndex, beat] of (scene.flow ?? []).entries()) {
        const location = `${epLabel}.scene${sceneIndex + 1}.beat${beatIndex + 1}`;
        collect(`${location}.action`, beat.action);
        collect(`${location}.line`, beat.line, false);
        collect(`${location}.delivery`, beat.delivery);
        if (typeof beat.line !== 'string') continue;
        const speaker = auditText(beat.speaker);
        evaluated('no-internal-labels');
        if (speaker.toUpperCase() !== 'VO' && !characterIds.has(speaker) && auditLabels(speaker).length) {
          issue('no-internal-labels', `${location}.speaker`, 'An internal label is used as the speaker', { speaker });
        }
        const id = resolveSpeaker(speaker);
        if (id) {
          const name = characterIds.has(speaker) ? identityChars.find((c) => c.id === id)?.name : speaker;
          noteName(id, auditWord(name), `${location}.speaker`);
        } else if (speaker && speaker.toUpperCase() !== 'VO' && speaker !== 'extra') {
          skip('consistent-character-naming', `${location}.speaker`, `Speaker ${speaker} has no unique cast/outline mapping`);
        }
      }
    }
  }
  for (const episode of board?.episodes ?? []) {
    const scriptEpisode = expanded?.get(episode.ep);
    let previous = null;
    for (const segment of episode.segments ?? []) {
      const sid = segment.id ?? '?';
      const prompt = auditText(segment.h3Prompt);
      const scene = scriptEpisode?.scenes[segment.sceneIndex - 1];
      if (prompt.includes('subject_definitions:') && (board?.promptLang ?? 'en') === 'en') {
        const body = auditSections(prompt).get('detailed_description') ?? '';
        const opening = body.split('[Shot 1]')[0].trim();
        evaluated(styleRule);
        if (styleOpening == null) styleOpening = opening;
        const expected = board?.styleSentence ?? styleOpening;
        if (!opening || opening !== expected) {
          issue(styleRule, `${sid}.detailed_description.style`, 'Every segment must repeat the same reference-derived style sentence before [Shot 1]', { expected, actual: opening });
        }
      }
      evaluated('video-no-film-still');
      if (!prompt) issue('video-no-film-still', `${sid}.h3Prompt`, 'Video prompt is missing');
      else if (/\bfilm[\s-]+still\b/i.test(prompt)) {
        issue('video-no-film-still', `${sid}.h3Prompt`, 'Video prompt contains film still', { matches: prompt.match(/\bfilm[\s-]+still\b/gi) });
        evaluated(styleRule);
        issue(styleRule, `${sid}.h3Prompt`, 'Video prompts use the reference artwork medium, without film-still styling');
      }
      collect(`${sid}.h3Prompt`, auditPromptBody(prompt));
      collect(`${sid}.note`, segment.note);
      const music = auditMusic(prompt);
      evaluated('no-background-music');
      if (music !== 'N/A') issue('no-background-music', `${sid}.non_diegetic_music`, 'Audience-only music is added by the user in post-production; every segment must write N/A', { music: music || null });
      const speakerSubjects = new Map();
      const terms = entityTerms({ characters: identityChars }, ctx.art);
      const referenceCharacters = new Map();
      const definitionText = auditSections(prompt).get('subject_definitions') ?? '';
      for (const match of definitionText.matchAll(/^\s*(<Subject \d+>)([^\n]*)/gm)) {
        const entity = identifySubject(match[2], terms);
        if (entity?.startsWith('char:')) referenceCharacters.set(entity.slice(5), match[1]);
      }
      const extraLines = new Set();
      for (const cut of segment.cuts ?? []) {
        const [from, to] = cut.beats ?? [];
        if (!scene || !Number.isInteger(from) || !Number.isInteger(to) || from < 1 || from > to || to > scene.beats.length) continue;
        for (const beat of scene.beats.slice(from - 1, to).filter((beat) => beat.kind === 'line')) {
          if (auditWord(beat.speaker) === 'extra') extraLines.add(beat.text);
          const subject = referenceCharacters.get(resolveSpeaker(beat.speaker) ?? beat.speaker);
          if (!subject) continue;
          if (!speakerSubjects.has(beat.text)) speakerSubjects.set(beat.text, new Set());
          speakerSubjects.get(beat.text).add(subject);
        }
      }
      auditRefFormat(segment, prompt, { evaluated, issue, skip }, speakerSubjects, board?.promptLang ?? 'en', {
        lines: extraLines,
        subjects: new Set(referenceCharacters.values()),
      });
      if (previous) {
        if (!Number.isInteger(segment.sceneIndex) || !Number.isInteger(previous.sceneIndex)) {
          skip('same-scene-continues-from', sid, 'sceneIndex is missing or invalid');
        } else if (previous.sceneIndex === segment.sceneIndex) {
          evaluated('same-scene-continues-from');
          if (segment.continuesFrom !== previous.id) issue('same-scene-continues-from', sid, 'Consecutive segments in the same scene must continue from the previous segment: set continuesFrom and open [Shot 1] on its last frame as h3-prompt.md shows', { expected: previous.id, actual: segment.continuesFrom ?? null });
          else auditOpening(previous, segment, issue);
        } else if (segment.continuesFrom != null) {
          evaluated('same-scene-continues-from');
          issue('same-scene-continues-from', sid, 'continuesFrom crosses a scene boundary', { previous: previous.id, continuesFrom: segment.continuesFrom });
        }
      } else if (segment.continuesFrom != null) {
        evaluated('same-scene-continues-from');
        issue('same-scene-continues-from', sid, 'The first segment cannot continue from a previous segment', { continuesFrom: segment.continuesFrom });
      }
      const videoMedium = auditMedium(prompt);
      const references = (segment.cuts ?? []).map((cut, i) => ({ location: `${sid}#${i + 1}.frame`, text: cut.frame }));
      if (scene) {
        const artScene = (ctx.art?.scenes ?? []).find((item) => item.id === scene.sceneId);
        if (artScene?.image) references.push({ location: `art.${scene.sceneId}.image`, text: [artScene.image.prompt, artScene.image.sheet].filter(Boolean).join(' ') });
        else if (ctx.art) skip(styleRule, `art.${scene.sceneId}.image`, 'The used scene has no reference image prompt');
      }
      for (const id of new Set((segment.cuts ?? []).flatMap((cut) => cut.characters ?? []))) {
        const character = characters.find((item) => item.id === id);
        const states = new Set((segment.cuts ?? []).map((cut) => cut.characterStates?.[id]).filter(Boolean));
        const images = states.size ? [...states].map((state) => state === 'default' ? character?.image : character?.states?.find((item) => item.id === state)?.image) : [character?.image];
        for (const [i, image] of images.entries()) {
          if (image) references.push({ location: `cast.${id}.image${i + 1}`, text: [image.prompt, image.sheet].filter(Boolean).join(' ') });
          else if (ctx.cast) skip(styleRule, `cast.${id}.image${i + 1}`, 'The used character/state has no uniquely mapped reference image prompt');
        }
      }
      for (const reference of references) {
        if (reference.location.endsWith('.frame') && /\bfilm[\s-]+still\b/i.test(reference.text ?? '')) {
          evaluated(styleRule);
          issue(styleRule, reference.location, 'Frame prompts use the reference artwork medium, without film-still styling');
        }
        const referenceMedium = auditMedium(reference.text);
        if (!videoMedium.length || !referenceMedium.length) skip(styleRule, `${sid} <> ${reference.location}`, 'Explicit medium markers are missing; generic cinematic/realistic wording does not establish medium equivalence');
        else {
          evaluated(styleRule);
          if (videoMedium.length !== 1 || referenceMedium.length !== 1 || videoMedium[0] !== referenceMedium[0]) {
            issue(styleRule, `${sid} <> ${reference.location}`, 'Explicit reference and video medium markers conflict', { video: videoMedium, reference: referenceMedium });
          }
        }
      }
      const slices = h3CutSlices(prompt, (segment.cuts ?? []).length, board?.promptLang ?? 'en');
      for (const [cutIndex, cut] of (segment.cuts ?? []).entries()) {
        const cid = `${sid}#${cutIndex + 1}`;
        collect(`${cid}.frame`, cut.frame);
        collect(`${cid}.note`, cut.note);
        const [from, to] = cut.beats ?? [];
        if (!scene || !Number.isInteger(from) || !Number.isInteger(to) || from < 1 || from > to || to > scene.beats.length) {
          for (const rule of ['dialogue-speaker-visible', 'one-speaker-per-cut']) skip(rule, cid, 'Valid script scene and beat range are required');
          continue;
        }
        const lines = scene.beats.slice(from - 1, to).filter((beat) => beat.kind === 'line');
        evaluated('one-speaker-per-cut');
        const speakers = new Set(lines.map((beat) => resolveSpeaker(beat.speaker) ?? auditText(beat.speaker)));
        if (speakers.size > 1) issue('one-speaker-per-cut', cid, 'A cut contains lines from multiple speakers; give each speaker a cut of their own (a voice-over is a separate source)', { speakers: [...speakers], beats: [from, to] });
        for (const beat of lines) {
          evaluated('dialogue-speaker-visible');
          const speaker = resolveSpeaker(beat.speaker) ?? beat.speaker;
          const visible = (cut.characters ?? []).some((id) => (resolveSpeaker(id) ?? id) === speaker);
          const promptVo = auditDialogueVo(slices[cutIndex] ?? '', beat.text);
          if (!visible && !auditVo(beat) && !promptVo) issue('dialogue-speaker-visible', cid, 'The dialogue speaker is absent from this cut and the line is not marked voice-over', { speaker: beat.speaker, characters: cut.characters ?? [], line: beat.text });
          if (auditVo(beat) && !promptVo) issue('dialogue-speaker-visible', cid, 'The script marks voice-over but the matching video dialogue block has no explicit voice-over marker', { speaker: beat.speaker, line: beat.text });
        }
      }
      previous = segment;
    }
  }
  const nameTerms = [...nameOwners].filter(([term]) => !characterIds.has(term.toUpperCase()) && term.length >= 2).sort((a, b) => b[0].length - a[0].length);
  for (const entry of prose) {
    evaluated('no-internal-labels');
    const labels = auditLabels(entry.text);
    if (labels.length) issue('no-internal-labels', entry.location, 'Internal labels appear in creative prose', { labels: [...new Set(labels)] });
    if (!entry.names || !nameTerms.length) continue;
    let rest = h3Remainder(entry.text).toLowerCase();
    for (const [term, owners] of nameTerms) {
      const re = /^[a-z0-9_-]+$/.test(term) ? new RegExp(`(?<![a-z0-9])${escapeRe(term)}(?![a-z0-9])`, 'g') : new RegExp(escapeRe(term), 'g');
      if (!re.test(rest)) continue;
      if (owners.size === 1) noteName([...owners][0], term, entry.location);
      else skip('consistent-character-naming', entry.location, `Alias ${term} is shared by multiple characters`);
      rest = rest.replace(re, ' ');
    }
  }
  if (!ctx.script) skip('no-internal-labels', 'script', 'Missing script.json; script prose and speakers were not audited');
  if (!(board?.episodes ?? []).some((episode) => (episode.segments ?? []).length)) {
    for (const rule of AUDIT_RULES) skip(rule, 'storyboard', 'No storyboard segments are available to audit');
  }
  if (!nameOwners.size) skip('consistent-character-naming', 'cast/outline', 'No character name/alias mapping is available');
  for (const [id, forms] of usage) {
    evaluated('consistent-character-naming');
    if (forms.size > 1) issue('consistent-character-naming', id, 'A uniquely mapped character has multiple narrative/speaker names', { forms: Object.fromEntries(forms) });
  }
  for (const rule of evaluated_checks) {
    rule.status = rule.issues ? 'fail' : rule.skipped ? 'incomplete' : rule.evaluated ? 'pass' : 'not-applicable';
  }
  return {
    audit_version: 3, scope: 'read-only',
    status: issues.length ? 'fail' : skips.length ? 'incomplete' : 'pass',
    issue_count: issues.length, issues, skips, evaluated_checks,
    limitations: [
      'Medium matching uses explicit positive text markers; it cannot prove rendered visual consistency or judge cinematic quality.',
      'Name consistency uses unique cast/outline mappings in narration and speaker labels; dialogue address forms and unknown identities are not inferred.',
      'Voice-over checks require a marker before the matching dialogue block; unstructured speaker attribution inside prose is not inferred.',
      'Official H3 checks apply to Ref2VA only; legacy keyframe/base prompts are skipped, rather than treated as conforming full-reference prompts.',
      'Style opening and inline Subject checks prove prose/label placement only; appearance, position, action, style suitability and actual speaker semantics require calibrated review.',
      'Vocal-source IDs are checked at explicit dialogue/lyric blocks; recognizable non-verbal human cues (sobs, laughter …) are reported as advisory h3-nonverbal-vocal-cues and never block production; ordinary wind, rain, impacts and other physical sounds are not assigned speaker IDs. Unstructured vocal attribution still requires review. Directly copied soundtrack cues may use Audio alone.',
      'Summary task types and retention markers are checked for format and label/shot existence; their true asset roles and preservation semantics are not inferred.',
      'Sound-placement checks reject explicit shot/time/synchronization anchors, dialogue and recognizable discrete events in soundscape; unrestricted natural-language sound semantics are not proved.',
      'Existing schema has no required picture-design fields; this audit introduces none. Quality and transcription completeness require calibrated review.',
    ],
  };
}

/* ------------------------------------------------------------------ */
/* 镜头配方卡库（可选挂载）                                               */
/* ------------------------------------------------------------------ */
/*
 * shot-recipes 是可选挂载的卡库：给了 --shots <卡片目录> 才有 shot-recipe
 * 这道门。两个 skill 必须各自独立、谁没有谁都能跑，所以这里刻意不
 * import shot-recipes.mjs，自己写一份受限 frontmatter 解析——与
 * expandScript 同一个先例（跨目录 import 会让 skill 拷不走）。
 *
 * 只取门要用的机器字段，正文一概不读；语法受限到只认 `key: 标量` 与
 * `key: [a, b, c]` 行内数组——受限就没有歧义，25 行足够。卡片格式的合法性
 * 由 shot-recipes 自己的 lint 负责，这边只管读得懂的部分。
 */

const RECIPE_FIELDS = new Set(['id', 'name', 'name_en', 'cuts', 'must_phrases', 'sizes', 'cameras']);
const unquote = (s) => String(s).replace(/^['"](.*)['"]$/, '$1').trim();

/** 受限 frontmatter 解析：只回机器字段，没有 id 就当不是卡片。 */
export function parseCardFields(text) {
  const m = /^---\r?\n([\s\S]*?)\r?\n---/.exec(String(text ?? ''));
  if (!m) return null;
  const card = {};
  for (const line of m[1].split(/\r?\n/)) {
    const kv = /^([a-z_]+):\s*(.*)$/.exec(line);
    if (!kv || !RECIPE_FIELDS.has(kv[1])) continue;
    const v = kv[2].trim();
    if (v.startsWith('[')) {
      const inner = v.replace(/^\[/, '').replace(/\]$/, '').trim();
      card[kv[1]] = inner
        ? inner.split(',').map(unquote).filter((x) => x !== '').map((x) => (/^-?\d+$/.test(x) ? Number(x) : x))
        : [];
    } else {
      card[kv[1]] = unquote(v);
    }
  }
  return card.id ? card : null;
}

/** 读卡片目录 → Map<id, 机器字段>。只吃顶层 .md（en/ 是正文翻译，机器字段只有一份）。 */
export function loadRecipes(dir) {
  const root = resolve(dir);
  const cards = new Map();
  if (!existsSync(root)) return cards;
  for (const f of readdirSync(root).filter((x) => x.endsWith('.md')).sort()) {
    const card = parseCardFields(readFileSync(join(root, f), 'utf8'));
    if (card) cards.set(card.id, card);
  }
  return cards;
}

/*
 * 建议景别 / 运镜**刻意不设门**，只在报告里提示偏离，理由三条：
 *   1. 配方是语汇不是法条——同一张卡在竖屏与横屏、两人与三人、有台词
 *      与无台词的情况下，景别会合理偏移（卡库那边把它们存成集合而不是
 *      序列，就是从结构上杜绝升级成硬门）
 *   2. 可选挂载的东西一旦变严就没人挂——挂了反而被拦，下次就不挂了
 *   3. 仓库已有明文判例：误拦的门比没有门更糟，门的信用比数量重要
 */
export function recipeDrift(cut, card) {
  const sizes = Array.isArray(card?.sizes) ? card.sizes : [];
  const cameras = Array.isArray(card?.cameras) ? card.cameras : [];
  return {
    sizes: sizes.length && !sizes.includes(cut?.size) ? sizes : [],
    cameras: cameras.length && !cameras.includes(cut?.camera) ? cameras : [],
  };
}

/* ------------------------------------------------------------------ */
/* 门失败累积                                                           */
/* ------------------------------------------------------------------ */
/*
 * 每次 validate / checkup 的结果本来跑完就没了，于是「模型最常违反哪条规则」
 * 只能靠印象。这里把每次运行与每条失败追加到工作目录的 .gates.jsonl，
 * stats 子命令再读回来，回答三个问题：
 *   哪道门最常响   → 那条规则模型最常无视，措辞该改
 *   哪道门从没响过 → 可能是死门，或者规则已经被模型内化了
 *   失败详情长什么样 → 反复出现却没有门的那类问题，只能靠人看这些自由文本
 *
 * 刻意做成纯函数 + CLI 负责 IO：自测不落盘也能验。
 * 写不进去就静默跳过——日志是附加价值，不能让它挡住主流程。
 */

export const GATE_LOG = '.gates.jsonl';

/** 一次运行产生的日志行（对象数组，CLI 负责序列化落盘）。 */
export function gateLogEntries(gates, { doc = '', at = '' } = {}) {
  const list = Array.isArray(gates) ? gates : [];
  if (!list.length) return [];
  const failed = list.filter((g) => !g.ok);
  const rows = [{ kind: 'run', at, doc, gates: list.length, failed: failed.length }];
  for (const g of failed) {
    rows.push({ kind: 'fail', at, doc, gate: g.id, label: g.label, detail: g.detail ?? '' });
  }
  return rows;
}

/** 汇总日志行。allGates 给全量门 id，用来找出「从没响过」的那些。 */
export function summarizeGateLog(entries, allGates = []) {
  const rows = (Array.isArray(entries) ? entries : []).filter((e) => e && typeof e === 'object');
  const runs = rows.filter((e) => e.kind === 'run');
  const fails = rows.filter((e) => e.kind === 'fail');
  const byGate = new Map();
  for (const f of fails) {
    if (!byGate.has(f.gate)) byGate.set(f.gate, { gate: f.gate, label: f.label ?? f.gate, count: 0, samples: [] });
    const rec = byGate.get(f.gate);
    rec.count += 1;
    if (rec.samples.length < 3 && f.detail) rec.samples.push(f.detail);
  }
  const ranked = [...byGate.values()].sort((a, b) => b.count - a.count || a.gate.localeCompare(b.gate));
  const silent = allGates.filter((id) => !byGate.has(id));
  return {
    runs: runs.length,
    cleanRuns: runs.filter((r) => !r.failed).length,
    fails: fails.length,
    ranked,
    silent,
  };
}

/* ------------------------------------------------------------------ */
/* stats                                                               */
/* ------------------------------------------------------------------ */

/** 报告与质量门共用的确定性统计。script 是硬前提——分镜离开剧本没有意义。 */
export function computeStats(board, script) {
  const params = paramsOf(board);
  const expanded = expandScript(script);
  const episodes = [];
  const batches = new Map(); // sceneId|lighting → 生成批次
  const dialogue = [];       // 配音对齐单：段 × 分镜 × 说话人 × 台词

  for (const ep of board?.episodes ?? []) {
    const sEp = expanded.get(ep.ep);
    let total = 0;
    let cutCount = 0;
    let withLines = 0;
    for (const seg of ep?.segments ?? []) {
      const scene = sEp?.scenes?.[seg.sceneIndex - 1];
      const secs = segSeconds(seg);
      total += secs;
      let segHasLine = false;
      (seg?.cuts ?? []).forEach((cut, ci) => {
        cutCount++;
        if (!scene) return;
        const [from, to] = cut.beats ?? [];
        for (const b of scene.beats.slice((from ?? 1) - 1, to ?? 0)) {
          if (b.kind !== 'line') continue;
          segHasLine = true;
          dialogue.push({ segment: seg.id, cut: ci + 1, ep: ep.ep, speaker: b.speaker, line: b.text, seconds: b.seconds });
        }
      });
      if (segHasLine) withLines++;
      if (scene) {
        const key = `${scene.sceneId}|${scene.lighting}`;
        if (!batches.has(key)) {
          batches.set(key, { sceneId: scene.sceneId, lighting: scene.lighting, segments: [], characters: new Set(), props: new Set() });
        }
        const batch = batches.get(key);
        batch.segments.push(seg.id);
        for (const cut of seg?.cuts ?? []) {
          for (const c of cut.characters ?? []) batch.characters.add(c);
          for (const pr of cut.props ?? []) batch.props.add(pr);
        }
      }
    }
    episodes.push({
      ep: ep.ep,
      target: sEp?.targetSeconds ?? 0,
      segments: (ep?.segments ?? []).length,
      cuts: cutCount,
      totalSeconds: r1(total),
      avgCutSeconds: cutCount ? r1(total / cutCount) : 0,
      withLines,
    });
  }

  const totals = {
    segments: episodes.reduce((n, e) => n + e.segments, 0),
    cuts: episodes.reduce((n, e) => n + e.cuts, 0),
    seconds: r1(episodes.reduce((n, e) => n + e.totalSeconds, 0)),
    targetSeconds: episodes.reduce((n, e) => n + e.target, 0),
    withLines: episodes.reduce((n, e) => n + e.withLines, 0),
    avgCutSeconds: 0,
  };
  totals.avgCutSeconds = totals.cuts ? r1(totals.seconds / totals.cuts) : 0;

  return {
    params,
    episodes,
    totals,
    dialogue,
    batches: [...batches.values()].map((b) => ({
      sceneId: b.sceneId, lighting: b.lighting, segments: b.segments,
      characters: [...b.characters], props: [...b.props],
    })),
  };
}

/* ------------------------------------------------------------------ */
/* 质量门                                                               */
/* ------------------------------------------------------------------ */

const CONTINUITY_CONTRACT_VERSION = 1;

function isRecord(value) {
  return value && typeof value === 'object' && !Array.isArray(value);
}

function reversedMotion(a, b) {
  return (a === 'left_to_right' && b === 'right_to_left')
    || (a === 'right_to_left' && b === 'left_to_right')
    || (a === 'up_to_down' && b === 'down_to_up')
    || (a === 'down_to_up' && b === 'up_to_down');
}

// The ledger is structured, but the frame prompt is still the source of the
// shot's visible action.  A no-op ledger must not be allowed to contradict it.
const FRAME_ACTION_RE = /\b(?:seiz(?:es|ed|ing)|gripp(?:s|ed|ing)|wrench(?:es|ed|ing)|reach(?:es|ed|ing)|clap(?:s|ped|ping)|pat(?:s|ted|ting)|tilt(?:s|ed|ing)|smooth(?:s|ed|ing)|kneel(?:s|ed|ing)|trembl(?:es|ed|ing)|turn(?:s|ed|ing)|nod(?:s|ded|ding)|hurr(?:y|ies|ied|ying)|wrap(?:s|ped|ping)|wipe(?:s|d|ing)|pivot(?:s|ed|ing)|shout(?:s|ed|ing)|chant(?:s|ed|ing)|rais(?:es|ed|ing)|lift(?:s|ed|ing)|walk(?:s|ed|ing)|run(?:s|ran|ning)|step(?:s|ped|ping)|march(?:es|ed|ing)|stare(?:s|d|ing)|look(?:s|ed|ing)|watch(?:es|ed|ing)|gaze(?:s|d|ing)|blink(?:s|ed|ing)|laugh(?:s|ed|ing)|smil(?:es|ed|ing)|speak(?:s|spoke|ing)|call(?:s|ed|ing)|whisper(?:s|ed|ing)|bow(?:s|ed|ing)|clutch(?:es|ed|ing)|count(?:s|ed|ing)|lean(?:s|ed|ing)|enter(?:s|ed|ing)|exit(?:s|ed|ing)|approach(?:es|ed|ing)|freeze(?:s|d|ing)|howl(?:s|ed|ing)|throw(?:s|threw|ing))\b/i;
const FRAME_GAZE_RE = /\b(?:look(?:s|ed|ing)?|watch(?:es|ed|ing)?|stare(?:s|d|ing)?|gaze(?:s|d|ing)?|eyes?\s+(?:fixed|locked|dragging|sliding|slide|turn(?:ed|ing)?|going|drawn))\b/i;
const NOOP_ACTIONS = new Set(['idle']);
// A missing change reason is ledger bookkeeping the video model never reads: report it, don't block.
const MISSING_REASON = /缺 (?:establishing 或 )?\w+Reason$/;

function actionLedgerIsNoop(actionStates, characters) {
  return characters.length > 0 && characters.every((character) => {
    const action = actionStates?.[character];
    return isRecord(action) && NOOP_ACTIONS.has(String(action.in ?? '').trim().toLowerCase())
      && NOOP_ACTIONS.has(String(action.out ?? '').trim().toLowerCase());
  });
}

/** Check adjacent cut state; the ledger is opt-in for legacy packages. */
export function continuityReport(board) {
  if (board?.continuityContractVersion !== CONTINUITY_CONTRACT_VERSION) {
    return {
      enabled: false,
      issues: [],
      detail: `未启用 continuityContractVersion=${CONTINUITY_CONTRACT_VERSION}，连续性门跳过（兼容旧分镜）`,
    };
  }

  const issues = [];
  for (const ep of board?.episodes ?? []) {
    let previous = null;
    for (const seg of ep?.segments ?? []) {
      if (previous && previous.sceneIndex !== seg?.sceneIndex) previous = null;
      for (let index = 0; index < (seg?.cuts ?? []).length; index += 1) {
        const cut = seg.cuts[index];
        const cid = `${seg.id ?? '?'}#${index + 1}`;
        const ledger = cut?.continuity;
        const characters = Array.isArray(cut?.characters) ? cut.characters : [];
        const props = Array.isArray(cut?.props) ? cut.props : [];

        if (!isRecord(ledger)) {
          issues.push(`${cid} 缺 continuity（轴线、画面位置、运动方向和道具状态）`);
          previous = { sceneIndex: seg?.sceneIndex, cut, ledger: null };
          continue;
        }
        if (!String(ledger.axisId ?? '').trim()) issues.push(`${cid} 缺 axisId`);
        if (!isRecord(ledger.screenPositions)) {
          issues.push(`${cid} 缺 screenPositions`);
        } else {
          for (const character of characters) {
            if (!String(ledger.screenPositions[character] ?? '').trim()) issues.push(`${cid} 的 ${character} 缺画面位置`);
          }
        }
        if (!isRecord(ledger.propStates)) {
          issues.push(`${cid} 缺 propStates`);
        } else {
          for (const prop of props) {
            if (!String(ledger.propStates[prop] ?? '').trim()) issues.push(`${cid} 的 ${prop} 缺道具状态`);
          }
        }
        if (!isRecord(ledger.actionStates)) {
          issues.push(`${cid} 缺 actionStates`);
        } else {
          for (const character of characters) {
            const action = ledger.actionStates[character];
            if (!isRecord(action) || !String(action.in ?? '').trim() || !String(action.out ?? '').trim()) {
              issues.push(`${cid} 的 ${character} 缺 actionStates.in/out`);
            }
          }
        }
        if (!isRecord(ledger.emotionStates)) {
          issues.push(`${cid} 缺 emotionStates（可观察的表演状态 in/out）`);
        } else {
          for (const character of characters) {
            const emotion = ledger.emotionStates[character];
            if (!isRecord(emotion) || !String(emotion.in ?? '').trim() || !String(emotion.out ?? '').trim()) {
              issues.push(`${cid} 的 ${character} 缺 emotionStates.in/out`);
            }
          }
        }
        const frame = String(cut?.frame ?? '');
        if (FRAME_ACTION_RE.test(frame) && actionLedgerIsNoop(ledger.actionStates, characters)
          && !String(ledger.actionEvidenceReason ?? '').trim()) {
          issues.push(`${cid} 的 frame 描述了可见动态动作，但 actionStates 全为 idle→idle；需填写真实起止动作或 actionEvidenceReason`);
        }
        if (FRAME_GAZE_RE.test(frame) && !ledger.eyelineTarget
          && !String(ledger.eyelineUnknownReason ?? '').trim()) {
          issues.push(`${cid} 的 frame 描述了明确视线，但 eyelineTarget 为空；需指向人物/道具/camera/offscreen 或说明待确认`);
        }
        if (ledger.eyelineTarget && ledger.eyelineTarget !== 'camera' && ledger.eyelineTarget !== 'offscreen') {
          const visibleSubjects = new Set([...characters, ...props]);
          if (!visibleSubjects.has(ledger.eyelineTarget)) {
            issues.push(`${cid} 的 eyelineTarget「${ledger.eyelineTarget}」不在本镜人物/道具中`);
          }
        }
        if (!previous) {
          if (ledger.establishing !== true && !String(ledger.establishingReason ?? '').trim()) {
            issues.push(`${cid} 是场次首镜，缺 establishing 或 establishingReason`);
          }
        } else if (previous.ledger) {
          const currentSubjects = new Set([...characters, ...props]);
          const previousTarget = previous.ledger.eyelineTarget;
          if (previousTarget
            && previousTarget !== 'camera'
            && previousTarget !== 'offscreen'
            && !currentSubjects.has(previousTarget)
            && !String(ledger.eyelineBreakReason ?? '').trim()) {
            issues.push(`${cid} 未兑现前镜视线目标「${previousTarget}」，缺 eyelineBreakReason`);
          }
          if (previous.ledger.axisId !== ledger.axisId && !String(ledger.axisBreakReason ?? '').trim()) {
            issues.push(`${cid} 与前镜轴线从「${previous.ledger.axisId}」变为「${ledger.axisId}」，缺 axisBreakReason`);
          }
          const oldPositions = previous.ledger.screenPositions ?? {};
          const newPositions = ledger.screenPositions ?? {};
          for (const character of characters) {
            if (previous.cut.characters?.includes(character)
              && oldPositions[character]
              && newPositions[character]
              && oldPositions[character] !== newPositions[character]
              && !String(ledger.positionChangeReason ?? '').trim()) {
              issues.push(`${cid} 的 ${character} 从「${oldPositions[character]}」跳到「${newPositions[character]}」，缺 positionChangeReason`);
            }
          }
          const oldProps = previous.ledger.propStates ?? {};
          const newProps = ledger.propStates ?? {};
          for (const prop of props) {
            if (previous.cut.props?.includes(prop)
              && oldProps[prop]
              && newProps[prop]
              && oldProps[prop] !== newProps[prop]
              && !String(ledger.propChangeReason ?? '').trim()) {
              issues.push(`${cid} 的 ${prop} 从「${oldProps[prop]}」变为「${newProps[prop]}」，缺 propChangeReason`);
            }
          }
          const oldActions = previous.ledger.actionStates ?? {};
          const newActions = ledger.actionStates ?? {};
          for (const character of characters) {
            const oldAction = oldActions[character];
            const newAction = newActions[character];
            if (previous.cut.characters?.includes(character)
              && isRecord(oldAction)
              && isRecord(newAction)
              && oldAction.out
              && newAction.in
              && oldAction.out !== newAction.in
              && !String(ledger.actionCarryReason ?? '').trim()) {
              issues.push(`${cid} 的 ${character} 动作从「${oldAction.out}」跳到「${newAction.in}」，缺 actionCarryReason`);
            }
          }
          if (reversedMotion(previous.ledger.motionVector, ledger.motionVector)
            && !String(ledger.directionChangeReason ?? '').trim()) {
            issues.push(`${cid} 与前镜运动方向相反，缺 directionChangeReason`);
          }
          const oldEmotions = previous.ledger.emotionStates ?? {};
          const newEmotions = ledger.emotionStates ?? {};
          for (const character of characters) {
            const oldEmotion = oldEmotions[character];
            const newEmotion = newEmotions[character];
            if (previous.cut.characters?.includes(character)
              && isRecord(oldEmotion)
              && isRecord(newEmotion)
              && oldEmotion.out
              && newEmotion.in
              && oldEmotion.out !== newEmotion.in
              && !String(ledger.emotionCarryReason ?? '').trim()) {
              issues.push(`${cid} 的 ${character} 表演状态从「${oldEmotion.out}」跳到「${newEmotion.in}」，缺 emotionCarryReason`);
            }
          }
        }
        previous = { sceneIndex: seg?.sceneIndex, cut, ledger };
      }
    }
  }
  const blocking = issues.filter((issue) => !MISSING_REASON.test(issue));
  const advisories = issues.filter((issue) => MISSING_REASON.test(issue));
  return { enabled: true, issues: blocking, advisories, detail: blocking.join('；') };
}

/** Short pointer to the offending text of an audit issue, for gate error messages. */
function auditHint(evidence = {}) {
  const parts = [];
  const clause = evidence.source_clause ?? evidence.first_clause ?? evidence.soundscape ?? evidence.opening;
  if (typeof clause === 'string' && clause.trim()) parts.push(`原文「…${clause.replace(/\s+/g, ' ').trim().slice(-100)}」`);
  if (Array.isArray(evidence.video) && Array.isArray(evidence.reference)) parts.push(`视频媒介 ${evidence.video.join('/')}，参考媒介 ${evidence.reference.join('/')}`);
  if (typeof evidence.expected === 'string') parts.push(`应为「${evidence.expected.slice(0, 200)}」`);
  if (Array.isArray(evidence.speakers)) parts.push(`说话人 ${evidence.speakers.join('、')}`);
  if (typeof evidence.previous_last === 'string') parts.push(`上一段末镜 ${evidence.previous_last}，本段首镜 ${evidence.first}`);
  if (Array.isArray(evidence.beats)) parts.push(`节拍 ${evidence.beats.join('–')}`);
  return parts.length ? `（${parts.join('；')}）` : '';
}

export function gateReport(board, ctx = {}) {
  const gates = [];
  const add = (id, label, ok, detail = '') => gates.push({ id, label, ok, detail });
  const params = paramsOf(board);
  const script = ctx.script ?? null;
  const expanded = script ? expandScript(script) : null;
  const eps = Array.isArray(board?.episodes) ? board.episodes : [];
  const bad = {
    coverage: [], segCap: [], cutLen: [], fit: [], duration: [], crowd: [],
    id: [], size: [], camera: [], english: [], names: [], refs: [],
    h3s: [], h3d: [], h3e: [], h3r: [], style: [], recipe: [], states: [], continuity: [],
  };
  // Reported without blocking: on the canvas neither reaches the video model.
  const advisory = { continuity: [], size: [] };
  // 画布模式（--ref2va）：视频卡只连角色图和场景图，提示词要把它们都定义成 Subject 并用上
  const refTerms = ctx.ref2va ? entityTerms(castView(ctx.cast, ctx.outline), ctx.art) : null;
  const continuity = continuityReport(board);
  bad.continuity.push(...continuity.issues);
  advisory.continuity.push(...(continuity.advisories ?? []));
  // 配方卡库是可选挂载：ctx.recipes 为空就整门跳过（不是「没有 cut 带 recipe」就跳过）
  const recipes = ctx.recipes ?? null;
  let recipeRefs = 0;
  const styleId = board?.style ?? DEFAULT_STYLE;
  const style = STYLE_PRESETS[styleId];
  if (!style) bad.style.push(`style「${styleId}」不在预设里（${Object.keys(STYLE_PRESETS).join(' / ')}）`);
  // 提示词语言：默认英文——官方规范的口径（台词仍在 <d> 里保留原文）；'zh' 可切整条中文
  const promptLang = board?.promptLang ?? 'en';

  // 提示词禁人名：outline 的名字 + cast 的名字与别名
  const banned = [];
  for (const c of ctx.outline?.characters ?? []) if (c?.name) banned.push(c.name);
  for (const c of ctx.cast?.characters ?? []) {
    if (c?.name) banned.push(c.name);
    for (const a of c?.aliases ?? []) banned.push(a);
  }
  const castStates = new Map((ctx.cast?.characters ?? []).map((c) => [
    c.id,
    new Set(['default', ...(c.states ?? []).map((s) => s?.id).filter(Boolean)]),
  ]));

  for (const ep of eps) {
    const label = `E${String(ep?.ep).padStart(2, '0')}`;
    const sEp = expanded?.get(ep?.ep);
    if (expanded && !sEp) bad.refs.push(`${label} 在剧本里不存在`);

    // 段号纪律：格式、集号一致、连号
    (ep?.segments ?? []).forEach((seg, i) => {
      const want = `${label}-${String(i + 1).padStart(2, '0')}`;
      if (seg?.id !== want) bad.id.push(`第 ${i + 1} 段应为 ${want}，实际「${seg?.id}」`);
    });

    let prevSceneIndex = 0;
    let prevSeg = null;
    // 画布模式下，一段的 ID 也指上一段的尾帧：承接段把它定义成一个 Subject
    const epTerms = refTerms
      ? [...refTerms, ...(ep?.segments ?? []).filter((s) => typeof s?.id === 'string')
        .map((s) => [s.id.toLowerCase(), `frame:${s.id}`])].sort((a, b) => b[0].length - a[0].length)
      : null;
    for (const seg of ep?.segments ?? []) {
      const sid = seg?.id ?? '?';
      const previous = prevSeg;
      prevSeg = seg;
      const continuesFrom = seg?.continuesFrom;
      if (continuesFrom != null) {
        if (!previous || continuesFrom !== previous.id) {
          bad.continuity.push(`${sid} continuesFrom「${continuesFrom}」只能写紧挨着的上一段 ${previous?.id ?? '（本段是本集第一段，不能承接）'}`);
        } else if (seg?.sceneIndex !== previous.sceneIndex) {
          bad.continuity.push(`${sid} 换了场次（${previous.sceneIndex} → ${seg?.sceneIndex}），不能从上一段的尾帧接着拍，去掉 continuesFrom`);
        }
      }
      const cuts = seg?.cuts ?? [];
      const total = segSeconds(seg);

      if (!(total >= params.minSegmentSeconds) || total > params.maxSegmentSeconds) {
        bad.segCap.push(`${sid} 共 ${total} 秒（必须在 ${params.minSegmentSeconds}–${params.maxSegmentSeconds} 秒之间）`);
      }

      const h3 = String(seg?.h3Prompt ?? '');
      const musicFields = [...h3.matchAll(/(?:^|\n)[ \t]*(?:non_diegetic_music:|非叙事配乐：)/g)];
      const musicValue = musicFields.length === 1
        ? h3.slice(musicFields[0].index + musicFields[0][0].length).trim() : null;
      if (musicValue !== 'N/A') {
        bad.h3s.push(`${sid} 背景配乐字段必须独立成行、仅出现一次且值为 N/A；由用户在后期统一配乐，剧内音乐写在对应镜头描述里`);
      }
      // Legacy storyboard packages align generated keyframes to cuts. Ref2VA
      // instead defines connected character/environment references as subjects;
      // those source images are not false first-frame or cut-frame anchors.
      const tk = H3_TOKENS[promptLang] ?? H3_TOKENS.zh;
      const ref2vaFields = ['subject_definitions:', 'summary:', 'retention_analysis:', 'detailed_description:', 'overall_soundscape:', 'non_diegetic_music:'];
      const ref2va = h3.includes('subject_definitions:');
      if (ref2va) {
        const indexes = ref2vaFields.map((field) => h3.indexOf(field));
        if (!h3.trimStart().startsWith(ref2vaFields[0]) || indexes.some((i) => i < 0) || indexes.some((value, i) => i > 0 && value <= indexes[i - 1]) || ref2vaFields.some((field) => h3.split(field).length !== 2)) {
          bad.h3s.push(`${sid} Ref2VA 六个字段必须各出现一次并按规范顺序排列`);
        } else {
          const starts = cutStarts(cuts);
          if (h3.indexOf(tk.shot(1), indexes[3]) < 0) bad.h3s.push(`${sid} detailed_description 缺 ${tk.shot(1)}`);
          for (let k = 2; k <= cuts.length; k++) {
            const mark = tk.cutMark(k, h3CutTime(starts[k - 1]));
            if (h3.indexOf(mark, indexes[3]) < 0) bad.h3s.push(`${sid} 缺「${mark}」——切点时刻必须等于前面分镜秒数的累计`);
          }
        }
      } else {
        const wantLine = h3AlignmentLine(cuts, promptLang);
        if (!h3.trimStart().startsWith(wantLine)) {
          bad.h3s.push(`${sid} 首行对齐指令和分镜结构对不上（promptLang=${promptLang}）`);
        } else {
          const idx = tk.fields.map((f) => h3.indexOf(f));
          if (idx.some((i) => i < 0) || !(idx[0] < idx[1] && idx[1] < idx[2])) {
            bad.h3s.push(`${sid} 三个核心字段缺失或顺序不对`);
          } else {
            const starts = cutStarts(cuts);
            if (h3.indexOf(tk.shot(1), idx[0]) < 0) bad.h3s.push(`${sid} 描述正文缺 ${tk.shot(1)}`);
            for (let k = 2; k <= cuts.length; k++) {
              const mark = tk.cutMark(k, h3CutTime(starts[k - 1]));
              if (h3.indexOf(mark, idx[0]) < 0) bad.h3s.push(`${sid} 缺「${mark}」——切点时刻必须等于前面分镜秒数的累计`);
            }
          }
        }
      }
      const rest = h3Remainder(h3);
      for (const match of h3.matchAll(/<d>([\s\S]*?)<\/d>/g)) {
        if (!/^\[[^\]]+\]\s*\S/.test(match[1].trim())) {
          bad.h3e.push(`${sid} 每个 <d> 块必须包含语言标签和非空台词`);
        }
      }
      for (const match of h3.matchAll(/<d>\[([^\]]+)\]\s*([\s\S]*?)\s*<\/d>/g)) {
        const language = match[1].toLowerCase();
        const line = match[2].trim();
        if ((language === 'english' && CJK.test(line))
          || (language === 'chinese' && !CJK.test(line) && /[a-z]{2}/i.test(line))) {
          bad.h3e.push(`${sid} <d>[${match[1]}] 的语言标签与台词实际语言不符`);
        }
      }
      if (promptLang === 'en') {
        if (CJK.test(rest)) bad.h3e.push(`${sid} 的 h3Prompt 设定英文却在 <d> 台词之外混入了中文`);
        // 英文提示词禁人名（图像/视频模型对英文语境的人名有偏见）；中文提示词人名放行——身份靠分镜图锚定
        for (const name of banned) {
          if (rest.includes(name)) bad.names.push(`${sid} 的 h3Prompt 在台词之外出现角色名「${name}」`);
        }
      } else if (!CJK.test(rest)) {
        bad.h3e.push(`${sid} 设定中文提示词（promptLang=${promptLang}），正文却写成了英文`);
      }

      const slices = h3CutSlices(h3, cuts.length, promptLang);
      const scene = sEp ? sEp.scenes[seg?.sceneIndex - 1] : null;
      if (sEp && !scene) bad.refs.push(`${sid} 的 sceneIndex ${seg?.sceneIndex} 在剧本第 ${ep.ep} 集里不存在`);
      if (scene) {
        if (seg.sceneIndex < prevSceneIndex) bad.coverage.push(`${sid} 场次顺序倒退`);
        prevSceneIndex = Math.max(prevSceneIndex, seg.sceneIndex);
      }
      if (refTerms) {
        // 与导入端 _resolve_scene_id 一致：剧本场次的 sceneId，没有就按场序 S0N
        const sceneId = scene?.sceneId ?? (Number.isInteger(seg?.sceneIndex) ? `S${String(seg.sceneIndex).padStart(2, '0')}` : null);
        const required = [
          ...(sceneId ? [`scene:${sceneId}`] : []),
          ...new Set(cuts.flatMap((c) => c?.characters ?? []).filter(Boolean).map((c) => `char:${c}`)),
          ...(continuesFrom != null && continuesFrom === previous?.id ? [`frame:${continuesFrom}`] : []),
        ];
        bad.h3r.push(...ref2vaReferenceProblems(sid, h3, required, epTerms));
      }

      cuts.forEach((cut, ci) => {
        const cid = `${sid}#${ci + 1}`;
        const shotSlice = slices[ci];

        if (!(cut?.seconds >= params.minCutSeconds) || cut.seconds > params.maxCutSeconds) {
          bad.cutLen.push(`${cid} ${cut?.seconds ?? '?'} 秒`);
        }
        if ((cut?.characters ?? []).length > params.maxOnScreen && !String(cut?.note ?? seg?.note ?? '').trim()) {
          bad.crowd.push(`${cid} 同框 ${cut.characters.length} 人且没有拆解说明`);
        }
        if (!SHOT_SIZES[cut?.size]) {
          bad.size.push(`${cid} 景别「${cut?.size}」不在枚举里`);
        } else if (!String(cut?.frame ?? '').toLowerCase().includes(SHOT_SIZES[cut.size].phrase)) {
          (ctx.ref2va ? advisory.size : bad.size).push(`${cid} 分镜图提示词缺景别短语「${SHOT_SIZES[cut.size].phrase}」`);
        }
        if (!CAMERA_MOVES[cut?.camera]) {
          bad.camera.push(`${cid} 运镜「${cut?.camera}」不在 H3 词表里`);
        } else {
          const slice = slices[ci];
          const term = promptLang === 'en' ? String(cut.camera).toLowerCase() : CAMERA_MOVES[cut.camera];
          if (slice == null) {
            bad.camera.push(`${cid} 在 h3Prompt 里找不到对应的 [Shot ${ci + 1}] 段落`);
          } else if (!(promptLang === 'en' ? slice.toLowerCase() : slice).includes(term)) {
            bad.camera.push(`${cid} 的 [Shot ${ci + 1}] 段落缺运镜词「${term}」`);
          } else if (cut.camera === 'Static Shot') {
            const moving = slice.match(CAMERA_MOVING);
            if (moving) {
              bad.camera.push(`${cid} 运镜是 Static Shot，[Shot ${ci + 1}] 段落却写「${moving[0]}」——固定镜头里镜头不动，改运镜或删掉这句`);
            }
          }
        }
        const frame = String(cut?.frame ?? '');
        if (!frame.trim()) bad.english.push(`${cid} 的分镜图提示词为空`);
        if (CJK.test(frame)) bad.english.push(`${cid} 的分镜图提示词混入了非英文`);
        if (style && !frame.toLowerCase().includes(style.phrase)) {
          bad.style.push(`${cid} 的分镜图提示词缺风格短语「${style.phrase}」`);
        }
        for (const name of banned) {
          if (frame.includes(name)) bad.names.push(`${cid} 的分镜图提示词出现角色名「${name}」`);
        }

        // 镜头配方：id 在卡库里 + 每条必备短语进了本切的 frame
        // 判定与 shot-recipes 的 checkRecipes 完全一致：两边小写化后 includes，逐条全中才算过
        if (recipes && typeof cut?.recipe === 'string' && cut.recipe) {
          recipeRefs += 1;
          const card = recipes.get(cut.recipe);
          if (!card) {
            bad.recipe.push(`${cid} 引用的配方「${cut.recipe}」不在配方库里`);
          } else {
            const lower = frame.toLowerCase();
            for (const ph of card.must_phrases ?? []) {
              if (!lower.includes(String(ph).toLowerCase())) {
                bad.recipe.push(`${cid} 的分镜图提示词缺配方「${card.name}」的必备短语「${ph}」`);
              }
            }
          }
        }

        // 引用对账 + 台词装得下 + 台词逐字进 <d>
        if (scene) {
          const cast = new Set(scene.characters);
          for (const c of cut?.characters ?? []) {
            if (!cast.has(c)) bad.refs.push(`${cid} 的 ${c} 不在剧本该场人物里`);
          }
          if (board?.stateContractVersion >= 1) {
            const states = cut?.characterStates;
            if (!states || typeof states !== 'object' || Array.isArray(states)) {
              bad.states.push(`${cid} 缺 characterStates`);
            } else {
              for (const c of cut?.characters ?? []) {
                const want = scene.characterStates?.[c];
                const got = states[c];
                if (!want) bad.states.push(`${cid} 的 ${c} 在剧本场次缺状态`);
                else if (got !== want) bad.states.push(`${cid} 的 ${c} 状态应为 ${want}，实际 ${got ?? '缺失'}`);
                else if (castStates.has(c) && !castStates.get(c).has(got)) bad.states.push(`${cid} 的 ${c}@${got} 不在 cast.json`);
              }
              for (const c of Object.keys(states)) {
                if (!(cut?.characters ?? []).includes(c)) bad.states.push(`${cid} 状态表多出画外角色 ${c}`);
              }
            }
          }
          const propSet = new Set(scene.props);
          for (const pr of cut?.props ?? []) {
            if (!propSet.has(pr)) bad.refs.push(`${cid} 的 ${pr} 不在剧本该场道具里`);
          }
          const [from, to] = cut?.beats ?? [];
          if (Number.isInteger(from) && Number.isInteger(to) && from >= 1 && to <= scene.beats.length && from <= to) {
            let dlg = 0;
            for (const b of scene.beats.slice(from - 1, to)) {
              if (b.kind !== 'line') continue;
              dlg += b.seconds;
              const re = new RegExp(`<d>\\[[^\\]]+\\]\\s*${b.text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*</d>`);
              if (!re.test(shotSlice ?? '')) bad.h3d.push(`${cid} 的本镜 h3Prompt 缺台词「${b.text.slice(0, 12)}…」的 <d> 块`);
            }
            const claimed = new Set(scene.beats.slice(from - 1, to)
              .filter((b) => b.kind === 'line')
              .map((b) => b.text));
            for (const match of String(shotSlice ?? '').matchAll(/<d>\[[^\]]+\]\s*([\s\S]*?)\s*<\/d>/g)) {
              const text = match[1].trim();
              if (!claimed.has(text)) {
                bad.h3d.push(`${cid} 的本镜 h3Prompt 混入未认领节拍台词「${text.slice(0, 12)}…」`);
              }
            }
            if (dlg > cut.seconds) bad.fit.push(`${cid} 台词 ${r1(dlg)} 秒装不进 ${cut.seconds} 秒`);
          }
        }
      });

      // 多格配方靠「连续同 id 的 run」表达（不引入新结构）：卡片 cuts 下限 ≥ 2 时，
      // 连续段的长度不得小于该下限——单独挂一格的两格配方是没兑现的配方
      if (recipes) {
        for (let i = 0; i < cuts.length; ) {
          const rid = cuts[i]?.recipe;
          if (typeof rid !== 'string' || !rid) {
            i += 1;
            continue;
          }
          let j = i;
          while (j + 1 < cuts.length && cuts[j + 1]?.recipe === rid) j += 1;
          const card = recipes.get(rid);
          const min = Array.isArray(card?.cuts) ? card.cuts[0] : 0;
          const run = j - i + 1;
          if (min >= 2 && run < min) {
            bad.recipe.push(`${sid}#${i + 1} 的配方「${card.name}」要 ${min} 格连排，这里只有 ${run} 格——多格配方靠连续同 recipe 的分镜表达`);
          }
          i = j + 1;
        }
      }
    }

    // 节拍全覆盖：每场的节拍被恰好一次、按顺序、连续认领（分镜级）
    if (sEp) {
      for (const scene of sEp.scenes) {
        const claims = [];
        for (const seg of ep?.segments ?? []) {
          if (seg?.sceneIndex !== scene.sceneIndex) continue;
          (seg?.cuts ?? []).forEach((cut, ci) => {
            const [from, to] = cut?.beats ?? [];
            if (!Number.isInteger(from) || !Number.isInteger(to) || from < 1 || to > scene.beats.length || from > to) {
              bad.coverage.push(`${seg.id}#${ci + 1} 的节拍区间 [${from}, ${to}] 不合法（该场共 ${scene.beats.length} 拍）`);
              return;
            }
            claims.push([from, to, `${seg.id}#${ci + 1}`]);
          });
        }
        let cursor = 1;
        for (const [from, to, id] of claims) {
          if (from !== cursor) {
            bad.coverage.push(`${label} 第 ${scene.sceneIndex} 场第 ${cursor} 拍${from > cursor ? '没人认领' : `被 ${id} 重复认领`}`);
          }
          cursor = Math.max(cursor, to + 1);
        }
        if (claims.length && cursor <= scene.beats.length) {
          bad.coverage.push(`${label} 第 ${scene.sceneIndex} 场第 ${cursor}–${scene.beats.length} 拍没人认领`);
        }
        if (!claims.length && scene.beats.length) {
          bad.coverage.push(`${label} 第 ${scene.sceneIndex} 场整场没有分镜`);
        }
      }

      // 每集总时长对齐剧本目标
      if (sEp.targetSeconds > 0) {
        const total = (ep?.segments ?? []).reduce((n, s) => n + segSeconds(s), 0);
        const lo = sEp.targetSeconds * (1 - params.tolerance);
        const hi = sEp.targetSeconds * (1 + params.tolerance);
        if (total < lo) bad.duration.push(`${label} 欠 ${r1(lo - total)} 秒（${r1(total)}s / 目标 ${sEp.targetSeconds}s）`);
        if (total > hi) bad.duration.push(`${label} 超 ${r1(total - hi)} 秒（${r1(total)}s / 目标 ${sEp.targetSeconds}s）`);
      }
    }
  }

  const SKIP_SCRIPT = '未提供 script.json，本门跳过（视为通过）';
  const SKIP_NAMES = '未提供 outline/cast，本门跳过（视为通过）';
  const SKIP_SHOTS = '未挂载配方卡库（--shots <卡片目录>），本门跳过（视为通过）';
  const NO_RECIPE = '本批分镜没有引用配方';
  const SKIP_REF2VA = '未指定 --ref2va（关键帧导出模式），本门跳过（视为通过）';

  add('coverage', '剧本节拍被恰好一次、按顺序、连续认领（分镜级）', bad.coverage.length === 0, script ? bad.coverage.join('；') : SKIP_SCRIPT);
  add('segment-cap', `每段 ${params.minSegmentSeconds} ≤ 总秒数 ≤ ${params.maxSegmentSeconds}（视频大模型生成的单段时长上下限）`, eps.length > 0 && bad.segCap.length === 0, bad.segCap.join('；'));
  add('cut-length', `每个分镜 ${params.minCutSeconds}–${params.maxCutSeconds} 秒——短剧的注意力节奏`, eps.length > 0 && bad.cutLen.length === 0, bad.cutLen.join('；'));
  add('dialogue-fit', '认领节拍的台词装得进分镜秒数', bad.fit.length === 0, script ? bad.fit.join('；') : SKIP_SCRIPT);
  add('ep-duration', `每集总时长在剧本目标 ±${Math.round(params.tolerance * 100)}% 内`, bad.duration.length === 0, script ? bad.duration.join('；') : SKIP_SCRIPT);
  add('crowd', `单个分镜同框 ≤ ${params.maxOnScreen} 人，超了必须带拆解说明`, bad.crowd.length === 0, bad.crowd.join('；'));
  add('segment-id', '段号 E01-01 格式、按顺序连号', bad.id.length === 0, bad.id.join('；'));
  add('size-phrase', '景别短语写进分镜图提示词', bad.size.length === 0, bad.size.join('；'));
  add('camera-phrase', '运镜用 H3 官方词表，出现在自己的 [Shot k] 段落里；固定镜头的段落不写镜头在动', bad.camera.length === 0, bad.camera.join('；'));
  add('h3-structure', 'H3 首行对齐指令和切点对账，片段背景配乐固定 N/A', eps.length > 0 && bad.h3s.length === 0, bad.h3s.join('；'));
  add('h3-references', 'Ref2VA：本段画布连的场景图和角色图都定义成 <Subject N>（写明 ID、引用 <Picture N>）并在正文里用上', bad.h3r.length === 0, ctx.ref2va ? bad.h3r.join('；') : SKIP_REF2VA);
  add('h3-dialogue', '认领节拍的台词逐字进 H3 提示词的 <d> 块', bad.h3d.length === 0, script ? bad.h3d.join('；') : SKIP_SCRIPT);
  add('h3-lang', `H3 提示词语言与设定一致（promptLang=${promptLang}，正文${promptLang === 'en' ? '全英文' : '中文'}、骨架 token 官方英文格式）`, bad.h3e.length === 0, bad.h3e.join('；'));
  add('style-phrase', `分镜图风格短语统一（${style ? `${styleId}：${style.phrase}` : '预设无效'}）——同剧不许画风漂`, bad.style.length === 0, bad.style.join('；'));
  add('prompt-english', '分镜图提示词全英文且非空', bad.english.length === 0, bad.english.join('；'));
  add('prompt-no-names', '英文提示词不含角色名（分镜图提示词恒查；中文 H3 提示词放行）', bad.names.length === 0, banned.length ? bad.names.join('；') : SKIP_NAMES);
  add('refs', '场次／人物／道具对账剧本', bad.refs.length === 0, script ? bad.refs.join('；') : SKIP_SCRIPT);
  add('character-states', '逐镜角色状态继承剧本场次，非默认状态在 cast.json 有独立资产', bad.states.length === 0, bad.states.join('；'));
  add('continuity', '相邻镜头轴线、位置、视线、运动方向和道具状态连续', bad.continuity.length === 0, continuity.enabled || bad.continuity.length ? bad.continuity.join('；') : continuity.detail);
  if (advisory.continuity.length) {
    gates.push({ id: 'continuity-reasons', label: '相邻镜头状态变化写明原因（只提示）', ok: true, advisory: true, detail: advisory.continuity.join('；') });
  }
  if (advisory.size.length) {
    gates.push({ id: 'frame-size-phrase', label: '景别短语写进分镜图提示词（画布模式只提示）', ok: true, advisory: true, detail: advisory.size.join('；') });
  }
  // 可选挂载的门放最后：没给 --shots 就跳过；给了但全篇没引用配方也算通过，但要明说，不静默
  add(
    'shot-recipe',
    '引用的配方存在、必备短语进了分镜图提示词、多格配方连排够格数',
    bad.recipe.length === 0,
    recipes ? (bad.recipe.length ? bad.recipe.join('；') : recipeRefs ? '' : NO_RECIPE) : SKIP_SHOTS,
  );

  // Ref2VA 投产复用只读 audit；缺证据的跳过项也必须阻止投产。错误附原文，模型才找得到出错的位置。
  const referenceAudit = ctx.ref2va ? auditStoryboard(board, ctx) : null;
  for (const [id, label] of [
    ['h3-style-before-shot1', 'Ref2VA：统一画风句写在 [Shot 1] 之前'],
    ['h3-subject-inline-binding', 'Ref2VA：参考对象首次出现就绑定在描述正文里'],
    ['h3-referenced-speaker-binding', 'Ref2VA：参考对象的发声用 <Subject N> (Sx) 绑定'],
    ['h3-vocal-source-ids', 'Ref2VA：独立人声按首次发声顺序编号并保持来源一致'],
    ['h3-summary-task-prefix', 'Ref2VA：summary 使用任务前缀，尾帧承接包含 keyframe completion'],
    ['h3-retention-per-line', 'Ref2VA：每个标签独立一行说明镜头、保留关系和内容'],
    ['h3-synchronized-sound-placement', 'Ref2VA：同步声音写入对应镜头，整体声景只写持续环境声'],
    ['h3-voiceover-exact-format', 'Ref2VA：画外音固定句式与紧随的闭唇说明完整'],
    ['style-consistency', 'Ref2VA：视频、分镜图与使用的角色和场景参考画风一致'],
    ['same-scene-continues-from', 'Ref2VA：同场相邻段从上一段尾帧接着拍（continuesFrom），首镜起幅接得上上一段末镜，换场不接'],
    ['one-speaker-per-cut', '一个镜头只收一个人的台词，画外音单算一个声源'],
  ]) {
    if (id === 'one-speaker-per-cut' && referenceAudit && !script) {
      add(id, label, true, SKIP_SCRIPT);
      continue;
    }
    const failures = referenceAudit ? [
      ...referenceAudit.issues.filter((item) => item.rule === id).map((item) => `${item.location}: ${item.detail}${auditHint(item.evidence)}`),
      ...referenceAudit.skips.filter((item) => item.rule === id).map((item) => `${item.location}: ${item.reason}`),
    ] : [];
    add(id, label, failures.length === 0,
      referenceAudit ? (failures.length ? `[${id}] ${failures.join('；')}` : '') : SKIP_REF2VA);
  }

  return gates;
}

/* ------------------------------------------------------------------ */
/* validate                                                            */
/* ------------------------------------------------------------------ */

export function validateStoryboard(board, ctx = {}) {
  const problems = [];
  const p = (msg) => problems.push(msg);
  if (!board || typeof board !== 'object') return ['storyboard.json 不是对象'];

  if (!String(board.source ?? '').trim()) p('缺少 source（剧名）');
  const eps = board.episodes;
  if (!Array.isArray(eps) || eps.length === 0) {
    p('episodes 为空');
    return problems;
  }
  const seen = new Set();
  for (const ep of eps) {
    const label = `第 ${ep?.ep ?? '?'} 集`;
    if (!Number.isInteger(ep?.ep) || ep.ep < 1) p(`${label}的 ep 必须是正整数`);
    if (seen.has(ep?.ep)) p(`集号 ${ep.ep} 重复`);
    seen.add(ep?.ep);
    if (!Array.isArray(ep?.segments) || ep.segments.length === 0) {
      p(`${label}没有段`);
      continue;
    }
    for (const seg of ep.segments) {
      const sid = seg?.id ?? '?';
      if (typeof seg?.id !== 'string') p(`${label}有段缺 id`);
      if (!Number.isInteger(seg?.sceneIndex) || seg.sceneIndex < 1) p(`${sid} 缺 sceneIndex（剧本里第几场）`);
      if (typeof seg?.h3Prompt !== 'string') p(`${sid} 缺 h3Prompt（H3 视频提示词，写法见 references/h3-prompt.md）`);
      if (!Array.isArray(seg?.cuts) || seg.cuts.length === 0) {
        p(`${sid} 没有分镜`);
        continue;
      }
      seg.cuts.forEach((cut, ci) => {
        const cid = `${sid}#${ci + 1}`;
        if (!Array.isArray(cut?.beats) || cut.beats.length !== 2) p(`${cid} 的 beats 必须是 [起, 止] 两个数`);
        if (typeof cut?.seconds !== 'number') p(`${cid} 缺 seconds`);
        if (!Array.isArray(cut?.characters)) p(`${cid} 缺 characters（空镜给空数组）`);
        if (typeof cut?.frame !== 'string') p(`${cid} 缺 frame（分镜图英文提示词）`);
      });
    }
  }

  for (const g of gateReport(board, ctx)) {
    if (!g.ok) p(`质量门未过：${g.label}${g.detail ? `（${g.detail}）` : ''}`);
  }
  return problems;
}

/* ------------------------------------------------------------------ */
/* seed — 从 script.json 确定性预填                                      */
/* ------------------------------------------------------------------ */

export function seedFromScript(script, epRange = null, ctx = {}) {
  const expanded = expandScript(script);
  const inRange = (n) => !epRange || (n >= epRange[0] && n <= epRange[1]);
  const episodes = [];
  for (const [epNo, sEp] of expanded) {
    if (!inRange(epNo)) continue;
    episodes.push({
      ep: epNo,
      segments: [],
      seedScenes: sEp.scenes.map((sc) => ({
        sceneIndex: sc.sceneIndex,
        sceneId: sc.sceneId,
        lighting: sc.lighting,
        characters: sc.characters,
        characterStates: sc.characterStates,
        props: sc.props,
        beats: sc.beats.map((b) => ({
          n: b.n,
          kind: b.kind,
          seconds: b.seconds,
          ...(b.speaker ? { speaker: b.speaker } : {}),
          text: b.text,
        })),
      })),
    });
  }
  return {
    source: script?.source ?? '',
    style: ctx.cast?.style ?? ctx.art?.style ?? DEFAULT_STYLE,
    styleSentence: STYLE_PRESETS[ctx.cast?.style ?? ctx.art?.style ?? DEFAULT_STYLE]?.sentence ?? '',
    stateContractVersion: script?.stateContractVersion ?? 1,
    continuityContractVersion: CONTINUITY_CONTRACT_VERSION,
    episodes,
  };
}

/* ------------------------------------------------------------------ */
/* export — H3 投产包                                                   */
/* ------------------------------------------------------------------ */
/*
 * 固定投产结构：每段一个文件夹——E01-01/f1.png … fN.png + prompt.md
 * （h3Prompt 原样），根部一份 manifest：按 Picture 序列出该段要挂的
 * 分镜图路径、秒数、缺图标注。提示词就躺在图旁边，整个文件夹拖给
 * H3 就是一次生成。纯函数返回文件清单，落盘在 CLI 层——可测性。
 */
export function exportPack(board, script, { imageExists = () => false, dir = '.' } = {}) {
  const prefix = dir === '.' ? '' : `${dir}/`;
  const files = [];
  const manifest = [];
  let missingTotal = 0;
  for (const ep of board?.episodes ?? []) {
    for (const seg of ep?.segments ?? []) {
      // prompt.md 头部先说清哪个文件是首帧、每张图钉在第几秒——
      // 分隔线以下是 h3Prompt 原样，整段复制就能用
      const starts = cutStarts(seg.cuts);
      const mapping = (seg.cuts ?? [])
        .map((_, i) => `- Picture ${i + 1} = f${i + 1}.png${i === 0 ? '（**首帧**，钉 0.00 秒）' : `（钉 ${starts[i].toFixed(2)} 秒）`}`)
        .join('\n');
      const promptMd = `# ${seg.id} · H3 提示词\n\n首帧 = **f1.png**。图片按 Picture 序号挂载：\n\n${mapping}\n\n---\n\n${seg.h3Prompt ?? ''}\n`;
      files.push({ path: `${prefix}${seg.id}/prompt.md`, content: promptMd });
      const pictures = (seg.cuts ?? []).map((_, i) => `${prefix}${seg.id}/f${i + 1}.png`);
      const missing = pictures.filter((rel) => !imageExists(rel));
      missingTotal += missing.length;
      manifest.push({
        segment: seg.id,
        seconds: segSeconds(seg),
        cuts: (seg.cuts ?? []).length,
        cutStarts: cutStarts(seg.cuts),
        prompt: `${prefix}${seg.id}/prompt.md`,
        pictures,
        missing,
      });
    }
  }
  files.push({ path: `${prefix}manifest.json`, content: JSON.stringify(manifest, null, 2) + '\n' });
  return { files, manifest, missingTotal };
}

/* ------------------------------------------------------------------ */
/* slug                                                                */
/* ------------------------------------------------------------------ */

export function slug(name) {
  return safeStem(name, 'storyboard', { dot: true });
}

/* ------------------------------------------------------------------ */
/* render — 界面文案                                                    */
/* ------------------------------------------------------------------ */

/*
 * 界面文案表：内置 zh / en 两套。语言优先级 --lang > JSON 顶层 lang 字段 > 'zh'，
 * 经 ctx.lang 传给渲染器。只管报告界面标签——与 promptLang（H3 提示词语言）
 * 互相独立：界面切英文不改提示词，提示词切中文不改界面。
 * 数据（H3 提示词、画面摘要、台词、质量门 detail）不在此表，原样透传。
 */
/* 门标签与「跳过」提示的英文映射：质量门面板是报告的一部分，出英文报告时
 * 这里做展示层翻译——gateReport 的逻辑与中文诊断文案一行不动（CLI 仍是中文）。
 * 动态阈值由门自己算，映射里只写固定语义；未命中的 id 回落到原标签。 */
const GATE_LABELS_EN = {
  'coverage': 'Every script beat claimed exactly once, in order, contiguous (cut level)',
  'segment-cap': 'Each segment 0 < total ≤ {1}s (the single-generation cap)',
  'cut-length': 'Every cut {0}–{1}s — the short-drama attention rhythm',
  'dialogue-fit': 'Dialogue of the claimed beats fits within the cut duration',
  'ep-duration': 'Episode total within ±{0}% of the script\'s target',
  'crowd': 'At most {0} characters on screen per cut; more requires a breakdown note',
  'segment-id': 'Segment IDs in E01-01 format, sequential',
  'size-phrase': 'Shot-size phrase present in the frame prompt',
  'continuity-reasons': 'Adjacent-cut state changes give a reason (advisory)',
  'frame-size-phrase': 'Shot-size phrase present in the frame prompt (advisory on the canvas)',
  'camera-phrase': 'Camera move from the official H3 vocabulary, inside its own [Shot k] passage; a static shot never says the camera moves',
  'h3-structure': 'H3 alignment and cut times match; segment background music is N/A',
  'h3-references': 'Ref2VA: the scene and character images connected on the canvas are each a <Subject N> (with its ID, citing its <Picture N>) and are used in the description',
  'h3-dialogue': 'Claimed dialogue appears verbatim inside the H3 <d> blocks',
  'h3-lang': 'Prompt language matches the promptLang setting',
  'style-phrase': 'Frame-prompt style phrase consistent — one drama, one look',
  'prompt-english': 'Frame prompts are English and non-empty',
  'prompt-no-names': 'English prompts carry no character names',
  'refs': 'Scenes / characters / props audited against the script',
  'shot-recipe': 'Referenced recipes exist, their must-phrases are in the frame prompt, multi-cut recipes run long enough',
  'h3-style-before-shot1': 'Ref2VA: one shared style sentence before [Shot 1]',
  'h3-subject-inline-binding': 'Ref2VA: first reference use bound inside descriptive prose',
  'h3-referenced-speaker-binding': 'Ref2VA: referenced vocal sources bound as <Subject N> (Sx)',
  'h3-vocal-source-ids': 'Ref2VA: stable vocal-source IDs in first-event order',
  'h3-summary-task-prefix': 'Ref2VA: task-prefixed summary, including keyframe completion for a preceding tail frame',
  'h3-retention-per-line': 'Ref2VA: one retention line per label with shots, relationship and explanation',
  'h3-synchronized-sound-placement': 'Ref2VA: synchronized sounds in their shots; only continuous ambience overall',
  'h3-voiceover-exact-format': 'Ref2VA: exact voiceover phrase and immediate closed-lips clause',
  'style-consistency': 'Ref2VA: video, frames and used cast/art references share the same artwork medium',
};
const GATE_SKIPS_EN = {
    '未提供 outline.json，本门跳过（视为通过）': 'outline.json not provided — gate skipped (treated as passing)',
    '未提供 art.json，本门跳过（视为通过）': 'art.json not provided — gate skipped (treated as passing)',
    '未提供 script.json，本门跳过（视为通过）': 'script.json not provided — gate skipped (treated as passing)',
    '未提供 outline/cast，本门跳过（视为通过）': 'outline/cast not provided — gate skipped (treated as passing)',
    '未提供 cast.json，本门跳过（视为通过）': 'cast.json not provided — gate skipped (treated as passing)',
    '未挂载配方卡库（--shots <卡片目录>），本门跳过（视为通过）': 'no recipe card library mounted (--shots <cards dir>) — gate skipped (treated as passing)',
    '本批分镜没有引用配方': 'no cut in this batch references a recipe',
    '未指定 --ref2va（关键帧导出模式），本门跳过（视为通过）': '--ref2va not given (keyframe export mode) — gate skipped (treated as passing)',
};
/** 报告里的门文案：英文界面取映射，未命中或中文界面回落原文。 */
const gateText = gateTextFor(GATE_LABELS_EN, GATE_SKIPS_EN);

const I18N = {
  zh: {
    langCode: 'zh',
    kicker: '分镜',
    docTitle: (s, a, b) => `${s} · 分镜${a === b ? `（第 ${a} 集）` : `（第 ${a}–${b} 集）`}`,
    epRange: (a, b) => (a === b ? `第 ${a} 集` : `第 ${a}–${b} 集`),
    exportJson: '导出 JSON',
    gatesPass: '全部通过',
    gatesFail: (n) => `${n} 项未过`,
    gatePill: (okN, total) => `质量门 ${okN} / ${total}`,
    kpi: {
      segments: '生成段', segmentsSub: (cap) => `一段一次调用，上限 ${cap} 秒`,
      cuts: '分镜', cutsSub: (avg) => `平均 ${avg} 秒一切`,
      time: '预估总时长', timeSub: (t) => `目标 ${t}`,
      batches: '生成批次', batchesSub: '同场景同光照共用环境参考图',
      lines: '台词段', linesSub: '其余是纯画面段',
    },
    secRhythm: '分镜节奏带',
    secSegments: '分集分镜表',
    secBatches: '生成批次单',
    secDialogue: '配音对齐单',
    secGates: '质量门',
    rhythmNote: '粗分隔 = 生成段边界 · 段宽 = 分镜时长占比 · 颜色越深景别越近',
    segmentsNote: '一段 = 一次生成：主分镜图钉 0.00 秒，子分镜图钉各自切点',
    batchesNote: '自动汇总 · 同批段共用同一张环境参考图',
    dialogueNote: '自动汇总 · TTS 音频对到哪一段的第几切',
    epHead: (nSeg, nCut, total, target) => `${nSeg} 段 ${nCut} 切 · 共 ${total} 秒 / 目标 ${target} 秒`,
    segHead: (total, n) => `${total} 秒 · ${n} 个分镜`,
    secBadge: (secs, n) => `${secs}s · ${n} 切`,
    rhythmVal: (nSeg, nCut, secs) => `${nSeg} 段 ${nCut} 切 · ${secs}s`,
    beatsLabel: (s, from, to) => `第 ${s} 场 ${from === to ? `第 ${from} 拍` : `第 ${from}–${to} 拍`}`,
    masterLabel: '主分镜图',
    subLabel: (i) => `子分镜 ${i}`,
    frameMissing: (i) => `#${i} 未生成`,
    framePrompt: '分镜图提示词',
    h3Prompt: 'H3 提示词',
    h3Section: 'H3 视频提示词',
    showSegs: '▾ 展开全部段',
    hideSegs: '▴ 收起',
    copy: '复制', copied: '已复制', copyFailed: '复制失败',
    dialogueCols: ['段 · 切', '说话人', '台词', '台词秒数'],
    cutCols: ['切', '起点', '秒', '景别', '运镜', '配方', '画面', '人物'],
    batchCols: ['场景', '光照', '段', '需要的角色', '道具'],
    atSec: (t) => `${t.toFixed(2)}s 起`,
    batchLabel: (num) => `批次 ${num}`,
    batchNeed: (chars, props) => `需要：${chars.length ? chars.join('、') + ' 的角色设定图' : '无角色（空镜）'}${props.length ? ' · ' + props.join('、') : ''}`,
    voiceOver: '画外音',
    listSep: '、',
    sizeName: (size) => SHOT_SIZES[size]?.zh ?? size,
    cameraLabel: (camera) => `${camera}（${CAMERA_MOVES[camera] ?? '?'}）`,
    recipeNone: '—',
    recipeName: (card, id) => card?.name ?? id,
    recipeDrift: (sizes, cameras) =>
      `配方建议${[sizes.length ? `景别 ${sizes.join(' / ')}` : '', cameras.length ? `运镜 ${cameras.join(' / ')}` : ''].filter(Boolean).join(' · ')}——只提示不设门`,
    recipeHint: (n) => `ℹ️ ${n} 处分镜的景别／运镜偏离了配方建议——配方是语汇不是法条，只提示不设门（报告的「配方」列有 ≠ 标记）`,
    speakerLine: (name, text) => `${name}：「${text}」`,
    withLighting: (name, lighting) => (lighting ? `${name}（${lighting}）` : name),
    fmtMin: (sec) => `${Math.floor(sec / 60)} 分 ${Math.round(sec % 60)} 秒`,
    unitSeg: '段',
    unitCut: '切',
    colophon: '分镜由模型依据剧本切分：段 = 一次生成（≤15 秒），分镜 = 段内 2–5 秒的剪切，每个分镜一张关键帧图。对齐指令、切点时刻、台词、提示词纪律全部由脚本确定性对账。分镜图出图走 codex，环境与角色设定图当参考图。',
  },
  en: {
    langCode: 'en',
    kicker: 'Storyboard',
    docTitle: (s, a, b) => `${s} · Storyboard (${a === b ? `Episode ${a}` : `Episodes ${a}–${b}`})`,
    epRange: (a, b) => (a === b ? `Episode ${a}` : `Episodes ${a}–${b}`),
    exportJson: 'Export JSON',
    gatesPass: 'All passed',
    gatesFail: (n) => `${n} failed`,
    gatePill: (okN, total) => `Quality gates ${okN} / ${total}`,
    kpi: {
      segments: 'Segments', segmentsSub: (cap) => `one generation call each, capped at ${cap}s`,
      cuts: 'Cuts', cutsSub: (avg) => `${avg}s per cut on average`,
      time: 'Estimated total', timeSub: (t) => `target ${t}`,
      batches: 'Generation batches', batchesSub: 'same scene + lighting share one environment reference',
      lines: 'Dialogue segments', linesSub: 'the rest are picture-only',
    },
    secRhythm: 'Cut rhythm strip',
    secSegments: 'Segment cards',
    secBatches: 'Generation batches',
    secDialogue: 'Audio alignment',
    secGates: 'Quality gates',
    rhythmNote: 'thick separators = segment boundaries · slice width = cut duration share · darker = closer shot size',
    segmentsNote: 'one segment = one generation: the master frame pins 0.00s, sub-frames pin their own cut marks',
    batchesNote: 'auto-computed · segments in a batch share one environment reference image',
    dialogueNote: 'auto-computed · which segment and cut each TTS clip lands on',
    epHead: (nSeg, nCut, total, target) => `${nSeg} segments ${nCut} cuts · ${total}s total / ${target}s target`,
    segHead: (total, n) => `${total}s · ${n} cuts`,
    secBadge: (secs, n) => `${secs}s · ${n} cuts`,
    rhythmVal: (nSeg, nCut, secs) => `${nSeg} seg ${nCut} cuts · ${secs}s`,
    beatsLabel: (s, from, to) => `Scene ${s} · ${from === to ? `beat ${from}` : `beats ${from}–${to}`}`,
    masterLabel: 'master frame',
    subLabel: (i) => `sub-frame ${i}`,
    frameMissing: (i) => `#${i} not generated`,
    framePrompt: 'Frame prompt',
    h3Prompt: 'H3 prompt',
    h3Section: 'H3 video prompt',
    showSegs: '▾ Show all segments',
    hideSegs: '▴ Collapse',
    copy: 'Copy', copied: 'Copied', copyFailed: 'Copy failed',
    dialogueCols: ['Segment · cut', 'Speaker', 'Line', 'Seconds'],
    cutCols: ['Cut', 'Start', 'Sec', 'Size', 'Camera', 'Recipe', 'Picture', 'Characters'],
    batchCols: ['Scene', 'Lighting', 'Segments', 'Characters needed', 'Props'],
    atSec: (t) => `from ${t.toFixed(2)}s`,
    batchLabel: (num) => `Batch ${num}`,
    batchNeed: (chars, props) => `Needs: ${chars.length ? `character sheets for ${chars.join(', ')}` : 'no characters (empty shot)'}${props.length ? ' · ' + props.join(', ') : ''}`,
    voiceOver: 'Voice-over',
    listSep: ', ',
    sizeName: (size) => SHOT_SIZES[size]?.phrase ?? size,
    cameraLabel: (camera) => camera,
    recipeNone: '—',
    recipeName: (card, id) => card?.name_en ?? card?.name ?? id,
    recipeDrift: (sizes, cameras) =>
      `Recipe suggests ${[sizes.length ? `size ${sizes.join(' / ')}` : '', cameras.length ? `camera ${cameras.join(' / ')}` : ''].filter(Boolean).join(' · ')} — advisory, not gated`,
    recipeHint: (n) => `ℹ️ ${n} cut(s) deviate from their recipe's suggested size / camera — a recipe is vocabulary, not law: advisory only (see the ≠ marks in the Recipe column)`,
    speakerLine: (name, text) => `${name}: “${text}”`,
    withLighting: (name, lighting) => (lighting ? `${name} (${lighting})` : name),
    fmtMin: (sec) => `${Math.floor(sec / 60)} min ${Math.round(sec % 60)} s`,
    unitSeg: 'seg',
    unitCut: 'cuts',
    colophon: 'Cut by the model from the script: a segment = one generation call (≤15s), a cut = a 2–5s edit inside it, one keyframe per cut. Alignment lines, cut marks, dialogue and prompt discipline are all audited deterministically by the script. Frames are generated through codex with the scene and character sheets as references.',
  },
};

const tOf = i18nLookup(I18N);

/* ------------------------------------------------------------------ */
/* render 公共                                                          */
/* ------------------------------------------------------------------ */


function namer(ctx = {}, t = I18N.zh) {
  const charName = new Map((ctx.outline?.characters ?? []).map((c) => [c.id, c.name]));
  const sceneName = new Map((ctx.art?.scenes ?? []).map((s) => [s.id, s.name]));
  const propName = new Map((ctx.art?.props ?? []).map((p) => [p.id, p.name]));
  return {
    char: (id) => (id === 'VO' ? t.voiceOver : charName.get(id) ?? id),
    scene: (id) => sceneName.get(id) ?? id,
    prop: (id) => propName.get(id) ?? id,
  };
}

/**
 * cut 的「配方」列：卡名 + 偏离建议景别／运镜时的 ≠ 标记。
 * 偏离只提示不设门——配方是语汇不是法条（理由见 recipeDrift 上方注释）。
 */
function cutRecipe(cut, recipes, t) {
  const id = typeof cut?.recipe === 'string' ? cut.recipe : '';
  if (!id) return null;
  const card = recipes?.get(id) ?? null;
  const off = recipeDrift(cut, card);
  return {
    name: t.recipeName(card, id),
    drift: off.sizes.length || off.cameras.length ? t.recipeDrift(off.sizes, off.cameras) : '',
  };
}

/** 分镜认领的节拍 → 画面摘要（动作原文 + 台词行），模型不重写。 */
function cutBeats(cut, scene) {
  if (!scene) return [];
  const [from, to] = cut.beats ?? [];
  return scene.beats.slice((from ?? 1) - 1, to ?? 0);
}

/* ------------------------------------------------------------------ */
/* render — markdown                                                   */
/* ------------------------------------------------------------------ */


export function renderMarkdown(board, ctx = {}) {
  const t = tOf(ctx.lang ?? board?.lang);
  const n = namer(ctx, t);
  const expanded = expandScript(ctx.script);
  const stats = computeStats(board, ctx.script);
  const eps = board.episodes;
  const out = [`# ${t.docTitle(board.source, eps[0]?.ep, eps[eps.length - 1]?.ep)}`, ''];

  for (const [i, ep] of eps.entries()) {
    const st = stats.episodes[i];
    const sEp = expanded.get(ep.ep);
    out.push(`## E${String(ep.ep).padStart(2, '0')}`, '', `> ${t.epHead(st.segments, st.cuts, st.totalSeconds, st.target)}`, '');
    for (const seg of ep.segments) {
      const scene = sEp?.scenes?.[seg.sceneIndex - 1];
      out.push(`### ${seg.id} · ${scene ? t.withLighting(n.scene(scene.sceneId), scene.lighting) : '?'} · ${t.segHead(segSeconds(seg), seg.cuts.length)}`, '');
      out.push(mdHead(t.cutCols));
      const starts = cutStarts(seg.cuts);
      seg.cuts.forEach((cut, ci) => {
        const summary = cutBeats(cut, scene)
          .map((b) => (b.kind === 'line' ? t.speakerLine(n.char(b.speaker), b.text) : b.text))
          .join(' ');
        // md 没有 title 属性，偏离的建议值直接写在格子里
        const rc = cutRecipe(cut, ctx.recipes, t);
        out.push(mdRow([
          `#${ci + 1}`, `${starts[ci].toFixed(2)}s`, cut.seconds,
          t.sizeName(cut.size), t.cameraLabel(cut.camera),
          rc ? `${rc.name}${rc.drift ? ` ≠（${rc.drift}）` : ''}` : t.recipeNone,
          summary, (cut.characters ?? []).map(n.char).join(t.listSep),
        ]));
      });
      out.push('', `**${t.h3Section}**`, '', '```text', seg.h3Prompt ?? '', '```', '');
    }
  }

  out.push(`## ${t.secBatches}`, '', mdHead(t.batchCols));
  for (const b of stats.batches) {
    out.push(mdRow([`${b.sceneId} ${n.scene(b.sceneId)}`, b.lighting, b.segments.join(t.listSep), b.characters.map(n.char).join(t.listSep), b.props.map(n.prop).join(t.listSep)]));
  }
  out.push('', `## ${t.secDialogue}`, '', mdHead(t.dialogueCols));
  for (const d of stats.dialogue) out.push(mdRow([`${d.segment}#${d.cut}`, n.char(d.speaker), d.line, d.seconds]));
  out.push('');
  return out.join('\n');
}

/* ------------------------------------------------------------------ */
/* render — html                                                       */
/* ------------------------------------------------------------------ */
/*
 * 与另外四份报告同一套视觉语言。设计约定见 references/report-style.md。
 * 分镜图从工作目录下 <段号>/f<切序>.png 找（imageExists 由 CLI 注入，
 * render 时检查相对工作目录的路径），有就内嵌显示 + 点击放大，
 * 没有就显示占位——不猜、不骗。
 */


export function renderHtml(board, ctx = {}) {
  const lang = ctx.lang ?? board?.lang ?? 'zh';
  const t = tOf(lang);
  const n = namer(ctx, t);
  const expanded = expandScript(ctx.script);
  const stats = computeStats(board, ctx.script);
  const gates = gateReport(board, ctx);
  const failed = gates.filter((g) => !g.ok);
  const eps = board.episodes;
  const params = stats.params;
  const fmtMin = t.fmtMin;

  const SIZE_ALPHA = { 'extreme-wide': 0.25, wide: 0.4, medium: 0.58, close: 0.78, 'extreme-close': 1 };

  // ---- 01 分镜节奏带：段是粗分隔的组，组内每个分镜一段色块 ----
  const rhythmRows = eps
    .map((ep, i) => {
      const st = stats.episodes[i];
      const groups = ep.segments
        .map((seg) => {
          const segs = seg.cuts
            .map((cut, ci) => {
              const w = st.totalSeconds ? (cut.seconds / st.totalSeconds) * 100 : 0;
              const alpha = SIZE_ALPHA[cut.size] ?? 0.5;
              return `<a class="seg" href="#seg-${esc(seg.id)}" style="width:${r1(w)}%;background:rgba(138,51,36,${alpha})" title="${esc(`${seg.id}#${ci + 1} · ${cut.seconds}s · ${t.sizeName(cut.size)} · ${cut.camera}`)}"></a>`;
            })
            .join('');
          const gw = st.totalSeconds ? (segSeconds(seg) / st.totalSeconds) * 100 : 0;
          return `<span class="rseg" style="width:${r1(gw)}%">${segs}</span>`;
        })
        .join('');
      return `<div class="rrow"><span class="rep">E${String(ep.ep).padStart(2, '0')}</span><div class="rtrack">${groups}</div><span class="rval">${esc(t.rhythmVal(st.segments, st.cuts, st.totalSeconds))}</span></div>`;
    })
    .join('\n');
  const rhythmLegend = Object.keys(SHOT_SIZES)
    .map((k) => `<i><span class="sw" style="background:rgba(138,51,36,${SIZE_ALPHA[k]})"></span>${esc(t.sizeName(k))}</i>`)
    .join('');

  // ---- 02 分集分镜表：段卡（主分镜图 + 子分镜条 + 分镜行） ----
  const epBlocks = eps
    .map((ep, i) => {
      const st = stats.episodes[i];
      const sEp = expanded.get(ep.ep);
      const cards = ep.segments
        .map((seg) => {
          const scene = sEp?.scenes?.[seg.sceneIndex - 1];
          const starts = cutStarts(seg.cuts);
          const frame = (ci) => `${seg.id}/f${ci + 1}.png`;
          const has = (ci) => (ctx.imageExists ? ctx.imageExists(frame(ci)) : false);

          // 主分镜图区：图出全的段保留原 master+subs 层级；有缺图的段每切一格——
          // 有图的格显示原图，无图的格显示整宽提示词卡 + 复制按钮（混合情况按格判断）
          const hasAll = seg.cuts.every((_, ci) => has(ci));
          let master, subs;
          if (hasAll) {
            master = `<img class="frame" src="${esc(frame(0))}" alt="${esc(`${seg.id}#1`)}" loading="lazy">`;
            subs = seg.cuts.length > 1
              ? `<div class="subs">${seg.cuts
                  .slice(1)
                  .map((cut, ci) => `<img class="subf" src="${esc(frame(ci + 1))}" alt="${esc(`${seg.id}#${ci + 2}`)}" loading="lazy">`)
                  .join('')}</div>`
              : '';
          } else {
            master = `<div class="fquad">${seg.cuts
              .map((cut, ci) => {
                const label = ci === 0 ? t.masterLabel : t.subLabel(ci + 1);
                const body = has(ci)
                  ? `<img class="frame" src="${esc(frame(ci))}" alt="${esc(`${seg.id}#${ci + 1}`)}" loading="lazy">`
                  : `<div class="frame ph fcell"><div class="fcell-h"><b>${esc(`${label} · ${t.frameMissing(ci + 1)}`)}</b><button class="copy mini" data-copy="${esc(cut.frame ?? '')}">${esc(t.copy)}</button></div><span class="fprompt">${esc(cut.frame ?? '')}</span></div>`;
                return body;
              })
              .join('\n')}</div>`;
            subs = '';
          }

          const cutRows = seg.cuts
            .map((cut, ci) => {
              const beats = cutBeats(cut, scene);
              const summary = beats
                .map((b) =>
                  b.kind === 'line'
                    ? `<p class="sline"><b>${esc(n.char(b.speaker))}</b>${esc(b.text)}</p>`
                    : `<p class="sact">${esc(b.text)}</p>`,
                )
                .join('');
              // 「配方」列：偏离建议景别／运镜的加 ≠ 上标，建议值写进 title——提示而已，不是门
              const rc = cutRecipe(cut, ctx.recipes, t);
              return `<li class="cut">
  <div class="cut-h">
    <b>#${ci + 1}</b>
    <span class="cut-t">${esc(t.atSec(starts[ci]))} · ${cut.seconds}s</span>
    <span class="cut-sc">${esc(t.sizeName(cut.size))} · ${esc(cut.camera)}</span>
    ${rc ? `<span class="cut-rc">${esc(rc.name)}${rc.drift ? `<sup title="${esc(rc.drift)}">≠</sup>` : ''}</span>` : ''}
    ${(cut.characters ?? []).map((id) => `<span class="chip">${esc(n.char(id))}</span>`).join('')}
    ${(cut.props ?? []).map((id) => `<span class="chip prop">${esc(n.prop(id))}</span>`).join('')}
    <button class="copy mini" data-copy="${esc(cut.frame ?? '')}">${esc(t.framePrompt)}</button>
  </div>
  ${summary}
</li>`;
            })
            .join('\n');

          return `<article class="segcard" id="seg-${esc(seg.id)}">
  <header class="seg-h">
    <b>${esc(seg.id)}</b>
    <span class="sec-badge">${esc(t.secBadge(segSeconds(seg), seg.cuts.length))}</span>
    <span class="chip">${esc(scene ? `${scene.sceneId} ${n.scene(scene.sceneId)}` : '?')}</span>
    ${scene?.lighting ? `<span class="chip lite">${esc(scene.lighting)}</span>` : ''}
    <span class="beatsref">${esc(t.beatsLabel(seg.sceneIndex, seg.cuts[0]?.beats?.[0], seg.cuts[seg.cuts.length - 1]?.beats?.[1]))}</span>
  </header>
  ${master}
  ${subs}
  <div class="duo">
    <ol class="cuts">
${cutRows}
    </ol>
    <div class="ppanel">
      <div class="pp-h">
        <b>${esc(t.h3Prompt)}</b>
        <button class="copy" data-copy="${esc(seg.h3Prompt ?? '')}">${esc(t.copy)}</button>
      </div>
      <pre class="pp on">${esc(seg.h3Prompt ?? '')}</pre>
    </div>
  </div>
  ${seg.note ? `<p class="seg-note">${esc(seg.note)}</p>` : ''}
</article>`;
        })
        .join('\n');
      return `<section class="ep" id="ep-${ep.ep}">
  <header class="ep-h">
    <span class="ep-n">E${String(ep.ep).padStart(2, '0')}</span>
    <span class="ep-est">${esc(t.epHead(st.segments, st.cuts, st.totalSeconds, st.target))}</span>
  </header>
  <div class="shots clip">
    <div class="seggrid">
${cards}
    </div>
  </div>
  <button class="shmore">${esc(t.showSegs)}</button>
</section>`;
    })
    .join('\n');

  // ---- 03 生成批次单 ----
  const batchCards = stats.batches
    .map((b, i) => {
      const sheet = `images/${slug(n.scene(b.sceneId))}-sheet.png`;
      const hasSheet = ctx.imageExists ? ctx.imageExists(sheet) : false;
      return `<article class="batch">
  ${hasSheet ? `<img class="bimg" src="${esc(sheet)}" alt="${esc(n.scene(b.sceneId))}" loading="lazy">` : ''}
  <header class="batch-h"><b>${esc(t.batchLabel(String(i + 1).padStart(2, '0')))}</b><span class="chip">${esc(`${b.sceneId} ${n.scene(b.sceneId)}`)}</span>${b.lighting ? `<span class="chip lite">${esc(b.lighting)}</span>` : ''}</header>
  <div class="batch-shots">${b.segments.map((s) => `<a class="chip mono" href="#seg-${esc(s)}">${esc(s)}</a>`).join('')}</div>
  <p class="batch-need">${esc(t.batchNeed(b.characters.map(n.char), b.props.map(n.prop)))}</p>
</article>`;
    })
    .join('\n');

  // ---- 04 配音对齐单 ----
  const dlgRows = stats.dialogue
    .map((d) => `<tr><td><a href="#seg-${esc(d.segment)}">${esc(d.segment)}</a> #${d.cut}</td><td>${esc(n.char(d.speaker))}</td><td class="serif">${esc(d.line)}</td><td>${d.seconds}</td></tr>`)
    .join('\n');

  const gateList = `<ul class="gate">
  ${gates
    .map(
      // 通过的门只有跳过说明与「没有引用配方」这类备注带 detail——都要显示出来，不静默
      (g) => `<li class="${g.ok ? 'ok' : 'bad'}"><span class="m">${g.ok ? '✓' : '✗'}</span><span>${esc(gateText(g, t.langCode).label)}${
        g.detail ? `<small>${esc(gateText(g, t.langCode).detail)}</small>` : ''
      }</span></li>`,
    )
    .join('\n  ')}
</ul>`;

  return `<!doctype html>
<html lang="${esc(lang)}"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(t.docTitle(board.source, eps[0]?.ep, eps[eps.length - 1]?.ep))}</title>
<style>
:root{
  --paper:#eceded; --panel:#f5f6f5; --side:#e4e6e3; --ink:#191d21; --ink-2:#5b636a; --ink-3:#8c9298;
  --rule:#d2d5d0; --rule-2:#c2c6bf; --seal:#8a3324; --seal-2:#c56a4e; --seal-soft:#8a332412; --ok:#3d6b4f;
  --serif:"Songti SC","STSong","Source Han Serif SC","Noto Serif CJK SC",Georgia,serif;
  --sans:"PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,-apple-system,sans-serif;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.7 var(--sans);-webkit-font-smoothing:antialiased}
.page{max-width:1600px;margin:0 auto;padding:24px 32px 90px}
h1,h2,h3{margin:0;font-weight:400}

.hd{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;border-bottom:2px solid var(--ink);padding-bottom:12px}
.hd h1{font:400 28px/1.1 var(--serif);letter-spacing:.06em}
.hd .sub{font-size:13px;color:var(--ink-2)}
.hd .right{margin-left:auto;display:flex;align-items:center;gap:10px}
.gatepill{display:inline-flex;align-items:center;gap:6px;font:500 12px/1 var(--sans);border-radius:99px;padding:6px 12px}
.gatepill.pass{color:var(--ok);border:1px solid var(--ok)}
.gatepill.fail{color:var(--seal);border:1px solid var(--seal);background:var(--seal-soft)}
.expo{font:500 11px/1 var(--sans);color:var(--ink-2);background:var(--panel);
  border:1px solid var(--rule-2);border-radius:2px;padding:7px 11px;cursor:pointer;transition:.15s}
.expo:hover{border-color:var(--seal);color:var(--seal)}
.expo:focus-visible{outline:2px solid var(--seal);outline-offset:2px}

.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:18px 0 6px}
@media(max-width:980px){.kpis{grid-template-columns:repeat(2,1fr)}}
.kpi{background:var(--panel);border:1px solid var(--rule);border-radius:2px;padding:11px 14px 9px}
.kpi .l{font:500 10px/1 var(--sans);letter-spacing:.18em;color:var(--ink-3)}
.kpi .v{font:400 28px/1.15 var(--serif);margin-top:5px}
.kpi .v small{font:400 14px var(--serif);color:var(--ink-2)}
.kpi .d{font-size:11px;color:var(--ink-2);margin-top:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.kpi.accent{border-top:2px solid var(--seal)}
.galert{margin:14px 0 0;border:1px solid var(--seal);background:var(--seal-soft);border-radius:2px;
  padding:10px 14px;font-size:13px}
.galert b{color:var(--seal)}
.galert span{display:block;font-size:12px;color:var(--ink-2)}

section.top-sec{margin-top:34px}
.sec-h{display:flex;align-items:baseline;gap:12px;border-bottom:1px solid var(--rule-2);padding-bottom:8px;margin-bottom:16px}
.sec-h .no{font:500 12px/1 var(--mono);color:var(--seal)}
.sec-h h2{font:400 20px/1.2 var(--serif);letter-spacing:.05em}
.sec-h .note{margin-left:auto;font-size:12px;color:var(--ink-3)}

/* 01 cut rhythm strip */
.rhythm{background:var(--panel);border:1px solid var(--rule);border-radius:2px;padding:16px 20px 10px}
.rrow{display:grid;grid-template-columns:44px minmax(0,1fr) 150px;gap:12px;align-items:center;padding:5px 0}
.rep{font:500 12px/1 var(--mono);color:var(--ink-2)}
.rtrack{display:flex;height:22px;border:1px solid var(--rule);border-radius:2px;overflow:hidden;background:var(--paper)}
.rseg{display:flex;border-right:2px solid var(--ink-2)}
.rseg:last-child{border-right:0}
.seg{display:block;border-right:1px solid var(--panel)}
.rseg .seg:last-child{border-right:0}
.seg:hover{outline:2px solid var(--ink);outline-offset:-2px}
.rval{font:500 12px/1.5 var(--sans);color:var(--ink-2)}
.legend{display:flex;gap:16px;font-size:12px;color:var(--ink-2);margin:8px 0 2px;flex-wrap:wrap}
.legend i{font-style:normal;display:inline-flex;align-items:center;gap:6px}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px}

/* 02 segment cards */
.ep{background:var(--panel);border:1px solid var(--rule);border-radius:2px;padding:18px 22px;margin-bottom:16px}
.ep-h{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;border-bottom:1px solid var(--rule-2);padding-bottom:10px;margin-bottom:14px}
.ep-n{font:400 22px/1 var(--serif);letter-spacing:.04em;color:var(--seal)}
.ep-est{font-size:12.5px;color:var(--ink-2)}
.shots{position:relative}
.shots.clip{max-height:760px;overflow:hidden}
.shots.clip::after{content:'';position:absolute;left:0;right:0;bottom:0;height:80px;
  background:linear-gradient(180deg,transparent,var(--panel));pointer-events:none}
.shmore{display:block;width:100%;margin-top:8px;font:500 11.5px/1 var(--sans);letter-spacing:.06em;
  color:var(--ink-2);background:var(--paper);border:1px solid var(--rule-2);border-radius:2px;
  padding:7px 0;cursor:pointer;transition:.15s}
.shmore:hover{border-color:var(--seal);color:var(--seal)}
.shmore:focus-visible{outline:2px solid var(--seal);outline-offset:2px}
.seggrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;align-items:start}
@media(max-width:1100px){.seggrid{grid-template-columns:minmax(0,1fr)}}
.segcard{background:var(--paper);border:1px solid var(--rule);border-radius:2px;padding:12px 14px;display:flex;flex-direction:column;gap:8px}
.seg-h{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.seg-h b{font:500 14px/1 var(--mono);color:var(--seal)}
.sec-badge{font:500 11px/1 var(--mono);border:1px solid var(--seal);color:var(--seal);border-radius:99px;padding:2px 8px}
.beatsref{margin-left:auto;font-size:10.5px;color:var(--ink-3)}
.frame{width:100%;aspect-ratio:16/9;object-fit:cover;border:1px solid var(--rule-2);border-radius:2px;
  cursor:zoom-in;display:block;background:var(--side)}
.frame.ph{display:flex;flex-direction:column;gap:6px;padding:10px 12px;cursor:default;overflow:hidden}
.frame.ph b{font:500 10px/1 var(--sans);letter-spacing:.14em;color:var(--ink-3)}
.frame.ph span{font:400 10.5px/1.55 var(--mono);color:var(--ink-2);overflow:hidden;display:-webkit-box;
  -webkit-line-clamp:5;-webkit-box-orient:vertical}
.subs{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px}
.fquad{display:grid;grid-template-columns:1fr;gap:10px;margin:10px 0}
.fquad .frame.ph{aspect-ratio:auto}
.fquad .fcell{margin:0;min-height:0}
.fcell-h{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:4px}
.fcell-h .copy.mini{margin:0;flex:none}
.frame.ph .fprompt{font:400 10.5px/1.4 var(--mono);color:var(--ink-2);white-space:pre-wrap;word-break:break-word;display:block;
  -webkit-line-clamp:none;-webkit-box-orient:vertical;overflow:visible}
.subf{width:100%;aspect-ratio:16/9;object-fit:cover;border:1px solid var(--rule-2);border-radius:2px;
  cursor:zoom-in;display:block;background:var(--side)}
.subf.ph{display:flex;align-items:center;justify-content:center;cursor:default;
  font:500 10px/1 var(--sans);color:var(--ink-3);letter-spacing:.08em}
.duo{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;align-items:start;border-top:1px solid var(--rule);padding-top:4px}
@media(max-width:900px){.duo{grid-template-columns:minmax(0,1fr)}}
.ppanel{border:1px solid var(--rule);border-radius:2px;background:var(--panel);margin-top:7px}
.pp-h{display:flex;align-items:center;gap:6px;padding:7px 10px;border-bottom:1px solid var(--rule)}
.pp-h b{font:500 11px/1 var(--sans);letter-spacing:.08em;color:var(--ink-2);margin-right:auto}
.pp{display:none;margin:0;padding:9px 12px;font:400 12px/1.8 var(--sans);color:var(--ink);
  white-space:pre-wrap;word-break:break-word;max-height:400px;overflow-y:auto;
  scrollbar-width:thin;scrollbar-color:var(--rule-2) transparent}
.pp.on{display:block}
.pp::-webkit-scrollbar{width:6px}
.pp::-webkit-scrollbar-thumb{background:var(--rule-2);border-radius:3px}
.cuts{margin:0;padding:0;list-style:none}
.cut{padding:7px 0;border-bottom:1px solid var(--rule)}
.cut:first-child{padding-top:11px}
.cut:last-child{border-bottom:0}
.cut-h{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.cut-h b{font:500 12px/1 var(--mono);color:var(--seal)}
.cut-t{font:500 10.5px/1.6 var(--mono);color:var(--ink-3)}
.cut-sc{font-size:11.5px;color:var(--ink-2)}
.cut-rc{font:400 10.5px/1.6 var(--mono);border:1px dashed var(--rule-2);border-radius:2px;padding:0 6px;color:var(--ink-2)}
.cut-rc sup{color:var(--seal-2);font-weight:700;cursor:help;margin-left:2px}
.cut-h .copy{margin-left:auto;opacity:0;transition:.15s}
.cut:hover .copy{opacity:1}
.cut p{margin:3px 0 0;font-size:12px;line-height:1.6}
.sact{color:var(--ink-2)}
.sline{font-family:var(--serif)}
.sline b{font-weight:500;margin-right:6px;color:var(--seal)}
.chip{font:400 10.5px/1.6 var(--mono);border:1px solid var(--rule-2);border-radius:2px;
  padding:0 6px;background:var(--panel);color:var(--ink-2);text-decoration:none}
.chip.lite{border-color:var(--seal-2);color:var(--seal-2)}
.chip.prop{border-color:var(--seal);color:var(--seal)}
.chip.mono{font-family:var(--mono)}
a.chip:hover{border-color:var(--seal);color:var(--seal)}
.prompts{display:flex;gap:6px}
.seg-note{margin:0;font-size:11px;color:var(--ink-3)}

/* 03 generation batches */
.batches{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;align-items:start}
@media(max-width:1100px){.batches{grid-template-columns:minmax(0,1fr)}}
.batch{background:var(--panel);border:1px solid var(--rule);border-radius:2px;padding:14px 18px}
.bimg{width:100%;aspect-ratio:16/9;object-fit:cover;border:1px solid var(--rule-2);border-radius:2px;
  cursor:zoom-in;display:block;margin-bottom:10px}
.batch-h{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.batch-h b{font:500 13px var(--serif);letter-spacing:.06em}
.batch-shots{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}
.batch-need{margin:8px 0 0;font-size:12px;color:var(--ink-2)}

/* 04 audio alignment */
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--rule);font-size:13px}
th,td{padding:8px 12px;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top}
th{font:500 11px/1 var(--sans);letter-spacing:.1em;color:var(--ink-3);background:var(--side)}
tr:last-child td{border-bottom:0}
td:first-child{font-family:var(--mono);font-size:12px;white-space:nowrap}
td a{color:var(--seal);text-decoration:none}
td.serif{font-family:var(--serif)}

.copy{flex:none;font:500 11px/1 var(--sans);color:var(--ink-2);background:var(--panel);
  border:1px solid var(--rule-2);border-radius:2px;padding:5px 10px;cursor:pointer;transition:.15s}
.copy:hover{border-color:var(--seal);color:var(--seal)}
.copy:focus-visible{outline:2px solid var(--seal);outline-offset:2px}
.copy[data-done]{border-color:var(--seal);color:var(--seal)}
.copy.mini{padding:3px 7px;font-size:10px}
.copy.h3{border-color:var(--seal-2);color:var(--seal-2);width:100%}
.copy.h3:hover,.copy.h3[data-done]{border-color:var(--seal);color:var(--seal)}

.gate{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:1fr 1fr;gap:2px 28px}
@media(max-width:900px){.gate{grid-template-columns:1fr}}
.gate li{display:flex;gap:8px;padding:5px 0;font-size:12.5px;line-height:1.55}
.gate .m{flex:none;font-weight:700}
.gate li.ok .m{color:var(--ok)}
.gate li.bad .m{color:var(--seal)}
.gate li.bad{background:var(--seal-soft);border-radius:2px;padding-left:6px}
.gate small{display:block;color:var(--ink-3)}
.gsum{margin:10px 0 0;font-size:12px;color:var(--ink-2)}
.gsum b{color:var(--seal)}

.lightbox{position:fixed;inset:0;background:rgba(20,22,24,.88);display:none;align-items:center;
  justify-content:center;z-index:9;cursor:zoom-out;padding:32px}
.lightbox.on{display:flex}
.lightbox img{max-width:96%;max-height:96%;border:1px solid #555;border-radius:2px}

.foot{margin-top:40px;font-size:11px;color:var(--ink-3);border-top:1px solid var(--rule);padding-top:14px}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
@media print{
  .expo,.copy,.shmore{display:none!important}
  .pp{max-height:none;overflow:visible}
  .duo{grid-template-columns:minmax(0,1fr)}
  .shots.clip{max-height:none}
  .shots.clip::after{display:none}
  .seggrid,.batches{grid-template-columns:minmax(0,1fr)}
  .page{max-width:none;padding:0}
  section.top-sec,.segcard,.batch{page-break-inside:avoid}
  body{background:#fff}
}
</style></head><body>
<div class="page">

<header class="hd">
  <h1>${esc(board.source)}</h1>
  <span class="sub">${esc(t.kicker)} · ${esc(t.epRange(eps[0]?.ep, eps[eps.length - 1]?.ep))}</span>
  <span class="right">
    <span class="gatepill ${failed.length ? 'fail' : 'pass'}">${failed.length ? '✗' : '✓'} ${esc(t.gatePill(gates.length - failed.length, gates.length))}</span>
    <button class="expo" data-name="${esc(slug(board.source))}-storyboard.json">${esc(t.exportJson)}</button>
  </span>
</header>

<div class="kpis">
  <div class="kpi accent"><div class="l">${esc(t.kpi.segments)}</div><div class="v">${stats.totals.segments} <small>${esc(t.unitSeg)}</small></div><div class="d">${esc(t.kpi.segmentsSub(params.maxSegmentSeconds))}</div></div>
  <div class="kpi"><div class="l">${esc(t.kpi.cuts)}</div><div class="v">${stats.totals.cuts} <small>${esc(t.unitCut)}</small></div><div class="d">${esc(t.kpi.cutsSub(stats.totals.avgCutSeconds))}</div></div>
  <div class="kpi"><div class="l">${esc(t.kpi.time)}</div><div class="v">${esc(fmtMin(stats.totals.seconds))}</div><div class="d">${esc(t.kpi.timeSub(fmtMin(stats.totals.targetSeconds)))}</div></div>
  <div class="kpi"><div class="l">${esc(t.kpi.batches)}</div><div class="v">${stats.batches.length}</div><div class="d">${esc(t.kpi.batchesSub)}</div></div>
  <div class="kpi"><div class="l">${esc(t.kpi.lines)}</div><div class="v">${stats.totals.withLines} <small>${esc(t.unitSeg)}</small></div><div class="d">${esc(t.kpi.linesSub)}</div></div>
</div>
${failed.length ? `<div class="galert"><b>✗ ${esc(t.gatesFail(failed.length))}</b>${failed.map((g) => `<span>${esc(gateText(g, t.langCode).label)}${g.detail ? ` — ${esc(gateText(g, t.langCode).detail)}` : ''}</span>`).join('')}</div>` : ''}

<section class="top-sec" id="sec-rhythm">
  <div class="sec-h"><span class="no">01</span><h2>${esc(t.secRhythm)}</h2><span class="note">${esc(t.rhythmNote)}</span></div>
  <div class="rhythm">
    <div class="legend">${rhythmLegend}</div>
${rhythmRows}
  </div>
</section>

<section class="top-sec" id="sec-segments">
  <div class="sec-h"><span class="no">02</span><h2>${esc(t.secSegments)}</h2><span class="note">${esc(t.segmentsNote)}</span></div>
${epBlocks}
</section>

<section class="top-sec" id="sec-batches">
  <div class="sec-h"><span class="no">03</span><h2>${esc(t.secBatches)}</h2><span class="note">${esc(t.batchesNote)}</span></div>
  <div class="batches">
${batchCards}
  </div>
</section>

<section class="top-sec" id="sec-dialogue">
  <div class="sec-h"><span class="no">04</span><h2>${esc(t.secDialogue)}</h2><span class="note">${esc(t.dialogueNote)}</span></div>
  <table><thead><tr>${t.dialogueCols.map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead>
  <tbody>
${dlgRows}
  </tbody></table>
</section>

<section class="top-sec" id="sec-gates">
  <div class="sec-h"><span class="no">05</span><h2>${esc(t.secGates)}</h2></div>
  ${gateList}
  <p class="gsum">${failed.length ? `<b>${esc(t.gatesFail(failed.length))}</b>` : esc(t.gatesPass)}</p>
</section>

<p class="foot">${esc(t.colophon)}</p>
</div>

<div class="lightbox" id="lightbox"><img alt=""></div>

<script type="application/json" id="storyboard-data">${embedJson(board)}</script>
<script>
const L = ${JSON.stringify({ copied: t.copied, failed: t.copyFailed, show: t.showSegs, hide: t.hideSegs })};

// 分集分镜表：段卡区默认最多 760px。不超高的集直接放开；超高的集点开/收起
document.querySelectorAll('.shmore').forEach((btn) => {
  const zone = btn.previousElementSibling;
  if (zone.scrollHeight <= 780) {
    zone.classList.remove('clip');
    btn.remove();
    return;
  }
  btn.addEventListener('click', () => {
    const clipped = zone.classList.toggle('clip');
    btn.textContent = clipped ? L.show : L.hide;
    if (clipped) zone.closest('.ep').scrollIntoView({ block: 'nearest' });
  });
});

// 点图放大（主分镜图 / 子分镜图 / 批次场景图）
const lb = document.getElementById('lightbox');
document.addEventListener('click', (e) => {
  const img = e.target.closest('img.frame, img.subf, img.bimg');
  if (img) {
    lb.querySelector('img').src = img.src;
    lb.classList.add('on');
    return;
  }
  if (e.target.closest('#lightbox')) lb.classList.remove('on');
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') lb.classList.remove('on');
});

// 复制提示词
document.addEventListener('click', async (e) => {
  const btn = e.target.closest('.copy');
  if (!btn) return;
  e.preventDefault();
  const label = btn.textContent;
  try {
    await navigator.clipboard.writeText(btn.dataset.copy);
    btn.textContent = L.copied;
    btn.dataset.done = '1';
  } catch {
    btn.textContent = L.failed;
  }
  setTimeout(() => { btn.textContent = label; delete btn.dataset.done; }, 1600);
});

// 导出：报告自己带着完整的 storyboard.json，下载的是它原样
document.querySelector('.expo').addEventListener('click', (e) => {
  const btn = e.currentTarget;
  const url = URL.createObjectURL(
    new Blob([document.getElementById('storyboard-data').textContent], { type: 'application/json' }),
  );
  const a = Object.assign(document.createElement('a'), { href: url, download: btn.dataset.name });
  a.click();
  // 别立刻回收——Safari 会抢在下载读完之前撤掉 blob
  setTimeout(() => URL.revokeObjectURL(url), 10000);
});
</script>
</body></html>`;
}

/* ------------------------------------------------------------------ */
/* CLI                                                                 */
/* ------------------------------------------------------------------ */

const USAGE = `cine-storyboard.mjs — cine-storyboard skill 的确定性工具（分镜）

  seed <script.json> [--eps 1-3]              从剧本预填节拍工作底稿（打印到 stdout）
  validate <sb.json> --script <script.json>   校验；有违规逐条打印并 exit 1
           [--outline] [--cast] [--art]       outline/cast 查提示词人名；art 只管显示名字
           [--shots <卡片目录>]                挂载镜头配方卡库，开 shot-recipe 门（不给就跳过）
           [--ref2va]                         画布模式：只有角色图/场景图参考，开 h3-references 门
  checkup <sb.json> --script <script.json>    只打印质量门 ✓/✗，有未过项 exit 1
          [--shots <卡片目录>]
  audit <sb.json> --script <script.json>      只读的电影感循环评估 A，打印 JSON，不写门日志
        [--outline] [--cast] [--art]           与生产 validate/checkup 分离；缺上下文标 incomplete
  render <sb.json> --script <script.json>     渲染报告到 stdout（默认 --md）
         [--html|--md] [--outline] [--art]    分镜图从 ./<段号>/f<切序>.png 找
         [--lang zh|en]                       报告界面语言（默认 zh；未指定时读取 JSON 顶层 lang 字段）
         [--shots <卡片目录>]                  报告的「配方」列显示卡名并标注建议景别／运镜的偏离
  export <sb.json> --script <script.json>     导出 H3 投产包：每段一个文件夹 <段号>/prompt.md
         [--out .]                            （分镜图 f1..fN.png 同住）+ 根部 manifest.json
  stats                                       读当前目录的 .gates.jsonl，汇总哪道门最常响、
                                              哪道门从没响过（validate/checkup 会自动累积）
  slug <name>                                 剧名转安全文件名

validate 与 checkup 每次都会把门的结果追加到当前目录的 .gates.jsonl。
积累几十次之后跑 stats，就知道模型最常违反哪条规则——那条规则的措辞该改。
不想记就加 --no-log；写不进去会静默跳过，不影响校验本身。`;



/*
 * --shots 只接受卡片目录，不接受导出的 shots.json：中间产物必然会漂，
 * 卡片 .md 才是唯一来源。目录里读不到卡片就直接报错——挂了却没生效
 * 比没挂更坏。
 */
function loadShots(dir) {
  if (/\.json$/i.test(dir)) {
    throw new Error('--shots 只接受卡片目录（shot-recipes/references/cards），不接受导出的 shots.json——中间产物必然会漂');
  }
  const cards = loadRecipes(dir);
  if (!cards.size) throw new Error(`--shots ${dir} 里没读到卡片（.md）——请指向 shot-recipes/references/cards`);
  return cards;
}

function loadCtx(rest) {
  const get = (name) => {
    const path = flag(rest, name);
    return path ? readJson(path) : null;
  };
  const shots = flag(rest, '--shots');
  return {
    script: get('--script'), outline: get('--outline'), cast: get('--cast'), art: get('--art'),
    recipes: shots ? loadShots(shots) : null,
    ref2va: rest.includes('--ref2va'),
  };
}

function main(argv) {
  const [cmd, ...rest] = argv;

  if (!cmd || cmd === '-h' || cmd === '--help') {
    console.log(USAGE);
    process.exit(cmd ? 0 : 1);
  }

  if (cmd === 'seed') {
    const [path] = rest;
    if (!path) throw new Error('用法：seed <script.json> [--eps 1-3]');
    const range = flag(rest, '--eps');
    let epRange = null;
    if (range) {
      const m = String(range).match(/^(\d+)-(\d+)$/) ?? String(range).match(/^(\d+)$/);
      if (!m) throw new Error('--eps 形如 3 或 1-6');
      epRange = m[2] ? [Number(m[1]), Number(m[2])] : [Number(m[1]), Number(m[1])];
    }
    console.log(JSON.stringify(seedFromScript(readJson(path), epRange, loadCtx(rest)), null, 2));
    return;
  }

  if (cmd === 'audit') {
    const [path] = rest;
    if (!path) throw new Error('用法：audit <storyboard.json> --script <script.json> [--outline] [--cast] [--art]');
    const report = auditStoryboard(readJson(path), loadCtx(rest));
    console.log(JSON.stringify(report, null, 2));
    process.exitCode = report.status === 'pass' ? 0 : report.status === 'incomplete' ? 2 : 1;
    return;
  }

  if (cmd === 'validate' || cmd === 'checkup') {
    const [path] = rest;
    if (!path) throw new Error(`用法：${cmd} <storyboard.json> --script <script.json> [--outline] [--cast]`);
    const board = readJson(path);
    const ctx = loadCtx(rest);
    if (!ctx.script) throw new Error('分镜离开剧本没有意义——必须给 --script <script.json>');
    if (!ctx.outline && !ctx.cast) console.error('⚠️ 没给 --outline / --cast，跳过提示词人名检查');

    // 门的结果追加到 .gates.jsonl——validate 与 checkup 都记，
    // 这样「跑过多少次」这个分母才是全的
    const logGates = (gates) => {
      if (rest.includes('--no-log')) return;
      try {
        const rows = gateLogEntries(gates, { doc: basename(path), at: new Date().toISOString() });
        if (rows.length) appendFileSync(GATE_LOG, rows.map((r) => JSON.stringify(r)).join('\n') + '\n');
      } catch { /* 写不进去就算了，日志不能挡住主流程 */ }
    };

    if (cmd === 'checkup') {
      const gates = gateReport(board, ctx);
      logGates(gates);
      for (const g of gates) console.log(`${g.ok ? '✓' : '✗'} ${g.label}${g.detail ? ` — ${g.detail}` : ''}`);
      const failedN = gates.filter((g) => !g.ok).length;
      console.log(failedN ? `\n✗ ${failedN} 项未过` : '\n✓ 全部通过');
      // 建议景别／运镜的偏离只在这里提示，不进门——配方是语汇不是法条，
      // 而且可选挂载的东西一旦变严就没人挂了
      if (ctx.recipes) {
        const drifted = [];
        for (const ep of board?.episodes ?? []) {
          for (const seg of ep?.segments ?? []) {
            (seg?.cuts ?? []).forEach((cut, ci) => {
              const card = typeof cut?.recipe === 'string' ? ctx.recipes.get(cut.recipe) : null;
              if (!card) return;
              const d = recipeDrift(cut, card);
              if (d.sizes.length || d.cameras.length) {
                drifted.push(`  ${seg.id}#${ci + 1}「${card.name}」${I18N.zh.recipeDrift(d.sizes, d.cameras)}`);
              }
            });
          }
        }
        if (drifted.length) console.error(`\n${I18N.zh.recipeHint(drifted.length)}\n${drifted.join('\n')}`);
      }
      if (failedN) process.exit(1);
      return;
    }

    logGates(gateReport(board, ctx));
    const problems = validateStoryboard(board, ctx);
    if (problems.length) {
      console.error(`✗ ${problems.length} 处违规：\n`);
      for (const x of problems) console.error('  ' + x);
      process.exit(1);
    }
    for (const g of gateReport(board, ctx)) if (g.advisory) console.log(`⚠️ ${g.label}：${g.detail}`);
    const st = computeStats(board, ctx.script);
    console.log(`✓ ${st.episodes.length} 集 / ${st.totals.segments} 段 / ${st.totals.cuts} 个分镜全部通过校验（共 ${st.totals.seconds}s / 目标 ${st.totals.targetSeconds}s / ${st.batches.length} 个生成批次）`);
    return;
  }

  if (cmd === 'render') {
    const [path] = rest;
    if (!path) throw new Error('用法：render <storyboard.json> --script <script.json> [--html|--md] [--lang zh|en] [--outline] [--art]');
    const board = readJson(path);
    const ctx = loadCtx(rest);
    if (!ctx.script) throw new Error('分镜离开剧本没有意义——必须给 --script <script.json>');
    // 界面语言：--lang > JSON 顶层 lang 字段 > 'zh'（后两级在渲染器里兜底）
    const langFlag = flag(rest, '--lang');
    if (langFlag) ctx.lang = langFlag;
    ctx.imageExists = (rel) => existsSync(resolve(rel));
    process.stdout.write((rest.includes('--html') ? renderHtml(board, ctx) : renderMarkdown(board, ctx)) + '\n');
    return;
  }

  if (cmd === 'export') {
    const [path] = rest;
    if (!path) throw new Error('用法：export <storyboard.json> --script <script.json> [--out h3]');
    const board = readJson(path);
    const ctx = loadCtx(rest);
    if (!ctx.script) throw new Error('分镜离开剧本没有意义——必须给 --script <script.json>');
    const dir = flag(rest, '--out', '.');
    const pack = exportPack(board, ctx.script, { imageExists: (rel) => existsSync(resolve(rel)), dir });
    for (const f of pack.files) {
      mkdirSync(resolve(f.path, '..'), { recursive: true });
      writeFileSync(resolve(f.path), f.content, 'utf8');
    }
    const segN = pack.manifest.length;
    console.log(`✓ ${segN} 段投产包 → ${resolve(dir)}/（每段一个文件夹：分镜图 + prompt.md；根部 manifest.json）`);
    if (pack.missingTotal) console.log(`⚠️ 缺 ${pack.missingTotal} 张分镜图，已在 manifest 的 missing 里标注——喂 H3 前先补齐`);
    return;
  }

  if (cmd === 'stats') {
    let entries = [];
    try {
      entries = readFileSync(GATE_LOG, 'utf8').split('\n').filter(Boolean).map((l) => {
        try { return JSON.parse(l); } catch { return null; }
      }).filter(Boolean);
    } catch {
      console.log(`还没有 ${GATE_LOG}——先在这个目录里跑几次 validate 或 checkup，门的失败会累积到这里。`);
      return;
    }
    const allGates = gateReport({ episodes: [] }, {}).map((g) => g.id);
    const s = summarizeGateLog(entries, allGates);
    console.log(`跑过 ${s.runs} 次，其中 ${s.cleanRuns} 次全过 · 累计 ${s.fails} 条失败\n`);
    if (s.ranked.length) {
      console.log('最常响的门（那条规则模型最常无视，措辞该改）：');
      for (const r of s.ranked) {
        console.log(`  ${String(r.count).padStart(3)} 次  ${r.gate.padEnd(16)} ${r.label}`);
        for (const x of r.samples) console.log(`         ${x.length > 90 ? x.slice(0, 90) + '…' : x}`);
      }
      console.log();
    }
    if (s.silent.length) {
      console.log(`从没响过的门（${s.silent.length} / ${allGates.length}）——可能是死门，也可能规则已经被模型内化：`);
      console.log('  ' + s.silent.join(' / '));
    }
    return;
  }

  if (cmd === 'slug') {
    if (!rest[0]) throw new Error('用法：slug <name>');
    console.log(slug(rest[0]));
    return;
  }

  throw new Error(`未知命令 ${cmd}\n\n${USAGE}`);
}

runCli(import.meta.url, main);
