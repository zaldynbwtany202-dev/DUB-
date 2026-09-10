/**
 * Tests for the «العينات» tab inside the studio dashboard.
 *
 * The tab reuses the voice-select channel; what is new here is the grouping
 * of samples by role, the read-only card of the agent's current choice, and
 * the wiring into index.html's tab system. The helpers are pure and imported
 * straight from the module (its DOM init is guarded), and the index/studio
 * wiring is pinned by reading the sources — a tab that never opens or a
 * module that never loads would both look "fine" to a casual glance.
 *
 * Run: node tests/browser/auditions-tab.test.mjs
 */
import {
  verdict, halfStepsOff, groupSamples, picksLine,
  parseCurrentSelection, describeSelection,
} from '../../docs/auditions-tab.js';
import { readFileSync } from 'fs';

let pass = 0, fail = 0;
const ok = (c, m) => { c ? pass++ : (fail++, console.log('FAIL:', m)); };

const S = (over = {}) => ({
  id: 'R4_voice00_masri', mp3: 'samples/R4_voice00_masri.mp3',
  role: 'راغنار', voice: 'voice-00', dialect: 'مصرية',
  text: 'تقدر تعملها؟', dur: 8.0, f0: 108, cv: 0.388, target: 110, ...over,
});

// ── verdict thresholds (same numbers the samples page documents) ────────
ok(verdict(0.456)[0] === 'ok' && verdict(0.456)[1] === 'حيّ', 'CV ≥ 0.30 is حيّ');
ok(verdict(0.287)[0] === 'warn' && verdict(0.287)[1] === 'مسطّح', '0.18–0.30 is مسطّح');
ok(verdict(0.157)[1] === 'آلي', 'CV < 0.18 is آلي');
ok(verdict(0.30)[0] === 'ok' && verdict(0.18)[0] === 'warn', 'thresholds are inclusive');

// ── half steps off ───────────────────────────────────────────────────────
ok(halfStepsOff(110, 110) === '0.0', 'same pitch is zero off');
ok(halfStepsOff(110 * 2 ** (3 / 12), 110) === '3.0', 'three semitones reads 3.0');
ok(halfStepsOff(0, 110) === '—', 'missing f0 degrades to —, never NaN');

// ── grouping ─────────────────────────────────────────────────────────────
const groups = groupSamples([
  S({ role: 'راغنار', id: 'a' }), S({ role: 'فلوكي', id: 'b' }), S({ role: 'راغنار', id: 'c' }),
]);
ok(groups.length === 2, 'two roles from mixed list');
ok(groups[0].role === 'راغنار' && groups[0].samples.length === 2, 'first-seen role keeps its order');
ok(groups[1].role === 'فلوكي', 'second role follows');
ok(groupSamples([]).length === 0, 'empty input → no groups');
ok(groupSamples(null).length === 0, 'null input → no groups');

// ── picks line ───────────────────────────────────────────────────────────
ok(picksLine({}) === '', 'no picks → empty line');
const line = picksLine({
  راغنار: S(), فلوكي: S({ role: 'فلوكي', voice: 'voice-05', dialect: 'فصحى' }),
});
ok(line.includes('راغنار = voice-00 مصرية'), 'line names first pick fully');
ok(line.includes('فلوكي = voice-05 فصحى'), 'line names second pick fully');
ok(line.includes(' ، '), 'picks are joined with the Arabic comma');

// ── parsing the agent's current selection ────────────────────────────────
ok(parseCurrentSelection(404, '{"message":"Not Found"}') === null,
   '404 is "nothing sent yet", not an error');
const good = JSON.stringify({
  kind: 'voice-selection', selection_id: 'voice-sel-1', roles: [{ role: 'راغنار', voice: 'voice-00' }],
});
ok(parseCurrentSelection(200, good)?.selection_id === 'voice-sel-1', 'valid payload parses');
let threw = '';
try { parseCurrentSelection(200, 'not json'); } catch (e) { threw = e.message; }
ok(threw.includes('JSON'), 'broken JSON throws an Arabic message');
threw = '';
try { parseCurrentSelection(200, '{"kind":"other"}'); } catch (e) { threw = e.message; }
ok(threw.includes('اختياراً صالحاً'), 'wrong kind throws');
threw = '';
try { parseCurrentSelection(500, 'oops'); } catch (e) { threw = e.message; }
ok(threw.includes('500'), 'server error surfaces the status');

// ── describing the selection (the card the user reads) ───────────────────
const card = describeSelection(parseCurrentSelection(200, good));
ok(card.includes('voice-sel-1'), 'card shows the selection id');
ok(card.includes('<b>voice-00</b>'), 'card bolds the voice');
ok(card.includes('راغنار'), 'card names the role');
ok(!card.includes('ملاحظة'), 'empty note renders no note line');
const noted = describeSelection(parseCurrentSelection(200, JSON.stringify({
  kind: 'voice-selection', selection_id: 'x', created_at: '2026-09-10T19:30:00Z',
  roles: [{ role: 'راغنار', voice: 'voice-00', dialect: 'مصرية', dialect_code: 'ar-EG' }],
  note: 'صوت الراوي',
})));
ok(noted.includes('ملاحظة: صوت الراوي'), 'note renders');
ok(noted.includes('ar-EG'), 'dialect code renders');
ok(noted.includes('2026-09-10 19:30'), 'timestamp renders as date + time');
const xss = describeSelection(parseCurrentSelection(200, JSON.stringify({
  kind: 'voice-selection', selection_id: 'x', roles: [{ role: '<img src=x onerror=alert(1)>', voice: 'v' }],
})));
ok(!xss.includes('<img'), 'role text is escaped, never injected');

// ── wiring: the tab exists and the studio knows it ───────────────────────
const index = readFileSync(new URL('../../docs/index.html', import.meta.url), 'utf8');
ok(index.includes('data-tab="auditions"'), 'nav has the auditions tab');
ok(index.includes('id="tab-auditions"'), 'panel section exists');
ok(index.includes('id="countAuditions"'), 'count badge exists');
ok(index.includes('id="auditionRoles"'), 'roles container exists');
ok(index.includes('id="auditionSend"'), 'send button exists');
ok(index.includes('src="auditions-tab.js"'), 'the module is loaded by the page');
ok(index.includes('data-jump="auditions"'), 'overview has a jump shortcut');
ok(index.indexOf('src="studio.js"') < index.indexOf('src="auditions-tab.js"'),
   'studio.js loads first, so its tab handlers exist before the tab module runs');

const studio = readFileSync(new URL('../../docs/studio.js', import.meta.url), 'utf8');
ok(studio.includes("'auditions'"), 'studio.js accepts #auditions as an initial hash');

const css = readFileSync(new URL('../../docs/studio.css', import.meta.url), 'utf8');
for (const cls of ['.audition-row', '.audition-group', '.audition-current', '.audition-pick']) {
  ok(css.includes(cls), `studio.css styles ${cls}`);
}

// The module stays importable in node: the DOM init must stay guarded.
const moduleSrc = readFileSync(new URL('../../docs/auditions-tab.js', import.meta.url), 'utf8');
ok(/if \(typeof document !== 'undefined'/.test(moduleSrc),
   'DOM init is guarded so node can import the helpers');
ok(moduleSrc.includes('playThrough'), 'playback walks a candidate list');
ok(moduleSrc.includes("from './audio-url.js'"), 'the tab resolves audio via audio-url.js');

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
