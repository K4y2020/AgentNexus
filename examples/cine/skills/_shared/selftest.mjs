#!/usr/bin/env node
// selftest — node _shared/selftest.mjs; exits 1 on the first failure.

import {
  embedJson,
  flag,
  gateTextFor,
  i18nLookup,
  isMainModule,
  mdRow,
  safeStem,
} from './cine-common.mjs';

let passed = 0;
const ok = (cond, msg) => {
  if (!cond) {
    console.error(`✗ ${msg}`);
    process.exit(1);
  }
  passed += 1;
};
const eq = (a, b, msg) => ok(JSON.stringify(a) === JSON.stringify(b), `${msg}: ${JSON.stringify(a)}`);

eq(safeStem('约翰·史密斯 /x', 'cast'), '约翰·史密斯-x', 'safeStem keeps the middle dot by default');
eq(safeStem('约翰·史密斯 /x', 'cast', { dot: true }), '约翰-史密斯-x', 'safeStem folds the middle dot');
eq(safeStem(' // ', 'scene'), 'scene', 'safeStem falls back');
eq(flag(['--lang', 'en'], '--lang'), 'en', 'flag reads a value');
eq(flag(['--lang'], '--lang', 'zh'), 'zh', 'flag falls back without a value');
eq(mdRow(['a|b', null]), '| a\\|b |  |', 'mdRow escapes pipes');
ok(!embedJson({ s: '</script>' }).includes('</'), 'embedJson escapes <');

const tOf = i18nLookup({ zh: { k: '中' }, en: { k: 'en' } });
eq(tOf(null).k, '中', 'i18nLookup defaults to zh');
let threw = false;
try {
  tOf('jp');
} catch (error) {
  threw = error.message.includes('zh / en');
}
ok(threw, 'i18nLookup rejects an unknown language');

const gateText = gateTextFor(
  { len: 'At most {0} chars', alias: 'Alias gate' },
  { '未提供 cast.json，本门跳过（视为通过）': 'cast.json not provided' },
);
const gate = { id: 'len', label: '不超过 35 字', detail: '未提供 cast.json，本门跳过（视为通过）' };
eq(gateText(gate, 'zh'), { label: gate.label, detail: gate.detail }, 'gateText keeps zh');
eq(gateText(gate, 'en'), { label: 'At most 35 chars', detail: 'cast.json not provided' }, 'gateText translates');
eq(gateText({ id: 'x', enKey: 'alias', label: 'y', detail: '' }, 'en').label, 'Alias gate', 'gateText uses enKey');
eq(gateText({ id: 'unknown', label: '原文', detail: '' }, 'en').label, '原文', 'gateText falls back');

ok(!isMainModule(new URL('./cine-common.mjs', import.meta.url).href), 'an imported module is not main');

console.log(`✓ ${passed} checks`);
