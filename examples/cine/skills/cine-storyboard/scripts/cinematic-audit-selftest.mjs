#!/usr/bin/env node
// Invented fixtures for the read-only audit and production gates.
import assert from 'node:assert/strict';
import { auditStoryboard, gateReport, STYLE_PRESETS, validateStoryboard } from './cine-storyboard.mjs';

const clone = (value) => JSON.parse(JSON.stringify(value));
const styleSentence = STYLE_PRESETS.realistic.sentence;
const script = {
  episodes: [{ ep: 1, hook: 'A locked cabinet.', cliff: 'The bell rings.', scenes: [
    { sceneId: 'S01', characters: ['C01', 'C02'], flow: [
      { action: 'Mara places a box on the workbench.' },
      { speaker: 'C01', line: '盒子到了。' },
      { action: 'Niko lowers the curtain.' },
      { speaker: 'C02', line: '先关窗。' },
    ] },
    { sceneId: 'S02', characters: ['C01'], flow: [
      { action: 'Mara enters the stairwell.' }, { speaker: 'C01', line: '灯亮了。' },
    ] },
  ] }],
};
const outline = { characters: [{ id: 'C01', name: 'Mara' }, { id: 'C02', name: 'Niko' }] };
const cast = { style: 'realistic', characters: [
  { id: 'C01', name: 'Mara', aliases: ['Assembler'], image: { prompt: 'Semi-realistic painterly character sheet.' }, voice: { prompt: 'Late-30s female, warm husky alto, low-mid pitch, steady chest support. Moderate volume, even pace. Focused.' } },
  { id: 'C02', name: 'Niko', aliases: ['Keeper'], image: { prompt: 'Semi-realistic painterly character sheet.' }, voice: { prompt: 'Mid-50s male, dry reedy tenor, mid pitch, thin nasal resonance. Low volume, clipped pace. Wary.' } },
] };
const voiceOf = (line) => (line === '先关窗。' ? 'mid-50s male, dry reedy tenor, mid pitch, thin nasal resonance' : 'late-30s female, warm husky alto, low-mid pitch, steady chest support');
const art = { style: 'realistic', scenes: [
  { id: 'S01', image: { prompt: 'Semi-realistic painterly empty workshop.' } },
  { id: 'S02', image: { prompt: 'Semi-realistic painterly empty stairwell.' } },
] };
const prompt = (line, music = 'N/A', delivery = 'says') =>
  `subject_definitions:\n<Subject 1> — ${line === '先关窗。' ? 'C02' : 'C01'}; the worker with a grey apron from <Picture 1>.\n<Subject 2> — ${line === '灯亮了。' ? 'S02' : 'S01'}; the room with a brick wall from <Picture 2>.\nsummary:\n[${line === '先关窗。' ? 'reference generation + keyframe completion' : 'reference generation'}] The worker <Subject 1> opens a box in the room <Subject 2>.\nretention_analysis:\n<Subject 1> (appears in [Shot 1]): fully_preserved - the worker's apron and appearance are retained.\n<Subject 2> (appears in [Shot 1]): fully_preserved - the room's brick wall is retained.\ndetailed_description:\n${styleSentence}\n[Shot 1] Static Shot. At frame left, <Subject 1> (S1), the worker in a grey apron with a ${voiceOf(line)} voice, opens the box beside the brick wall of <Subject 2>. <Subject 1> (S1) ${delivery}: <d>[Chinese] ${line}</d> while the worker's lips remain completely closed.\noverall_soundscape: Wind through the window.\nnon_diegetic_music: ${music}`;
const cut = (beats, characters) => ({ beats, characters, seconds: 4, size: 'medium', camera: 'Static Shot', frame: 'Semi-realistic painterly illustration, medium shot of a worker.' });
const board = { style: 'realistic', episodes: [{ ep: 1, segments: [
  { id: 'E01-01', sceneIndex: 1, cuts: [cut([1, 2], ['C01'])], h3Prompt: prompt('盒子到了。') },
  { id: 'E01-02', sceneIndex: 1, continuesFrom: 'E01-01', cuts: [cut([3, 4], ['C02', 'C01'])], h3Prompt: prompt('先关窗。') },
  { id: 'E01-03', sceneIndex: 2, cuts: [cut([1, 2], ['C01'])], h3Prompt: prompt('灯亮了。', 'N/A') },
] }] };
const ctx = { script, outline, cast, art };
let passed = 0;
const check = (condition, label) => { assert.ok(condition, label); passed += 1; };
const issues = (report, rule) => report.issues.filter((issue) => issue.rule === rule);
const audit = (mutateBoard = () => {}, mutateContext = () => {}) => {
  const copyBoard = clone(board);
  const copyContext = clone(ctx);
  mutateBoard(copyBoard);
  mutateContext(copyContext);
  return auditStoryboard(copyBoard, copyContext);
};
const clean = audit();
check(clean.status === 'pass' && !clean.issues.length && !clean.skips.length, 'complete Ref2VA fixture passes all applicable evaluation rules');
check(clean.evaluated_checks.length === 18 && clean.evaluated_checks.filter((item) => !['h3-voiceover-exact-format', 'h3-nonverbal-vocal-cues'].includes(item.id)).every((item) => item.evaluated > 0), 'all applicable format rules report evaluated counts; absent voice-over and non-verbal cues are not applicable');
check(!issues(clean, 'video-no-film-still').length, 'painterly frames and video prompts contain no film-still instruction');
check(issues(audit((b) => { b.episodes[0].segments[0].h3Prompt += '\nFILM STILL'; }), 'video-no-film-still').length === 1, 'film still is rejected only in the video prompt');
check(issues(audit((b) => { b.episodes[0].segments[0].h3Prompt += '\nfilm-still'; }), 'video-no-film-still').length === 1, 'hyphenated film-still is rejected');
check(issues(audit((b) => { b.episodes[0].segments[0].h3Prompt = ''; }), 'video-no-film-still').length === 1, 'an empty prompt is not a successful check');

check(issues(audit((b) => { b.episodes[0].segments[0].cuts[0].frame = 'Photorealistic medium shot.'; }), 'style-consistency').length === 1, 'painted reference and live-action video conflict');
check(issues(audit(() => {}, (c) => { c.cast.style = 'ghibli'; }), 'style-consistency').length === 1, 'style metadata mismatch is reported');
check(issues(audit(() => {}, (c) => { c.cast.characters[0].image.prompt = 'Hand-painted anime character sheet.'; }), 'style-consistency').length === 3, 'used cast references are compared to each segment');
check(issues(audit(() => {}, (c) => { c.art.scenes[0].image.prompt = 'Cartoon empty workshop.'; }), 'style-consistency').length === 2, 'used scene references are compared');
check(audit((b) => { b.episodes[0].segments[0].cuts[0].frame = 'Cinematic film still, medium shot.'; }).skips.some((item) => item.rule === 'style-consistency'), 'generic cinematic wording is recorded as insufficient evidence');
check(audit((b) => { b.episodes[0].segments[0].h3Prompt = b.episodes[0].segments[0].h3Prompt.replace(styleSentence, 'Cinematic.'); }).skips.some((item) => item.rule === 'style-consistency'), 'weak medium evidence cannot receive pass');
check(!issues(audit(() => {}, (c) => { c.cast.characters[0].image.negativePrompt = 'anime, cartoon'; }), 'style-consistency').length, 'negative prompt style markers are not positive style evidence');
check(!issues(audit(() => {}, (c) => { c.cast.characters[0].image.sheet = 'Portrait framed like an ID photograph, semi-realistic painterly rendering.'; }), 'style-consistency').length, 'ID-photo framing describes composition rather than a photographic medium');
check(!issues(audit(() => {}, (c) => { c.cast.characters[0].image.sheet = 'Semi-realistic painterly rendering without photographic micro-detail.'; }), 'style-consistency').length, 'a negated photographic detail is not positive medium evidence');
check(!issues(audit(() => {}, (c) => { c.cast.characters[0].image.sheet = 'Bust portrait, the bottom edge cut straight and level like an ID photograph, semi-realistic painterly rendering.'; }), 'style-consistency').length, 'any ID-photo simile describes composition rather than a photographic medium');
check(!issues(audit(() => {}, (c) => { c.art.scenes[0].image.prompt = 'Semi-realistic painterly workshop, anime posters and a framed family photograph on the wall.'; }), 'style-consistency').length, 'posters and photographs in the set are objects, not the medium');
check(issues(audit(() => {}, (c) => { c.cast.characters[0].image.sheet = 'Photorealistic rendering with painterly edges.'; }), 'style-consistency').some((item) => String(item.detail).includes('fixed in cast.json/art.json')), 'a reference that mixes media is sent back upstream');
check(issues(audit(() => {}, (c) => { c.cast.characters[0].image.sheet = 'Photorealistic rendering with painterly edges.'; }), 'style-consistency').length === 3, 'conflicting positive medium declarations remain rejected');
check(issues(audit(() => {}, (c) => { c.cast.characters[0].states = [{ id: 'coat', image: { prompt: 'Cartoon character sheet.' } }]; c.script.episodes[0].scenes[0].characterStates = { C01: 'coat' }; }), 'style-consistency').length === 0, 'unused state image does not contaminate references');
check(issues(audit((b) => { b.episodes[0].segments[0].cuts[0].characterStates = { C01: 'coat' }; }, (c) => { c.cast.characters[0].states = [{ id: 'coat', image: { prompt: 'Cartoon character sheet.' } }]; }), 'style-consistency').length === 1, 'used state reference is audited');

check(issues(audit((b) => { b.episodes[0].segments[1].h3Prompt = prompt('先关窗。', 'A jaunty whistle.'); }), 'no-background-music').length === 1, 'audience-only music is rejected in every segment');
check(issues(audit((b) => { b.episodes[0].segments[1].h3Prompt = prompt('先关窗。', 'Continue the previous bowed-string pulse.'); }), 'no-background-music').length === 1, 'English music continuation does not bypass the user policy');
check(issues(audit((b) => { b.episodes[0].segments[1].h3Prompt = prompt('先关窗。', '延续上一段的弦乐脉冲。'); }), 'no-background-music').length === 1, 'Chinese music continuation does not bypass the user policy');
check(!issues(audit((b) => { b.episodes[0].segments[1].h3Prompt = prompt('先关窗。', ' N/A '); }), 'no-background-music').length, 'N/A section padding is allowed');
check(issues(audit((b) => { b.episodes[0].segments[0].h3Prompt = b.episodes[0].segments[0].h3Prompt.replace(/non_diegetic_music:.*/, ''); }), 'no-background-music').length === 1, 'missing music section is a violation');
check(issues(audit((b) => { b.episodes[0].segments[2].h3Prompt = prompt('灯亮了。', 'A solo drumbeat.'); }), 'no-background-music').length === 1, 'music in another scene is also rejected');
check(!issues(audit((b) => { b.episodes[0].segments[0].h3Prompt = b.episodes[0].segments[0].h3Prompt.replace('Static Shot.', 'Static Shot. A radio plays diegetic instrumental music audible to the worker.'); }), 'no-background-music').length, 'diegetic radio music stays in the shot and does not violate audience-only music policy');
check(issues(audit((b) => { b.episodes[0].segments[0].h3Prompt = prompt('盒子到了。', 'N/A.'); }), 'no-background-music').length === 1, 'the required music field is exactly N/A');

check(issues(audit((b) => { b.episodes[0].segments[0].cuts[0].characters = ['C02']; }), 'dialogue-speaker-visible').length === 1, 'scene presence does not substitute for cut presence');
check(!issues(audit((b) => { b.episodes[0].segments[0].cuts[0].characters = []; b.episodes[0].segments[0].h3Prompt = prompt('盒子到了。', undefined, 'says in an off-screen voiceover'); }, (c) => { c.script.episodes[0].scenes[0].flow[1].delivery = '内心独白'; }), 'dialogue-speaker-visible').length, 'script and prompt voice-over permit an off-screen speaker');
check(!issues(audit((b) => { b.episodes[0].segments[0].cuts[0].characters = []; b.episodes[0].segments[0].h3Prompt = prompt('盒子到了。', undefined, 'says in an off-screen voiceover'); }), 'dialogue-speaker-visible').length, 'a matching prompt voice-over can make spoken script dialogue off-screen');
check(issues(audit((b) => { b.episodes[0].segments[0].cuts[0].characters = []; b.episodes[0].segments[0].h3Prompt = prompt('别人的句子。', undefined, 'says in an off-screen voiceover').replace('overall_soundscape:', 'A worker says: <d>[Chinese] 盒子到了。</d>\noverall_soundscape:'); }), 'dialogue-speaker-visible').length === 1, 'voice-over on another dialogue block does not exempt this line');
check(issues(audit((b) => { b.episodes[0].segments[0].cuts[0].characters = []; }, (c) => { c.script.episodes[0].scenes[0].flow[1].delivery = '画外音'; }), 'dialogue-speaker-visible').length === 1, 'voice-over script needs explicit prompt voice-over');
check(!issues(audit((b) => { b.episodes[0].segments[0].cuts[0].characters = []; b.episodes[0].segments[0].h3Prompt = prompt('盒子到了。', undefined, 'voice-over'); }, (c) => { c.script.episodes[0].scenes[0].flow[1].speaker = 'VO'; }), 'dialogue-speaker-visible').length, 'the VO speaker convention is recognized');
check(issues(audit((b) => { b.episodes[0].segments[0].cuts[0].beats = [1, 4]; }), 'one-speaker-per-cut').length === 1, 'one cut with two speakers fails');
check(!issues(audit((b) => { b.episodes[0].segments[0].cuts[0].beats = [1, 4]; }, (c) => { c.script.episodes[0].scenes[0].flow[3].speaker = 'C01'; }), 'one-speaker-per-cut').length, 'multiple lines and actions by one speaker are allowed');
check(audit((b) => { b.episodes[0].segments[0].cuts[0].beats = [1, 99]; }).skips.some((item) => item.rule === 'one-speaker-per-cut'), 'invalid beat claims are not counted as successful speaker checks');
check(audit(() => {}, (c) => { delete c.script; }).skips.some((item) => item.rule === 'dialogue-speaker-visible'), 'missing script reports unevaluated speaker checks');

check(issues(audit((b) => { delete b.episodes[0].segments[1].continuesFrom; }), 'same-scene-continues-from').length === 1, 'same-scene continuation must be present');
check(issues(audit((b) => { b.episodes[0].segments[1].continuesFrom = 'E01-00'; }), 'same-scene-continues-from').length === 1, 'continuation must target the immediately preceding segment');
check(issues(audit((b) => { b.episodes[0].segments[2].continuesFrom = 'E01-02'; }), 'same-scene-continues-from').length === 1, 'cross-scene continuation fails');
check(issues(audit((b) => { b.episodes[0].segments[0].continuesFrom = 'E00-01'; }), 'same-scene-continues-from').length === 1, 'first segment continuation fails');
check(issues(audit((b) => { b.episodes[0].segments[1].cuts[0].characters = ['C02']; }), 'same-scene-continues-from').some((item) => item.location === 'E01-02#1'), 'a continuation that opens on none of the previous last-frame characters fails');
check(issues(audit((b) => { b.episodes[0].segments[1].cuts[0].size = 'close'; }), 'same-scene-continues-from').length === 1, 'a static opening cannot change the previous static framing');
check(!issues(audit((b) => { Object.assign(b.episodes[0].segments[1].cuts[0], { size: 'close', camera: 'Push In' }); }), 'same-scene-continues-from').length, 'a camera move may reframe a continuation that starts on the previous characters');

check(issues(audit(() => {}, (c) => { c.script.episodes[0].scenes[0].flow[1].speaker = 'extra'; }), 'no-internal-labels').length === 1, 'extra speaker is reported without changing the production speaker gate');
check(issues(audit(() => {}, (c) => { c.script.episodes[0].scenes[0].flow[0].action = 'C01 opens P01.'; }), 'no-internal-labels').length === 1, 'C/P IDs in prose are reported');
check(issues(audit((b) => { b.episodes[0].segments[0].h3Prompt += '\nextra closes P01.'; }), 'no-internal-labels').length === 1, 'internal labels in video prose fail');
check(!issues(clean, 'no-internal-labels').length, 'required Ref2VA definition IDs are exempt metadata');
check(!issues(audit(() => {}, (c) => { c.script.episodes[0].scenes[0].flow[0].action = 'Mara adds extra padding and an extraordinary ribbon.'; }), 'no-internal-labels').length, 'ordinary extra adjective is not treated as an internal speaker label');
check(!issues(audit(() => {}, (c) => { c.script.episodes[0].scenes[0].flow[0].action = 'Mara adds extraordinary padding.'; }), 'no-internal-labels').length, 'extraordinary is not an extra label');

check(issues(audit(() => {}, (c) => { c.script.episodes[0].scenes[0].flow[0].action = 'Assembler opens the box.'; }), 'consistent-character-naming').length === 1, 'unique alias and canonical speaker name are reported as two narrative names');
check(!issues(audit(() => {}, (c) => { c.script.episodes[0].scenes[0].flow[1].line = 'Keeper，先关窗。'; }), 'consistent-character-naming').length, 'dialogue forms of address are not narrative renaming');
check(issues(audit((b) => { b.episodes[0].segments[0].h3Prompt += '\nAssembler opens the box.'; }), 'consistent-character-naming').length === 1, 'uniquely mapped aliases in video prose are audited');
check(issues(audit((b) => { b.episodes[0].segments[0].cuts[0].frame += ' Assembler opens the box.'; }), 'consistent-character-naming').length === 1, 'uniquely mapped aliases in frame prose are audited');
check(!issues(audit((b) => { b.episodes[0].segments[0].h3Prompt += '\n<d>[Chinese] Keeper，先关窗。</d>'; }), 'consistent-character-naming').length, 'quoted prompt dialogue does not become narrative naming');
check(audit(() => {}, (c) => { c.cast.characters[1].aliases.push('Assembler'); c.script.episodes[0].scenes[0].flow[0].action = 'Assembler closes the box.'; }).skips.some((item) => item.rule === 'consistent-character-naming'), 'shared aliases are not guessed');
check(!issues(audit(() => {}, (c) => { c.cast.characters[0].name = 'Mara Stone'; c.cast.characters[0].aliases = ['Mara']; c.outline.characters[0].name = 'Mara Stone'; c.script.episodes[0].scenes[0].flow[0].action = 'Mara Stone opens the box.'; c.script.episodes[0].scenes[1].flow[0].action = 'Mara Stone enters the stairwell.'; }), 'consistent-character-naming').length, 'long names are matched before substrings');
check(audit(() => {}, (c) => { delete c.outline; delete c.cast; }).skips.some((item) => item.rule === 'consistent-character-naming'), 'missing identity context is explicit');
check(auditStoryboard({ episodes: [] }, ctx).status === 'incomplete', 'an empty storyboard cannot be a passing audit');

const firstPrompt = (change) => audit((b) => { b.episodes[0].segments[0].h3Prompt = change(b.episodes[0].segments[0].h3Prompt); });
const hasIssue = (change, rule) => issues(firstPrompt(change), rule).length > 0;
// Advisory audit rules are reported but never become production gates.
const advisoryRules = ['h3-nonverbal-vocal-cues'];
const officialRules = clean.evaluated_checks.filter((rule) => rule.id.startsWith('h3-') && !advisoryRules.includes(rule.id)).map((rule) => rule.id);
check(clean.audit_version === 3 && clean.scope === 'read-only', 'audit version identifies the updated evaluation policy');
check(!clean.evaluated_checks.some((rule) => rule.id === 'scene-music-continuity'), 'superseded music-continuity rule is absent');
check(clean.limitations.some((item) => item.includes('appearance, position, action')), 'inline binding does not certify visual-description semantics');
check(clean.limitations.some((item) => item.includes('ordinary wind, rain, impacts')), 'speaker-ID boundary excludes ordinary physical sound sources');
check(clean.limitations.some((item) => item.includes('true asset roles')), 'task-prefix and retention semantics are explicitly outside deterministic proof');
const legacy = firstPrompt(() => 'integrated_multimodal_description:\n[Shot 1] Semi-realistic painterly. A worker (S1) says: <d>[Chinese] 盒子到了。</d>\noverall_soundscape: Wind.\nnon_diegetic_music: N/A');
check(legacy.status === 'incomplete' && officialRules.every((rule) => legacy.skips.some((item) => item.rule === rule)), 'legacy base/keyframe format is skipped for every official Ref2VA rule');
const chinese = audit((b) => { b.promptLang = 'zh'; });
check(chinese.status === 'incomplete' && officialRules.every((rule) => chinese.skips.some((item) => item.rule === rule && item.reason.includes('non-English'))), 'Chinese prompt mode is unsupported by the English official-format audit');
check(firstPrompt((p) => p.replace(/\n/g, '\r\n')).status === 'pass', 'CRLF official sections preserve format recognition');
check(firstPrompt((p) => p.replace('<Subject 1> (S1) says:', '<Subject 1>\n(S1) says:\n')).status === 'pass', 'natural line wraps do not break a speaker binding or dialogue source');
check(!hasIssue((p) => p.replace('brick wall of <Subject 2>', 'brick wall of\n<Subject 2>'), 'h3-subject-inline-binding'), 'line wraps do not turn descriptive reference use into a detached label list');
check(firstPrompt((p) => p.replace(styleSentence, 'The video uses painterly rendering with natural textured edges.')).issues.some((item) => item.rule === 'style-consistency'), 'a different style sentence in just one segment fails exact repetition');
check(hasIssue((p) => p.replace(`${styleSentence}\n[Shot 1]`, `[Shot 1] ${styleSentence}`), 'h3-style-before-shot1'), 'style after Shot 1 fails only the Ref2VA opening-placement check');
check(hasIssue((p) => p.replace(`${styleSentence}\n[Shot 1]`, '<Subject 1>\n[Shot 1]'), 'h3-style-before-shot1'), 'bare reference labels are not style-opening prose');
check(hasIssue((p) => p.replace('[Shot 1] Static Shot.', '[Shot 2] Static Shot.'), 'h3-style-before-shot1'), 'missing Shot 1 is not a passing opening-placement check');
check(hasIssue((p) => p.replace(/At frame left, <Subject 1> \(S1\),[^\n]*?while the worker's lips remain completely closed\./, 'The worker opens the box. On screen: <Subject 1>, in <Subject 2>.'), 'h3-subject-inline-binding'), 'first-use detached On screen list is rejected');
check(issues(firstPrompt((p) => p.replace(/At frame left, <Subject 1> \(S1\),[^\n]*?while the worker's lips remain completely closed\./, 'A worker (S1) says: <d>[Chinese] 盒子到了。</d> On screen: <Subject 1>, in <Subject 2>.')), 'h3-subject-inline-binding').length === 2, 'post-dialogue detached roster remains a list despite original-language punctuation');
check(hasIssue((p) => p.replace(/At frame left, <Subject 1> \(S1\),[^\n]*?while the worker's lips remain completely closed\./, '<Subject 1>, <Subject 2>.'), 'h3-subject-inline-binding'), 'bare subject list is rejected');
check(hasIssue((p) => p.replace(/detailed_description:\n[\s\S]*?overall_soundscape:/, `detailed_description:\n${styleSentence}\n[Shot 1] <Subject 1> opens the box.\noverall_soundscape:`), 'h3-subject-inline-binding'), 'a defined subject absent from description is rejected');
check(!hasIssue((p) => p.replace(/At frame left,[^\n]*?while the worker's lips remain completely closed\./, '<Subject 1> waits in <Subject 2>.'), 'h3-subject-inline-binding'), 'minimal inline prose passes placement without certifying appearance/position/action quality');
check(firstPrompt((p) => p.replace('<Subject 1> — C01;', '<Subject 1> is Mara,').replace('<Subject 2> — S01;', '<Subject 2> is the workshop,')).status === 'pass', 'official natural is definitions are accepted alongside importer-specific dash definitions');
check(hasIssue((p) => p.replace('<Subject 1> (S1) says:', 'Mara (S1) says:'), 'h3-referenced-speaker-binding'), 'script-mapped referenced speaker needs a Subject label at this dialogue clause');
check(hasIssue((p) => p.replace('<Subject 1> (S1) says:', '<Subject 1> says in voice (S1):'), 'h3-referenced-speaker-binding'), 'speaker ID separated from Subject does not satisfy the official binding form');
check(hasIssue((p) => p.replace('<Subject 1> (S1) says:', '<Subject 2> (S1) says:'), 'h3-referenced-speaker-binding'), 'wrong referenced speaker label fails unique script mapping');
check(hasIssue((p) => p.replace('<Subject 1> (S1) says:', '<Subject 1> says:'), 'h3-vocal-source-ids'), 'speaker ID in a prior appearance sentence does not count for this vocal event');
check(hasIssue((p) => p.replace('<Subject 1> (S1) says:', '<Subject 1> (S0) says:'), 'h3-vocal-source-ids'), 'zero speaker ID fails the official numbering boundary');
check(hasIssue((p) => p.replace('<Subject 1> (S1) says:', '<Subject 1> (S3) says:'), 'h3-vocal-source-ids'), 'first vocal event starts at S1 rather than an arbitrary source number');
check(!hasIssue((p) => p.replace('Static Shot.', 'Static Shot. Wind, cloth movement and a box impact are audible.'), 'h3-vocal-source-ids'), 'physical wind/cloth/impact events do not receive invented S IDs');
check(hasIssue((p) => p.replace('Static Shot.', 'Static Shot. A child cheers.'), 'h3-nonverbal-vocal-cues'), 'an untagged non-verbal human voice is reported as an advisory cue');
check(!hasIssue((p) => p.replace('Static Shot.', 'Static Shot. A child cheers.'), 'h3-vocal-source-ids'), 'an untagged non-verbal voice is not a dialogue source-ID failure');
check(!hasIssue((p) => p.replace('Static Shot.', 'Static Shot. A child (S1) cheers.').replace('<Subject 1> (S1) says:', '<Subject 1> (S2) says:'), 'h3-vocal-source-ids'), 'non-verbal voices precede later dialogue in source numbering');
check(hasIssue((p) => p.replace('Static Shot.', 'Static Shot. <Subject 1> cheers.'), 'h3-nonverbal-vocal-cues'), 'a referenced untagged cheer is reported as an advisory cue');
check(!hasIssue((p) => p.replace('Static Shot.', 'Static Shot. <Subject 1> cheers.'), 'h3-referenced-speaker-binding'), 'an untagged cheer does not shift dialogue speaker-binding checks');
check(!hasIssue((p) => p.replace('Static Shot.', 'Static Shot. <Subject 1> (S1) cheers.'), 'h3-vocal-source-ids'), 'one referenced source keeps its ID across cheering and speech');
check(hasIssue((p) => p.replace('Static Shot.', 'Static Shot. <Subject 1> (S1) cheers.').replace('<Subject 1> (S1) says:', '<Subject 1> (S2) says:'), 'h3-vocal-source-ids'), 'a non-verbal referenced source cannot change identity at speech');
check(!hasIssue((p) => p.replace('Static Shot.', 'Static Shot. The worker silently laughs.'), 'h3-vocal-source-ids'), 'explicitly silent physical laughter is not a new audible voice');
for (const sound of ['Distant footsteps.', 'The rustle of loose cash.', 'A structural rumble.', 'A car door closure.', 'The room drone transitions into street traffic.', 'An ominous drone.']) {
  check(hasIssue((p) => p.replace('Wind through the window.', sound), 'h3-synchronized-sound-placement'), `event-specific soundscape is rejected: ${sound}`);
}
check(!hasIssue((p) => p.replace('Wind through the window.', 'A continuous breeze rustles overhead canvas.'), 'h3-synchronized-sound-placement'), 'wind-driven canvas ambience remains ambient');
check(hasIssue((p) => p.replace('Static Shot.', 'Static Shot. Cinematic film still.'), 'style-consistency'), 'video film-still styling also violates the shared reference style rule');
check(!hasIssue((p) => p.replace('<Subject 1> (S1) says:', '<Subject 1> (S1) sings:'), 'h3-vocal-source-ids'), 'singing uses the same vocal-source ID format');
check(hasIssue((p) => p.replace('<Subject 1> (S1) says:', '<Subject 1> sings:'), 'h3-vocal-source-ids'), 'sung lyrics also require a vocal-source ID');
check(!hasIssue((p) => p.replace('<Subject 1> (S1) says:', '<Subject 1> (S1) says off-screen:'), 'h3-referenced-speaker-binding'), 'off-screen physical speaking retains the reference/source binding');
check(!hasIssue((p) => p.replace('<Subject 1> (S1) says:', '<Subject 1> (S1) says off-screen:'), 'h3-voiceover-exact-format'), 'ordinary off-screen speaking is not automatically reclassified as internal voice-over');
check(hasIssue((p) => p.replace('overall_soundscape:', '<Subject 1> (S2) says: <d>[English] A different line.</d>\noverall_soundscape:'), 'h3-vocal-source-ids'), 'a repeated reference speaker cannot change its global source ID');
check(hasIssue((p) => p.replace('overall_soundscape:', '<Subject 2> (S1) says: <d>[English] A different line.</d>\noverall_soundscape:'), 'h3-vocal-source-ids'), 'two distinct referenced sources cannot share one ID');
check(!hasIssue((p) => p.replace('overall_soundscape:', '<Subject 1> (S1) says: <d>[English] A different line.</d>\noverall_soundscape:'), 'h3-vocal-source-ids'), 'repeated vocal events keep one stable reference/source ID');
check(!hasIssue((p) => p.replace('overall_soundscape:', 'Two unseen children (S2,S3) shout: <d>[English] Here.</d>\noverall_soundscape:'), 'h3-vocal-source-ids'), 'official compound IDs are accepted for group speech');
check(!hasIssue((p) => p.replace('overall_soundscape:', 'A low, measured narrator (S2) says: <d>[English] A different line.</d>\noverall_soundscape:'), 'h3-referenced-speaker-binding'), 'an unreferenced narrator may use a stable voice description and source ID');
{
  // A line the script gives to an unnamed extra must not borrow a cast member's face and voice.
  const extraLine = (c) => { c.script.episodes[0].scenes[0].flow[1].speaker = 'extra'; };
  const borrowed = issues(audit(() => {}, extraLine), 'h3-referenced-speaker-binding');
  check(borrowed.length === 1 && borrowed[0].detail.includes('unnamed extra') && borrowed[0].detail.includes('<Subject 1>'), 'an extra line voiced through a cast reference is rejected');
  const described = audit((b) => {
    b.episodes[0].segments[0].h3Prompt = b.episodes[0].segments[0].h3Prompt.replace('<Subject 1> (S1) says:', 'a passing courier (S1) says:');
  }, extraLine);
  check(!issues(described, 'h3-referenced-speaker-binding').length, 'an extra described as their own speaker passes');
}
{
  // Segments are generated separately: a speaker with a cast voice repeats it before their first line in each one.
  const unvoiced = (p) => p.replace(/ with a .*? voice,/, ',');
  const missing = issues(firstPrompt(unvoiced), 'h3-speaker-voice');
  check(missing.length === 1 && missing[0].location === 'E01-01.dialogue1' && missing[0].evidence.voice === voiceOf('盒子到了。'), 'a speaker without the cast voice is rejected and told which voice to write');
  check(issues(audit((b) => { b.episodes[0].segments[1].h3Prompt = unvoiced(b.episodes[0].segments[1].h3Prompt); }), 'h3-speaker-voice').map((item) => item.location).join() === 'E01-02.dialogue1', 'only the segment that drops the voice fails');
  check(hasIssue((p) => unvoiced(p).replace('remain completely closed.', `remain completely closed. Her voice is ${voiceOf('盒子到了。')}.`), 'h3-speaker-voice'), 'a voice written after the first line does not count');
  check(!hasIssue((p) => p.replace(/ with a .*? voice,/, ' (voice: A Late-30s  female, warm husky alto, LOW-MID pitch, steady chest support),'), 'h3-speaker-voice'), 'case, spacing and a leading article do not matter');
  check(!hasIssue((p) => p.replace('overall_soundscape:', '<Subject 1> (S1) says: <d>[English] Again.</d>\noverall_soundscape:'), 'h3-speaker-voice'), 'later lines by the same speaker need no repeat');
  const noVoice = audit((b) => { b.episodes[0].segments[0].h3Prompt = unvoiced(b.episodes[0].segments[0].h3Prompt); }, (c) => { delete c.cast.characters[0].voice; });
  check(!issues(noVoice, 'h3-speaker-voice').length && !noVoice.skips.some((item) => item.rule === 'h3-speaker-voice'), 'a character without a cast voice prompt is not checked');
  check(gateReport(clone(board), { ...ctx, ref2va: true }).find((gate) => gate.id === 'h3-speaker-voice').ok, 'the voiced fixture passes the production gate');
}
check(hasIssue((p) => p.replace('[reference generation]', '[cinematic adaptation]'), 'h3-summary-task-prefix'), 'invented summary task type is rejected');
check(hasIssue((p) => p.replace('[reference generation]', 'reference generation'), 'h3-summary-task-prefix'), 'summary requires bracketed task prefix');
check(hasIssue((p) => p.replace('[reference generation]', '[reference generation + reference generation]'), 'h3-summary-task-prefix'), 'summary cannot duplicate a task type');
check(!hasIssue((p) => p.replace('[reference generation]', '[reference generation + keyframe completion]'), 'h3-summary-task-prefix'), 'valid combined task types pass format without proving the true asset roles');
check(hasIssue((p) => p.replace(/summary:\n[^\n]*/, 'summary:\n[reference generation]'), 'h3-summary-task-prefix'), 'prefix-only summary is incomplete');
check(hasIssue((p) => p.replace(/retention_analysis:\n[\s\S]*?detailed_description:/, 'retention_analysis:\nAll references are retained exactly.\ndetailed_description:'), 'h3-retention-per-line'), 'one aggregate retention sentence does not cover standalone labels');
check(hasIssue((p) => p.replace("<Subject 1> (appears in [Shot 1]): fully_preserved - the worker's apron and appearance are retained.\n<Subject 2> (appears in [Shot 1]): fully_preserved - the room's brick wall is retained.", '<Subject 1>, <Subject 2> (appears in [Shot 1]): fully_preserved - both are retained.'), 'h3-retention-per-line'), 'multiple standalone labels in one retention entry fail');
check(hasIssue((p) => p.replace('fully_preserved - the worker', 'fully_copy - the worker'), 'h3-retention-per-line'), 'audio copy marker cannot be used for visible content');
check(hasIssue((p) => p.replace('fully_preserved - the worker', 'retained_exactly - the worker'), 'h3-retention-per-line'), 'nonofficial relationship marker fails');
check(hasIssue((p) => p.replace('summary:', '<Subject 1> is a second definition of the worker.\nsummary:'), 'h3-retention-per-line'), 'duplicate definitions cannot hide behind a single retention row');
check(hasIssue((p) => p.replace('<Subject 1> (appears in [Shot 1]):', '<Subject 1> (appears throughout):'), 'h3-retention-per-line'), 'visible subject retention names its shots');
check(hasIssue((p) => p.replace('<Subject 1> (appears in [Shot 1]):', '<Subject 1> (appears in [Shot 9]):'), 'h3-retention-per-line'), 'retention cannot claim nonexistent shots');
check(hasIssue((p) => p.replace("fully_preserved - the worker's apron and appearance are retained.", 'fully_preserved'), 'h3-retention-per-line'), 'relationship marker alone needs an explanation');
check(hasIssue((p) => p.replace('<Subject 1> (appears in [Shot 1]):', '<Subject 1> (S1) (appears in [Shot 1]):'), 'h3-retention-per-line'), 'retention must not assign speaker IDs');
check(hasIssue((p) => p.replace('detailed_description:', '<Picture 1> ([Shot 1] first frame): fully_preserved - the composition is retained.\ndetailed_description:'), 'h3-retention-per-line'), 'provenance-only picture labels do not become standalone retention entries');
check(!hasIssue((p) => p.replace('fully_preserved - the worker', 'attribute_transfer - the worker'), 'h3-retention-per-line'), 'all official visible relationship markers are format-valid');
check(hasIssue((p) => p.replace('Wind through the window.', '[Shot 1] A latch clicks at the cut.'), 'h3-synchronized-sound-placement'), 'explicit shot-bound sound belongs inside that shot');
check(hasIssue((p) => p.replace('Wind through the window.', 'At 00:02.000, a latch clicks.'), 'h3-synchronized-sound-placement'), 'explicit timestamp-bound sound belongs inside that shot');
check(hasIssue((p) => p.replace('Wind through the window.', 'A latch clicks at 2.0 seconds.'), 'h3-synchronized-sound-placement'), 'plain-second timestamp-bound sound belongs inside that shot');
check(hasIssue((p) => p.replace('Wind through the window.', 'A metallic tick is synchronized to the hand contact.'), 'h3-synchronized-sound-placement'), 'explicit synchronization belongs inside the matching shot');
check(hasIssue((p) => p.replace('Wind through the window.', 'A worker says <d>[English] Wait.</d>'), 'h3-synchronized-sound-placement'), 'soundscape cannot repeat dialogue');
check(hasIssue((p) => p.replace('Wind through the window.', 'The door slams.'), 'h3-synchronized-sound-placement'), 'recognizable one-off sound cannot be placed in continuous ambience');
check(firstPrompt((p) => p.replace('Wind through the window.', 'Quiet room tone and a ventilation hum continue throughout the video.')).status === 'pass', 'explicit continuous ambience passes the placement-presence check');
check(!hasIssue((p) => p.replace('Static Shot.', 'Static Shot. On impact, the box produces a hollow thud.').replace('Wind through the window.', 'Quiet room tone continues throughout.'), 'h3-synchronized-sound-placement'), 'synchronized sound written inside the shot passes placement check');
check(hasIssue((p) => p.replace('Wind through the window.', ''), 'h3-synchronized-sound-placement'), 'empty soundscape cannot pass its format-presence check');
const voiceover = prompt('盒子到了。', 'N/A', 'says in an off-screen voiceover');
check(firstPrompt(() => voiceover).status === 'pass', 'complete official voice-over example passes all format checks');
check(firstPrompt(() => voiceover.replace('says in an off-screen voiceover', 'says in an off-screen\nvoiceover').replace("while the worker's lips", "while\nthe worker's lips")).status === 'pass', 'line-wrapped fixed voice-over phrase and following clause preserve their normalized wording');
check(hasIssue(() => voiceover.replace('says in an off-screen voiceover', 'groans in an off-screen voiceover'), 'h3-voiceover-exact-format'), 'near-match voice-over verb is rejected');
check(hasIssue(() => voiceover.replace('says in an off-screen voiceover', 'says in an offscreen voiceover'), 'h3-voiceover-exact-format'), 'near-match offscreen spelling is rejected');
check(hasIssue(() => voiceover.replace(" while the worker's lips remain completely closed.", ''), 'h3-voiceover-exact-format'), 'voice-over needs the immediate closed-lips clause');
check(hasIssue(() => voiceover.replace(" while the worker's lips remain completely closed.", " The box closes. while the worker's lips remain completely closed."), 'h3-voiceover-exact-format'), 'closed-lips clause after an intervening action does not pass');
check(hasIssue(() => voiceover.replace('lips remain completely closed', 'mouth stays shut'), 'h3-voiceover-exact-format'), 'closed-mouth paraphrase does not replace the fixed lip wording');
check(hasIssue(() => voiceover.replace('overall_soundscape:', '<Subject 1> (S1) says in an off-screen voiceover: <d>[English] Again.</d>\noverall_soundscape:'), 'h3-voiceover-exact-format'), 'each voice-over block needs its own following closed-lips clause');
check(!hasIssue(() => voiceover.replace('overall_soundscape:', "<Subject 1> (S1) says in an off-screen voiceover: <d>[English] Again.</d> while her lips remain completely closed.\noverall_soundscape:"), 'h3-voiceover-exact-format'), 'repeated voice-over passes with each immediate closed-lips clause');
check(firstPrompt((p) => p.replace(/detailed_description:\n[\s\S]*?overall_soundscape:/, 'overall_soundscape:')).skips.some((item) => item.rule === 'h3-subject-inline-binding'), 'missing detailed description is explicit insufficient evidence');
const audioCue = (p, relation = 'fully_copy', source = 'When <Audio 1> reaches the phrase') => p
  .replace('summary:', '<Audio 1> is a copied soundtrack.\nsummary:')
  .replace('detailed_description:', `<Audio 1>: ${relation} - the track is copied into the target audio.\ndetailed_description:`)
  .replace('<Subject 1> (S1) says: <d>[Chinese] 盒子到了。</d>', `${source}: <d>[English] Turn the page.</d>`);
check(!hasIssue((p) => audioCue(p), 'h3-vocal-source-ids'), 'directly copied soundtrack verbal cue uses Audio alone as allowed by official guide');
check(hasIssue((p) => audioCue(p, 'reference'), 'h3-vocal-source-ids'), 'timbre-reference Audio does not exempt an actual vocal source ID');
check(hasIssue((p) => audioCue(p, 'fully_copy', 'The narrator using <Audio 1> says'), 'h3-vocal-source-ids'), 'a concrete narrator producing words requires an ID despite copied Audio being present');
check(!hasIssue((p) => audioCue(p, 'partially_copy'), 'h3-retention-per-line'), 'audio role accepts its official partially_copy marker');
check(hasIssue((p) => audioCue(p, 'fully_preserved'), 'h3-retention-per-line'), 'visible-preservation marker does not replace an Audio relationship');
check(!hasIssue((p) => p.replace('summary:', '<Picture 3> is a keyframe for [Shot 1].\nsummary:').replace('detailed_description:', '<Picture 3> ([Shot 1] keyframe): fully_preserved - the frame composition is retained.\ndetailed_description:'), 'h3-retention-per-line'), 'standalone keyframe Picture has its own retention line with shot role');
check(!hasIssue((p) => p.replace('summary:', '<Video 1> is a camera-structure reference.\nsummary:').replace('detailed_description:', '<Video 1> (cut and pacing structure): weak_reference - the timing is retained.\ndetailed_description:'), 'h3-retention-per-line'), 'Video structural retention does not require a visible appearance shot');

const targetRules = [...officialRules, 'style-consistency'];
const strictCtx = { ...ctx, ref2va: true };
const strictGates = gateReport(board, strictCtx);
check(strictGates.length === 32 && targetRules.length === 10, 'production exposes 32 gates including all ten reference-format and style rules');
const sceneRules = ['same-scene-continues-from', 'one-speaker-per-cut'];
check(sceneRules.every((rule) => strictGates.some((gate) => gate.id === rule && gate.ok && !gate.detail)), 'continuation and one-speaker gates pass the complete fixture');
const unlinked = clone(board);
delete unlinked.episodes[0].segments[1].continuesFrom;
const unlinkedGate = gateReport(unlinked, strictCtx).find((gate) => gate.id === 'same-scene-continues-from');
check(!unlinkedGate.ok && unlinkedGate.detail.includes('E01-02') && unlinkedGate.detail.includes('应为「E01-01」'), 'production rejects a same-scene segment that does not continue and names the segment to continue from');
check(validateStoryboard(unlinked, strictCtx).some((problem) => problem.includes('[same-scene-continues-from]')), 'production validate surfaces the missing continuation');
const elsewhere = clone(board);
elsewhere.episodes[0].segments[1].cuts[0].characters = ['C02'];
check(gateReport(elsewhere, strictCtx).find((gate) => gate.id === 'same-scene-continues-from').detail.includes('上一段末镜 medium · C01 · Static Shot，本段首镜 medium · C02 · Static Shot'), 'production rejects an opening that drops the previous last-frame characters and shows both shots');
check(gateReport(unlinked, ctx).find((gate) => gate.id === 'same-scene-continues-from').detail.includes('--ref2va'), 'keyframe export mode does not require continuation');
const twoVoices = clone(board);
twoVoices.episodes[0].segments[0].cuts[0].beats = [1, 4];
const twoVoicesGate = gateReport(twoVoices, strictCtx).find((gate) => gate.id === 'one-speaker-per-cut');
check(!twoVoicesGate.ok && twoVoicesGate.detail.includes('E01-01#1') && twoVoicesGate.detail.includes('说话人 C01、C02'), 'production rejects a cut carrying two speakers and names them');
const oneVoice = clone(twoVoices);
const oneVoiceScript = clone(strictCtx);
oneVoiceScript.script.episodes[0].scenes[0].flow[3].speaker = 'C01';
check(gateReport(oneVoice, oneVoiceScript).find((gate) => gate.id === 'one-speaker-per-cut').ok, 'several lines by one speaker in a cut pass production');
check(gateReport(board, { ...strictCtx, script: undefined }).find((gate) => gate.id === 'one-speaker-per-cut').detail.includes('script.json'), 'without a script the speaker gate is skipped like the other script gates');
check(targetRules.every((rule) => strictGates.some((gate) => gate.id === rule && gate.ok && !gate.detail)), 'complete invented reference fixture passes every selected production rule without skip details');
for (const [rule, changePrompt, changeContext] of [
  ['h3-style-before-shot1', (p) => p.replace(`${styleSentence}\n[Shot 1]`, `[Shot 1] ${styleSentence}`)],
  ['h3-subject-inline-binding', (p) => p.replace(/At frame left, <Subject 1> \(S1\),[^\n]*?while the worker's lips remain completely closed\./, 'The worker opens the box. On screen: <Subject 1>, in <Subject 2>.')],
  ['h3-referenced-speaker-binding', (p) => p.replace('<Subject 1> (S1) says:', '<Subject 2> (S1) says:')],
  ['h3-vocal-source-ids', (p) => p.replace('<Subject 1> (S1) says:', '<Subject 1> (S3) says:')],
  ['h3-summary-task-prefix', (p) => p.replace('[reference generation]', 'reference generation')],
  ['h3-retention-per-line', (p) => p.replace(/retention_analysis:\n[\s\S]*?detailed_description:/, 'retention_analysis:\nAll references are retained exactly.\ndetailed_description:')],
  ['h3-synchronized-sound-placement', (p) => p.replace('Wind through the window.', 'A structural rumble.')],
  ['h3-voiceover-exact-format', () => voiceover.replace('says in an off-screen voiceover', 'groans in an off-screen voiceover')],
  ['h3-speaker-voice', (p) => p.replace(/ with a .*? voice,/, ',')],
  ['style-consistency', (p) => p, (c) => { c.cast.characters[0].image.prompt = 'Hand-painted anime character sheet.'; }],
]) {
  const badBoard = clone(board);
  const badCtx = clone(strictCtx);
  badBoard.episodes[0].segments[0].h3Prompt = changePrompt(badBoard.episodes[0].segments[0].h3Prompt);
  changeContext?.(badCtx);
  const failed = gateReport(badBoard, badCtx).find((gate) => gate.id === rule);
  check(!failed.ok && failed.detail.includes(`[${rule}]`) && failed.detail.includes('E01-01'), `production gate rejects and identifies the ${rule} mutant`);
  check(validateStoryboard(badBoard, badCtx).some((problem) => problem.includes(`[${rule}]`)), `production validate surfaces the ${rule} rejection`);
}
const sobbing = clone(board);
sobbing.episodes[0].segments[0].h3Prompt = sobbing.episodes[0].segments[0].h3Prompt.replace('Static Shot.', 'Static Shot. <Subject 1> lowers the box as quiet sobs shake the shoulders.');
check(auditStoryboard(sobbing, strictCtx).issues.some((item) => item.rule === 'h3-nonverbal-vocal-cues' && item.location === 'E01-01.vocalcue1'), 'audit reports an untagged sob as an advisory vocal cue');
check(['h3-vocal-source-ids', 'h3-referenced-speaker-binding'].every((rule) => gateReport(sobbing, strictCtx).find((gate) => gate.id === rule).ok), 'an untagged non-verbal cue does not block production');
const misbound = clone(board);
misbound.episodes[0].segments[0].h3Prompt = misbound.episodes[0].segments[0].h3Prompt.replace('<Subject 1> (S1) says:', '<Subject 2> (S1) says:');
check(gateReport(misbound, strictCtx).find((gate) => gate.id === 'h3-referenced-speaker-binding').detail.includes('原文「'), 'production gate errors quote the offending clause');
const noCompletion = clone(board);
noCompletion.episodes[0].segments[1].h3Prompt = noCompletion.episodes[0].segments[1].h3Prompt.replace('[reference generation + keyframe completion]', '[reference generation]');
check(!gateReport(noCompletion, strictCtx).find((gate) => gate.id === 'h3-summary-task-prefix').ok, 'production rejects a preceding tail-frame continuation without keyframe completion');

for (const [label, mutate] of [
  ['legacy keyframe', (b) => { b.episodes[0].segments[0].h3Prompt = 'integrated_multimodal_description:\n[Shot 1] Semi-realistic painterly. A worker (S1) says: <d>[Chinese] 盒子到了。</d>\noverall_soundscape: Wind.\nnon_diegetic_music: N/A'; }],
  ['Chinese prompt mode', (b) => { b.promptLang = 'zh'; }],
]) {
  const unsupported = clone(board);
  mutate(unsupported);
  const compatibility = gateReport(unsupported, ctx).filter((gate) => targetRules.includes(gate.id));
  check(compatibility.length === 10 && compatibility.every((gate) => gate.ok && gate.detail.includes('--ref2va') && gate.detail.includes('跳过')), `${label} keeps explicit flag-off compatibility for all ten new gates`);
  const strict = gateReport(unsupported, strictCtx);
  check(officialRules.every((rule) => strict.some((gate) => gate.id === rule && !gate.ok && gate.detail.includes(rule))), `${label} is rejected under explicit Ref2VA because official-format audit skips are incomplete evidence`);
}
const noDetail = clone(board);
noDetail.episodes[0].segments[0].h3Prompt = noDetail.episodes[0].segments[0].h3Prompt.replace(/detailed_description:\n[\s\S]*?overall_soundscape:/, 'overall_soundscape:');
check(issues(auditStoryboard(noDetail, ctx), 'h3-subject-inline-binding').length === 0, 'missing detail has no inline audit issue to masquerade as a production rejection');
const noDetailGate = gateReport(noDetail, strictCtx).find((gate) => gate.id === 'h3-subject-inline-binding');
check(!noDetailGate.ok && noDetailGate.detail.includes('Detailed description is unavailable'), 'production inline gate rejects a skip when detailed description is unavailable');
const noDefinitions = clone(board);
noDefinitions.episodes[0].segments[0].h3Prompt = noDefinitions.episodes[0].segments[0].h3Prompt
  .replace(/subject_definitions:\n[\s\S]*?summary:/, 'subject_definitions:\nsummary:')
  .replace(/retention_analysis:\n[\s\S]*?detailed_description:/, 'retention_analysis:\ndetailed_description:');
const definitionSkip = auditStoryboard(noDefinitions, ctx);
check(definitionSkip.skips.some((item) => item.rule === 'h3-retention-per-line' && item.reason.includes('No standalone reference definitions')), 'missing definitions are recorded as an audit skip');
check(issues(definitionSkip, 'h3-retention-per-line').length === 0, 'unparsed definitions produce a pure retention skip without issue-based fallback');
const noDefinitionGate = gateReport(noDefinitions, strictCtx).find((gate) => gate.id === 'h3-retention-per-line');
check(!noDefinitionGate.ok && noDefinitionGate.detail.includes('No standalone reference definitions'), 'production retention gate rejects an unparsed-definition skip');
for (const contextFile of ['cast', 'art']) {
  const missing = clone(strictCtx);
  delete missing[contextFile];
  const failed = gateReport(board, missing).find((gate) => gate.id === 'style-consistency');
  check(!failed.ok && failed.detail.includes(`Missing ${contextFile}.json`), `strict production style gate rejects missing ${contextFile} reference context`);
  check(gateReport(board, { ...missing, ref2va: false }).filter((gate) => targetRules.includes(gate.id)).every((gate) => gate.ok && gate.detail.includes('--ref2va')), `missing ${contextFile} preserves explicit flag-off compatibility`);
}
const noScript = { ...strictCtx, script: undefined };
check(gateReport(board, noScript).filter((gate) => targetRules.includes(gate.id)).every((gate) => gate.ok && !gate.detail), 'the ten reference-format gates retain sufficient cast/art and prompt evidence without a script');
const silent = clone(board);
for (const segment of silent.episodes[0].segments) segment.h3Prompt = segment.h3Prompt.replace(/<Subject 1> \(S1\) says: <d>[\s\S]*?<\/d> while the worker's lips remain completely closed\./, 'The worker closes the box silently.');
const silentAudit = auditStoryboard(silent, ctx);
for (const rule of ['h3-referenced-speaker-binding', 'h3-speaker-voice', 'h3-vocal-source-ids', 'h3-voiceover-exact-format']) {
  const counts = silentAudit.evaluated_checks.find((item) => item.id === rule);
  check(counts.evaluated === 0 && counts.skipped === 0 && counts.issues === 0, `${rule} is inapplicable rather than skipped when the prompt has no vocal or voiceover events`);
  check(gateReport(silent, strictCtx).find((gate) => gate.id === rule).ok, `${rule} can pass production with zero applicable events and complete evidence`);
}

const frozen = clone({ board, ctx });
const freeze = (value) => { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } };
const before = JSON.stringify(frozen);
const productionBefore = gateReport(frozen.board, frozen.ctx);
const strictProductionBefore = gateReport(frozen.board, { ...frozen.ctx, ref2va: true });
freeze(frozen);
auditStoryboard(frozen.board, frozen.ctx);
check(JSON.stringify(frozen) === before, 'audit never mutates input documents');
check(JSON.stringify(gateReport(frozen.board, frozen.ctx)) === JSON.stringify(productionBefore), 'audit does not alter production gate behavior');
check(JSON.stringify(gateReport(frozen.board, { ...frozen.ctx, ref2va: true })) === JSON.stringify(strictProductionBefore), 'strict production gates reuse the same audit results without mutation');
check(JSON.stringify(frozen) === before, 'strict production validation leaves every frozen storyboard/context document unchanged');
check(clean.issues.length === clean.evaluated_checks.reduce((sum, item) => sum + item.issues, 0), 'raw issue totals reconcile with rule counts');
console.log(`✓ ${passed} cinematic audit checks passed`);
