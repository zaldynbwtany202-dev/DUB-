/**
 * Tests for the 100-sample voice shop and the raw voice library on the site.
 *
 * Both pages came over from main as plain files; what could silently break is
 * everything this session learned the hard way: mp3 playback must go through
 * raw.githubusercontent.com (the Pages host answered 500 for audio), the
 * choice must land on the branch the agent reads, and index.html must expose
 * the tabs so #shop opens them. The Python reader side is pinned from the
 * pytest suite; here it is the browser half plus the cross-file contracts.
 *
 * Run: node tests/browser/voice-shop.test.mjs
 */
import { readFileSync, existsSync, readdirSync } from 'fs';
import { execFileSync } from 'child_process';

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

// ── the mp3-500 lesson: audio from raw, never from the Pages host ────────
ok(shop.includes('rawDocs(s.preview_url)'), 'shop preview plays through rawDocs');
ok(shop.includes('raw.githubusercontent.com') && !/new Audio\(relative\(/.test(shop),
   'no shop Audio element points at the Pages origin');
ok(lib.includes('rawDocs(r.audio)'), 'library audio plays through rawDocs');
ok(/const RAW_DOCS = `https:\/\/raw\.githubusercontent\.com\/\$\{OWNER\}\/\$\{REPO\}\/\$\{encodeURIComponent\(BRANCH\)\}\/docs\/`/.test(shop),
   'shop raw base derives from the shared OWNER/REPO/BRANCH');
ok(/const RAW_DOCS = `https:\/\/raw\.githubusercontent\.com/.test(lib),
   'library raw base derives from the shared constants');

// ── the choice channel: writer and reader agree ──────────────────────────
ok(shop.includes("commitTextFile") && lib.includes("commitTextFile"),
   'both pages commit the choice through commitTextFile');
ok(upload.includes('export async function commitTextFile'),
   'github-upload.js exports commitTextFile on this branch');
ok(upload.includes("BRANCH = 'arena/01a07c69-dub'"),
   'the choice lands on the Sendbox branch the agent reads');
ok(!/localStorage\.getItem\(BRANCH_KEY\)/.test(upload),
   'no dynamic branch lookup — a stale session default must not eat the choice');
ok(shop.includes("CHOICE_PATH = 'docs/voice-choice.json'"), 'shop writes the agreed path');
ok(lib.includes("CHOICE_PATH = 'docs/voice-choice.json'"), 'library writes the agreed path');

// The shop reads its saved choice from raw on BRANCH first — the comment in
// the file says why: a relative read would show a stale branch's choice.
ok(shop.includes('`${CHOICE_PATH.split(\'/\').pop()}?ts=') , 'fallback still reads locally last');

// ── the studio exposes both, and #shop resolves ──────────────────────────
ok(index.includes('data-tab="shop"') && index.includes('id="tab-shop"'), 'shop tab exists');
ok(index.includes('data-tab="lib100"') && index.includes('id="tab-lib100"'), 'library tab exists');
ok(index.includes('src="voice-shop.html"') && index.includes('src="voice-library.html"'),
   'both panels embed their pages');
ok(studio.includes("'shop'") && studio.includes("'lib100'"),
   'studio.js accepts #shop and #lib100 as initial hashes');
ok(index.indexOf('src="studio.js"') < index.indexOf('auditions-tab.js'),
   'studio.js still loads before the auditions module');

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
