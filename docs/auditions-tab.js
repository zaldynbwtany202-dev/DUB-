/**
 * The «العينات» tab of the studio (docs/index.html): listen to the real
 * audition samples, pick one voice per role, and commit the pick for the
 * agent — the same channel voices.html uses (docs/voice-select.js), now
 * living inside the dashboard.
 *
 * The token is the studio's own: loadToken() reads the same localStorage key
 * the Settings tab saves, so a user connected once is connected everywhere.
 *
 * The pure helpers (verdict, groupSamples, picksLine, parseCurrentSelection,
 * describeSelection) are exported and unit-tested from node; every DOM access
 * sits inside initAuditions(), which only runs in a browser that has the tab.
 */

import {
  buildSelection, commitSelection, SELECTION_BRANCH, SELECTION_PATH,
} from './voice-select.js';
import { OWNER, REPO, loadToken } from './github-upload.js';
import { audioCandidates } from './audio-url.js';

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));



// ── pure helpers ─────────────────────────────────────────────────────────

/** Pitch-variety verdict, same thresholds the samples page documents. */
export function verdict(cv) {
  if (cv >= 0.30) return ['ok', 'حيّ'];
  if (cv >= 0.18) return ['warn', 'مسطّح'];
  return ['', 'آلي'];
}

/** Distance from the actor's reference pitch, in half steps. */
export function halfStepsOff(f0, target) {
  if (!f0 || !target) return '—';
  return Math.abs(12 * Math.log2(f0 / target)).toFixed(1);
}

/** Group samples by role, keeping the order roles first appear in. */
export function groupSamples(samples) {
  const roles = [];
  const byRole = new Map();
  for (const s of samples || []) {
    if (!byRole.has(s.role)) {
      byRole.set(s.role, []);
      roles.push(s.role);
    }
    byRole.get(s.role).push(s);
  }
  return roles.map((role) => ({ role, samples: byRole.get(role) }));
}

/** The one-line summary of the current picks, in selection order. */
export function picksLine(chosen) {
  return Object.values(chosen || {})
    .map((s) => `${s.role} = ${s.voice} ${s.dialect}`)
    .join(' ، ');
}

/**
 * Read the agent's current selection from a contents-API response.
 * 404 means nothing was sent yet (not an error); anything unparsable throws
 * with an Arabic message instead of rendering a broken card.
 */
export function parseCurrentSelection(status, text) {
  if (status === 404) return null;
  if (status !== 200) throw new Error(`تعذّر قراءة الاختيار الحالي (HTTP ${status})`);
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error('ملف الاختيار الموجود ليس JSON صالحاً');
  }
  if (!data || data.kind !== 'voice-selection' || !Array.isArray(data.roles)) {
    throw new Error('الملف الموجود ليس اختياراً صالحاً');
  }
  return data;
}

/** Arabic card body describing what the agent currently knows. */
export function describeSelection(data) {
  if (!data) return '';
  const when = String(data.created_at || '').slice(0, 16).replace('T', ' ');
  const parts = [`المعرف <span class="mono">${esc(data.selection_id || '؟')}</span>`];
  if (when) parts.push(`أُرسل ${esc(when)}`);
  const lines = [`<div>${parts.join(' · ')}</div>`];
  for (const r of data.roles || []) {
    const code = r.dialect_code ? ` (${esc(r.dialect_code)})` : '';
    lines.push(`<div>• ${esc(r.role)} = <b>${esc(r.voice)}</b> · ${esc(r.dialect)}${code}</div>`);
  }
  const note = String(data.note || '').trim();
  if (note) lines.push(`<div>ملاحظة: ${esc(note)}</div>`);
  return lines.join('');
}

// ── browser wiring ───────────────────────────────────────────────────────

function initAuditions() {
  const rolesBox = $('auditionRoles');
  const answer = $('auditionAnswer');
  const hint = $('auditionHint');
  const status = $('auditionStatus');
  const sendBtn = $('auditionSend');
  const noteInput = $('auditionNote');
  const say = (msg, kind = 'info') => { status.textContent = msg; status.className = `status ${kind}`; };

  const player = new Audio();
  let playingBtn = null;
  const chosen = {};
  player.onerror = () => {
    say('تعذّر تحميل الملف الصوتي — تحقق من الاتصال ثم اضغط ▶ من جديد.', 'err');
  };

  const stop = () => {
    player.pause();
    if (playingBtn) { playingBtn.textContent = '▶'; playingBtn = null; }
  };
  player.onended = stop;

  const setPickState = () => {
    const line = picksLine(chosen);
    answer.textContent = line || '—';
    const count = Object.keys(chosen).length;
    hint.textContent = count === 0
      ? 'لم تختر بعد — اضغط «اختر» عند الصوت الذي يعجبك لكل شخصية.'
      : 'اختيارك جاهز — عدّله متى شئت وأعد الإرسال؛ الكوميت الجديد يستبدل القديم.';
    sendBtn.disabled = count === 0;
  };

  const render = (samples) => {
    $('countAuditions').textContent = String(samples.length);
    rolesBox.innerHTML = '';
    for (const group of groupSamples(samples)) {
      const box = document.createElement('div');
      box.className = 'audition-group';
      const target = group.samples[0]?.target;
      box.innerHTML = `<h3>${esc(group.role)} <small>${target ? `نبرة الممثل الأصلي ${target} هرتز` : ''}</small></h3>`;
      for (const s of group.samples) {
        const [cls, word] = verdict(s.cv);
        const row = document.createElement('div');
        row.className = 'audition-row';
        row.innerHTML = `
          <button type="button" class="play" aria-label="تشغيل">▶</button>
          <div class="info">
            <div class="name">${esc(s.voice)} · ${esc(s.dialect)}</div>
            <div class="say">${esc(s.text)}</div>
            <div class="chips">
              <span class="tag ${cls}">CV ${s.cv} — ${word}</span>
              <span class="tag">${s.f0} هرتز</span>
              <span class="tag">فرق ${halfStepsOff(s.f0, s.target)} نصف نغمة</span>
              <span class="tag">${s.dur} ث</span>
            </div>
          </div>
          <button type="button" class="btn small pick">اختر</button>`;
        const play = row.querySelector('.play');
        play.onclick = () => {
          if (playingBtn === play) { stop(); return; }
          stop();
          // Branch comes from the deployed manifest (audio-branch.json); on
          // error the next candidate branch is tried before giving up.
          audioCandidates(s.mp3).then((urls) => playThrough(urls));
        };
        const playThrough = (urls, i = 0) => {
          if (i >= urls.length) {
            say('تعذّر تحميل الملف الصوتي من كل الفروع — تحقق من الاتصال ثم أعد المحاولة.', 'err');
            play.textContent = '⚠';
            return;
          }
          let moved = false;
          const onErr = () => {
            if (moved) return;
            moved = true;
            player.removeEventListener('error', onErr);
            playThrough(urls, i + 1);
          };
          player.addEventListener('error', onErr);
          player.src = urls[i];
          player.play().then(() => { playingBtn = play; play.textContent = '⏸'; })
                       .catch(() => { /* a refused play keeps the error path in charge */ });
        };
        row.querySelector('.pick').onclick = () => {
          rolesBox.querySelectorAll(`.audition-row[data-role="${CSS.escape(s.role)}"]`)
            .forEach((r) => r.classList.remove('on'));
          row.classList.add('on');
          chosen[s.role] = s;
          setPickState();
        };
        row.dataset.role = s.role;
        box.appendChild(row);
      }
      rolesBox.appendChild(box);
    }
    setPickState();
  };

  // What the agent currently knows — read even without a token (public repo),
  // so the card works in read-only mode like the rest of the studio.
  const refreshCurrent = async () => {
    const box = $('auditionCurrent');
    box.textContent = 'جارٍ الفحص…';
    try {
      const token = loadToken();
      const headers = { Accept: 'application/vnd.github.raw' };
      if (token) headers.Authorization = `Bearer ${token}`;
      const res = await fetch(
        `https://api.github.com/repos/${OWNER}/${REPO}/contents/${SELECTION_PATH}?ref=${encodeURIComponent(SELECTION_BRANCH)}`,
        { headers },
      );
      const data = parseCurrentSelection(res.status, await res.text());
      box.innerHTML = data
        ? describeSelection(data)
        : 'لا يوجد اختيار بعد — اسمع العينات أدناه، اختر، وأرسل؛ سيظهر الاختيار هنا فوراً.';
    } catch (err) {
      box.textContent = err.message;
    }
  };
  $('auditionRefreshCurrent').onclick = refreshCurrent;

  sendBtn.onclick = async () => {
    const token = loadToken();
    if (!token) {
      say('أضف رمز GitHub من تبويب «الإعدادات» أولاً — الإرسال يثبّت اختيارك كوميتاً في المستودع.', 'err');
      document.querySelector('.tab[data-tab="settings"]')?.click();
      return;
    }
    const selection = buildSelection(chosen, { note: noteInput.value, page: 'index.html#auditions' });
    sendBtn.disabled = true;
    say('يثبّت الاختيار في المستودع…');
    try {
      const r = await commitSelection(selection, token);
      say(`وصل اختيارك إلى GitHub ✓ — ${r.commit.slice(0, 7)}. اكتب لي في الشات: «اخترت من الصفحة».`, 'ok');
      await refreshCurrent();
    } catch (err) {
      say(`فشل الإرسال: ${err.message}`, 'err');
    } finally {
      setPickState();
    }
  };

  fetch('samples.json')
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
    .then((d) => render(d.samples || []))
    .catch(() => { rolesBox.innerHTML = '<div class="empty">تعذّر تحميل العينات.</div>'; });

  refreshCurrent();
}

if (typeof document !== 'undefined' && document.getElementById('auditionRoles')) {
  initAuditions();
}
