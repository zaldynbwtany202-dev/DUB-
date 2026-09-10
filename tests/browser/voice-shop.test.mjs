/**
 * Tests for the 100-sample voice shop and the raw voice library on the site.
 *
 * Both pages came over from main as plain files; what could silently break is
 * everything this session learned the hard way: mp3 playback must go through
 * raw.githubusercontent.com with the branch VERBATIM (the Pages host 500s on
 * audio, and %2F-encoded branches 404 on raw), the choice must land on the
 * branch the agent reads, and index.html must expose the tabs so #shop opens
 * them. The Python reader side is pinned from the pytest suite; here it is
 * the browser half plus the cross-file contracts.
 *
 * Run: node tests/browser/voice-shop.test.mjs
 */
import { readFileSync, existsSync, readdirSync } from 'fs';

let pass = 0, fail = 0;
const ok = (c, m) => { c ? pass++ : (fail++, console.log('FAIL:', m)); };
const read = (p) => readFileSync(new URL(`../../${p}`, import.meta.url), 'utf8');

const shop = read('docs/voice-shop.html');
const lib = read('docs/voice-library.html');
const index = read('docs/index.html');
const studio = read('docs/studio.js');
const upload = read('docs/github-upload.js');
const catalog = JSON.parse(read('docs/voice-shop.json'));

// ── files are really here ────────────────────────────────────────────────
ok(existsSync(new URL('../../docs/voice-shop.json', import.meta.url)), 'catalog json exists');
ok(existsSync(new URL('../../docs/voice-library.json', import.meta.url)), 'library json exists');
const mp3s = readdirSync(new URL('../../docs/voice-shop', import.meta.url));
ok(mp3s.length === 85, `85 shop mp3s present (got ${mp3s.length})`);
ok(mp3s.includes('sample-001.mp3') && mp3s.includes('sample-085.mp3'), 'first and last slots present');
const libAudio = readdirSync(new URL('../../docs/voice-library', import.meta.url))
  .filter((f) => f.endsWith('.mp3'));
ok(libAudio.length === 28, `28 raw library takes present (got ${libAudio.length})`);
ok(catalog.total_slots === 100 && catalog.ready === 85, 'catalog says 100 slots, 85 ready');
ok(catalog.samples.length === 100, 'catalog carries 100 entries');

// ── the audio-host lessons: raw origin, branch verbatim ──────────────────
ok(shop.includes('rawDocs(s.preview_url)'), 'shop preview plays through rawDocs');
ok(!/new Audio\(relative\(/.test(shop), 'no shop Audio element points at the Pages origin');
ok(!shop.includes('github.io/raw') && !shop.includes('github.io/voice-shop'),
   'no shop audio URL targets github.io');
ok(lib.includes("data-audio=") && lib.includes('audioCandidates(el.dataset.audio)'),
   'library audio plays through the candidate list (rawDocs retired there)');
ok(shop.includes("from './audio-url.js'") && lib.includes("from './audio-url.js'"),
   'both pages resolve the audio host through the shared audio-url module');
ok(shop.includes('audioBranches()') && lib.includes('audioBranches()'),
   'both pages read the deployed manifest');
ok(!/raw\.githubusercontent\.com[^`]*encodeURIComponent/.test(shop) &&
   !/raw\.githubusercontent\.com[^`]*encodeURIComponent/.test(lib),
   'no raw URL in either page encodes its branch (raw 404s %2F)');
ok(shop.includes('rawDocsUrl(next, s.preview_url)'), 'shop falls back to the next branch on error');
ok(lib.includes('wireAudio') && lib.includes('audioCandidates(el.dataset.audio)'),
   'library players walk the candidate list too');
ok(lib.includes('data-audio='), 'library audio elements carry their path for wiring');
const dash = read('docs/dashboard.html');
ok(dash.includes("from './audio-url.js'") && dash.includes('rawRepoUrl(br,f.path)'),
   'dashboard videos resolve through the shared module from the deployed branch');
ok(!/raw\.githubusercontent\.com[^`\n]*encodeURIComponent\(BRANCH\)/.test(dash),
   'no dashboard raw URL encodes its branch');
const studioSrc = read('docs/studio.js');
ok(studioSrc.includes('translation-preflight.yml غير موجود'),
   'the preflight button explains a missing workflow instead of HTTP 404');
ok(read('docs/index.html').includes('غير مثبّت في هذا المستودع حالياً'),
   'the settings hint states the workflow is not installed');

// ── the choice channel: writer and reader agree ──────────────────────────
ok(shop.includes('commitTextFile') && lib.includes('commitTextFile'),
   'both pages commit the choice through commitTextFile');
ok(upload.includes('export async function commitTextFile'),
   'github-upload.js exports commitTextFile on this branch');
ok(upload.includes("BRANCH = 'arena/01a07c69-dub'"),
   'the choice lands on the Sendbox branch the agent reads');
ok(!/localStorage\.getItem\(BRANCH_KEY\)/.test(upload),
   'no dynamic branch lookup — a stale session default must not eat the choice');
ok(shop.includes("CHOICE_PATH = 'docs/voice-choice.json'"), 'shop writes the agreed path');
ok(lib.includes("CHOICE_PATH = 'docs/voice-choice.json'"), 'library writes the agreed path');

// ── the studio exposes both, and #shop resolves ──────────────────────────
ok(index.includes('data-tab="shop"') && index.includes('id="tab-shop"'), 'shop tab exists');
ok(index.includes('data-tab="lib100"') && index.includes('id="tab-lib100"'), 'library tab exists');
ok(index.includes('src="voice-shop.html"') && index.includes('src="voice-library.html"'),
   'both panels embed their pages');
ok(studio.includes("'shop'") && studio.includes("'lib100'"),
   'studio.js accepts #shop and #lib100 as initial hashes');
ok(index.indexOf('src="studio.js"') < index.indexOf('auditions-tab.js'),
   'studio.js loads first, so its tab handlers exist before the tab module runs');

// ── the catalog facts the agent relies on ────────────────────────────────
const ready = catalog.samples.filter((s) => s.status === 'ready');
ok(ready.every((s) => typeof s.preview_url === 'string' && s.preview_url.startsWith('voice-shop/')),
   'every ready slot names its preview file under voice-shop/');
ok(ready.every((s) => typeof s.chain === 'string' && s.chain.length > 0),
   'every ready slot declares its filter chain');
ok(catalog.samples.filter((s) => s.status === 'queued').length === 15,
   '15 slots are queued, shown as reserved — never faked as ready');

// ── scripts came along ───────────────────────────────────────────────────
ok(existsSync(new URL('../../scripts/build_voice_catalog.py', import.meta.url)),
   'build_voice_catalog.py present (the catalog says it built this)');
ok(existsSync(new URL('../../scripts/set_voice_choice.py', import.meta.url)),
   'set_voice_choice.py present (the choice applier)');
const setter = read('scripts/set_voice_choice.py');
ok(setter.includes('ALLOWED_FILTERS') && setter.includes('chain_is_safe'),
   'the applier re-reads the catalog and refuses browser filter strings');

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
