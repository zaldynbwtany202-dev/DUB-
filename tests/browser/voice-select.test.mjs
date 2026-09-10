/**
 * Tests for the voice-choice payload (docs/voice-select.js).
 *
 * The send path itself hits the network and is verified end to end by the
 * arrival half (scripts/fetch_voice_selection.py + a real commit). What is
 * pinned here is everything that can be wrong before any network call: the
 * payload shape, the id format the agent greps for, the dialect map, and the
 * branch/path contract with the reader.
 *
 * Run: node tests/browser/voice-select.test.mjs
 */
import {
  buildSelection, commitMessage, dialectCode,
  SELECTION_BRANCH, SELECTION_PATH,
} from '../../docs/voice-select.js';
import { BRANCH as UPLOAD_BRANCH, OWNER, REPO } from '../../docs/github-upload.js';

let pass = 0, fail = 0;
const ok = (c, m) => { c ? pass++ : (fail++, console.log('FAIL:', m)); };

const SAMPLE = {
  id: 'R4_voice00_masri', mp3: 'samples/R4_voice00_masri.mp3',
  role: 'راغنار', voice: 'voice-00', dialect: 'مصرية',
  text: 'جملة الاختبار', dur: 8.0, f0: 108, cv: 0.388, target: 110,
};
const NOW = new Date('2026-09-10T12:34:56Z');

// ── constants: writer and reader must agree ─────────────────────────────
ok(SELECTION_PATH === 'inbox/voice-selection.json', 'path is the reader path');
ok(SELECTION_BRANCH === UPLOAD_BRANCH,
   'selection rides the same Sendbox branch as uploads');
ok(SELECTION_BRANCH === 'arena/01a07c69-dub', 'branch is the Sendbox branch');
ok(OWNER === 'zaldynbwtany202-dev' && REPO === 'DUB-',
   'fallback repo matches this repository');

// ── dialect map ──────────────────────────────────────────────────────────
ok(dialectCode('مصرية') === 'ar-EG', 'Egyptian maps to ar-EG');
ok(dialectCode('فصحى') === 'ar', 'MSA maps to ar');
ok(dialectCode('شامية') === 'ar-Levantine', 'Shami maps to the advisory tag');
ok(dialectCode('لغة غير معروفة') === '', 'unknown dialect degrades to empty, never a guess');

// ── buildSelection shape ────────────────────────────────────────────────
const sel = buildSelection({ 'راغنار': SAMPLE }, { note: '  صوت الراوي  ', now: NOW });

ok(sel.kind === 'voice-selection', 'kind names the payload type');
ok(sel.selection_id === 'voice-sel-20260910-123456',
   `id comes from the UTC stamp, got ${sel.selection_id}`);
ok(sel.created_at === '2026-09-10T12:34:56.000Z', 'created_at is ISO from the same clock');
ok(sel.note === 'صوت الراوي', 'note is trimmed');

ok(Array.isArray(sel.roles) && sel.roles.length === 1, 'one entry per chosen role');
const r = sel.roles[0];
ok(r.role === 'راغنار', 'role survives');
ok(r.voice === 'voice-00', 'voice id survives');
ok(r.dialect === 'مصرية', 'dialect text survives');
ok(r.dialect_code === 'ar-EG', 'dialect code is attached');
ok(r.sample_id === 'R4_voice00_masri', 'sample id survives');
ok(r.sample === 'samples/R4_voice00_masri.mp3', 'sample path survives');
ok(r.f0 === 108 && r.cv === 0.388 && r.target_f0 === 110,
   'measured pitch facts survive');
ok(r.phrase === 'جملة الاختبار', 'the spoken phrase survives');

// The reader (fetch_voice_selection.py) refuses a payload without these.
for (const field of ['kind', 'selection_id', 'created_at', 'roles']) {
  ok(sel[field] !== undefined && sel[field] !== '', `payload carries ${field}`);
}

// Multiple roles keep their own entries — the agent reads one line per role.
const two = buildSelection({
  'راغنار': SAMPLE,
  'فلوكي': { ...SAMPLE, role: 'فلوكي', id: 'F2_voice00_masri', voice: 'voice-05' },
}, { now: NOW });
ok(two.roles.length === 2, 'both roles are carried');
ok(two.roles[1].voice === 'voice-05', 'second role keeps its own voice');

// An empty pick still produces a well-typed payload; the page disables the
// send button before this can be committed, but the module must not crash.
const none = buildSelection({}, { now: NOW });
ok(none.roles.length === 0, 'no picks → empty roles array, no crash');

// ── commit message ───────────────────────────────────────────────────────
const msg = commitMessage(sel);
ok(msg.includes('voice-00') && msg.includes('مصرية'), 'message names voice and dialect');
ok(msg.includes('راغنار'), 'message names the role');
ok(commitMessage(none).includes('بدون أصوات'), 'empty pick still yields a message');
ok(commitMessage({ ...sel, note: 'ملاحظة' }).includes('ملاحظة'), 'note reaches the message');

// ── id uniqueness across a whole day of picking ──────────────────────────
const ids = new Set();
for (let s = 0; s < 2; s++) {
  for (let m = 0; m < 60; m++) {
    const d = new Date(Date.UTC(2026, 8, 10, s, m, 30));
    ids.add(buildSelection({}, { now: d }).selection_id);
  }
}
ok(ids.size === 120, 'id changes every second — no silent overwrite');

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
