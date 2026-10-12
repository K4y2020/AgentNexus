// cine-common — helpers the five cine-* stage scripts share. Not a skill.
// Zero npm dependencies (Node 18+ stdlib); it ships beside the skills, so
// copy or link the whole skills/ folder, not a single skill.

import { readFileSync, realpathSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

/* ------------------------------------------------------------------ */
/* CLI                                                                 */
/* ------------------------------------------------------------------ */

export function readJson(path) {
  return JSON.parse(readFileSync(resolve(path), 'utf8'));
}

export function flag(rest, name, fallback = null) {
  const i = rest.indexOf(name);
  return i >= 0 && rest[i + 1] ? rest[i + 1] : fallback;
}

// 软链安装时 argv[1] 是链接路径，两边都取 realpath 才能比得上
export function isMainModule(metaUrl) {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(metaUrl));
  } catch {
    return false;
  }
}

/** 脚本是入口时跑 CLI：管道提前关闭安静退出，出错打印消息并 exit 1。 */
export function runCli(metaUrl, main) {
  if (!isMainModule(metaUrl)) return;
  // `render ... | head` 这类管道提前关闭时安静退出，别甩 EPIPE 堆栈
  process.stdout.on('error', (e) => {
    if (e.code === 'EPIPE') process.exit(0);
    throw e;
  });
  try {
    main(process.argv.slice(2));
  } catch (error) {
    console.error(error instanceof Error ? error.message : error);
    process.exit(1);
  }
}

/* ------------------------------------------------------------------ */
/* 文本                                                                */
/* ------------------------------------------------------------------ */

const UNSAFE_STEM = /[\s/\\:*?"<>|]+/g;
const UNSAFE_STEM_DOT = /[\s/\\:*?"<>|·]+/g;

/** 名字转安全文件名；dot 为真时把间隔号「·」也换成连字符。 */
export function safeStem(name, fallback, { dot = false } = {}) {
  const cleaned = String(name)
    .trim()
    .replace(dot ? UNSAFE_STEM_DOT : UNSAFE_STEM, '-')
    .replace(/^-+|-+$/g, '');
  return cleaned || fallback;
}

export const thText = (s) => typeof s === 'string' && s.trim();
export const r1 = (n) => Math.round(n * 10) / 10;

export const esc = (s) =>
  String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

export const mdRow = (cells) => `| ${cells.map((c) => String(c ?? '').replace(/\|/g, '\\|')).join(' | ')} |`;
export const mdHead = (cols) => [mdRow(cols), mdRow(cols.map(() => '---'))].join('\n');

/** 内嵌进 <script> 的 JSON：`<` 转义，防 `</script` 截断。 */
export function embedJson(value) {
  return JSON.stringify(value).replace(/</g, '\\u003c');
}

/* ------------------------------------------------------------------ */
/* 报告界面语言                                                         */
/* ------------------------------------------------------------------ */

/** 按界面语言取文案表；未内置的语言直接报错。 */
export const i18nLookup = (table) => (lang) => {
  if (lang && !table[lang]) throw new Error('报告界面语言目前内置 zh / en');
  return table[lang ?? 'zh'];
};

/** 报告里的门文案：英文界面取映射，未命中或中文界面回落原文。 */
export const gateTextFor = (labelsEn, skipsEn) => (g, lang) => {
  if (lang !== 'en') return { label: g.label, detail: g.detail };
  const en = labelsEn[g.enKey ?? g.id];
  // 阈值仍由门自己算：把中文标签里出现的数字按序填进 {0} {1}
  const nums = String(g.label).match(/\d+(?:\.\d+)?/g) ?? [];
  const label = en ? en.replace(/\{(\d)\}/g, (m, i) => nums[Number(i)] ?? m) : g.label;
  return { label, detail: skipsEn[g.detail] ?? g.detail };
};

/* ------------------------------------------------------------------ */
/* style presets                                                       */
/* ------------------------------------------------------------------ */

/**
 * Old style ids still found in saved cast/art/storyboard files.
 * @deprecated `realistic` was the name of `semi-realistic` before photoreal existed;
 * remove the alias in 0.14.0.
 */
export const STYLE_ALIASES = { realistic: 'semi-realistic' };

/** The preset id a saved or requested style means today. */
export const canonicalStyle = (id) => STYLE_ALIASES[id] ?? id;

/* ------------------------------------------------------------------ */
/* artwork medium                                                      */
/* ------------------------------------------------------------------ */

// Words that name a medium but describe composition, negation or an object in the scene.
const NOT_A_MEDIUM = [
  /\b(?:like|as)\s+(?:an?\s+)?(?:ID|passport|portrait|mugshot|yearbook)\s+photo(?:graph)?\b/gi,
  /\b(?:no|not|without|avoid)\s+(?:photorealistic|photographic|live[ -]action|3d\s+render(?:ing)?)\b/gi,
  /\b(?:anime|manga|cartoon)\s+(?:posters?|figur(?:es|ines?)|stickers?|merch(?:andise)?|prints?|wallpapers?|dolls?|plush(?:ies|es)?|keychains?|books?|magazines?|cards?|illustrations?)\b/gi,
  /\b(?:framed|family|old|wedding|polaroid|printed)\s+photo(?:graph)?\b/gi,
  /\bphoto\s+(?:frames?|albums?|booths?|wall)\b/gi,
];

/** The artwork medium families a prompt names: live-action, anime, cartoon, painted, 3d. */
export function artworkMedium(text) {
  const value = NOT_A_MEDIUM.reduce((rest, pattern) => rest.replace(pattern, ''), String(text ?? ''));
  const families = [];
  if (/\blive[ -]action\b|\bphoto(?:realistic|graph(?:ic|y)?)\b|真人实拍|照片写实/i.test(value)) families.push('live-action');
  if (/\banime\b|\bghibli\b|吉卜力|手绘动画/i.test(value)) families.push('anime');
  else if (/\bcartoon\b|\b2d\s+animation\b|卡通|二维动画/i.test(value)) families.push('cartoon');
  else if (/\bpainterly\b|\bhand[ -]painted\b|\b(?:oil|watercolou?r)\s+paint(?:ing|ed)\b|绘画风|油画|水彩/i.test(value)) families.push('painted');
  if (/\b3d\s+(?:render|animation)\b|三维动画/i.test(value)) families.push('3d');
  return families;
}

/** Reference images whose prompt names more than one medium, and the media all of them name. */
export function mediumAudit(references) {
  const mixed = [];
  const media = new Set();
  for (const { label, text } of references) {
    const families = artworkMedium(text);
    if (families.length > 1) mixed.push(`${label}（${families.join('/')}）`);
    else if (families.length === 1) media.add(families[0]);
  }
  return { mixed, media: [...media] };
}
