/**
 * Tests for the shared audio-host resolution (docs/audio-url.js).
 *
 * Playback broke twice for real: github.io 500s on mp3 requests, and then
 * every raw URL pointed at the upload branch (whose tree lacks the shop
 * files) with the branch's slashes percent-encoded into %2F — raw 404s those
 * too. Both lessons are pinned here: audio never comes from the Pages
 * origin, the branch goes into the URL verbatim, and the branch itself comes
 * from the deployed manifest with the upload branch behind it as fallback.
 *
 * Run: node tests/browser/audio-url.test.mjs
 */
import {
  rawDocsUrl, rawRepoUrl, audioCandidatesFor, parseManifest, parseDeployments,
  audioBranches, DEFAULT_AUDIO_BRANCHES, MANIFEST_PATH,
} from '../../docs/audio-url.js';
import { readFileSync, existsSync } from 'fs';

let pass = 0, fail = 0;
const ok = (c, m) => { c ? pass++ : (fail++, console.log('FAIL:', m)); };

// ── raw URL builder ──────────────────────────────────────────────────────
const url = rawDocsUrl('arena/x-dub', 'voice-shop/sample-001.mp3');
ok(url === 'https://raw.githubusercontent.com/zaldynbwtany202-dev/DUB-/arena/x-dub/docs/voice-shop/sample-001.mp3',
   'raw URL: owner/repo/branch/docs/path, branch verbatim');
ok(!url.includes('%2F'), 'branch slashes are NEVER encoded — %2F makes raw 404');
ok(!url.includes('github.io'), 'audio never points at the Pages origin');
ok(rawDocsUrl('b', 'a b/c.mp3').includes('a%20b/c.mp3'), 'path segments encoded, slashes kept');
ok(rawDocsUrl('b', '').endsWith('/docs/'), 'empty path → docs base');

// ── candidate list ───────────────────────────────────────────────────────
const cands = audioCandidatesFor(['a', 'b', 'a', '', '  ', 'b', 'c'], 'x.mp3');
ok(cands.length === 3, 'deduped and blank-free');
ok(cands[0].includes('/a/') && cands[2].includes('/c/'), 'order preserved');
ok(audioCandidatesFor(null, 'x').length === 0, 'null branches → no candidates');

// ── deployments parsing: the live branch discovered, never guessed ───────
ok(parseDeployments('[{"ref":"arena/new-dub"},{"ref":"arena/old-dub"},{"ref":"arena/new-dub"}]')
   .join(',') === 'arena/new-dub,arena/old-dub', 'deployment refs deduped, order kept');
ok(parseDeployments('[]').length === 0, 'empty deployments → empty list');
ok(parseDeployments('[{"sha":"x"}]').length === 0, 'refs without names dropped');
ok(parseDeployments('garbage').length === 0, 'broken JSON → empty, never a throw');
ok(parseDeployments('[{"ref":"  "}]').length === 0, 'blank refs dropped');

// ── rawRepoUrl: same verbatim rule, no /docs/ prefix ─────────────────────
ok(rawRepoUrl('arena/x-dub', 'dubs/a/b.mp4')
   === 'https://raw.githubusercontent.com/zaldynbwtany202-dev/DUB-/arena/x-dub/dubs/a/b.mp4',
   'rawRepoUrl keeps the repo root path');
ok(!rawRepoUrl('b', 'x').includes('/docs/'), 'rawRepoUrl has no docs prefix');

// ── manifest parsing: the branch comes from the file, never a guess ──────
ok(parseManifest('{"branch":"arena/new-dub"}') === 'arena/new-dub', 'valid manifest parses');
ok(parseManifest('{"branch":"  "}') === null, 'blank branch refused');
ok(parseManifest('{"nope":1}') === null, 'missing branch refused');
ok(parseManifest('not json') === null, 'broken JSON refused');
ok(parseManifest('{"branch":42}') === null, 'non-string branch refused');

// ── node (no fetch) resolves the defaults ────────────────────────────────
const list = await audioBranches();
ok(JSON.stringify(list) === JSON.stringify(DEFAULT_AUDIO_BRANCHES),
   'without fetch the default order stands: deployment first, upload second');
ok(DEFAULT_AUDIO_BRANCHES[0] === 'arena/01a08cae-dub', 'deployment branch is first');
ok(DEFAULT_AUDIO_BRANCHES[1] === 'arena/01a07c69-dub', 'upload branch is the fallback');

// ── the deployed manifest agrees with the module ─────────────────────────
ok(existsSync(new URL('../../docs/audio-branch.json', import.meta.url)),
   'audio-branch.json exists on the site');
const manifest = JSON.parse(readFileSync(new URL('../../docs/audio-branch.json', import.meta.url), 'utf8'));
ok(manifest.branch === DEFAULT_AUDIO_BRANCHES[0],
   `manifest names the deployment branch (${manifest.branch})`);
ok(Array.isArray(manifest.fallbacks) && manifest.fallbacks.includes(DEFAULT_AUDIO_BRANCHES[1]),
   'manifest records the upload-branch fallback');
ok(typeof manifest.note === 'string' && manifest.note.includes('فرع'),
   'manifest carries the maintenance note for the next session');

// ── every audio page resolves through this module ────────────────────────
for (const [file, needle] of [
  ['docs/auditions-tab.js', "from './audio-url.js'"],
  ['docs/voices.html', "from './audio-url.js'"],
  ['docs/voice-shop.html', "from './audio-url.js'"],
  ['docs/voice-library.html', "from './audio-url.js'"],
]) {
  const src = readFileSync(new URL(`../../${file}`, import.meta.url), 'utf8');
  ok(src.includes(needle), `${file} imports audio-url.js`);
}
const tab = readFileSync(new URL('../../docs/auditions-tab.js', import.meta.url), 'utf8');
ok(tab.includes('audioCandidates(s.mp3)') && tab.includes('playThrough'),
   'auditions tab plays through the candidate list');
ok(!tab.includes('sampleAudioUrl'), 'the old fixed-branch helper is gone');
ok(tab.includes('playThrough(urls, i + 1)'), 'a failed candidate advances to the next branch');

const shop = readFileSync(new URL('../../docs/voice-shop.html', import.meta.url), 'utf8');
ok(shop.includes('rawDocsUrl(next, s.preview_url)'), 'shop falls back to the next branch on error');

// ── the studio voice bank had the same %2F bug ───────────────────────────
const studio = readFileSync(new URL('../../docs/studio.js', import.meta.url), 'utf8');
ok(studio.includes('`https://raw.githubusercontent.com/${OWNER}/${REPO}/${BRANCH}/`'),
   'studio.js RAW uses the branch verbatim');
ok(!/raw\.githubusercontent\.com[^`]*encodeURIComponent/.test(studio),
   'no raw URL in studio.js encodes its branch (api.github.com decodes %2F, raw does not)');
ok(!/encodeURIComponent\(branch\)/.test(readFileSync(new URL('../../docs/audio-url.js', import.meta.url), 'utf8')),
   'no encodeURIComponent(branch) in audio-url.js');

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
