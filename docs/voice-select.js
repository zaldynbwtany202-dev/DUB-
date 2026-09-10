/**
 * Send a voice choice from the page to the agent — through the repository itself.
 *
 * The agent cannot see this page or hear its buttons. The one channel that
 * already works in both directions is the repository: send.html commits files
 * from the browser straight to the Sendbox branch, and the agent reads them
 * back with `gh api` (scripts/fetch_inbox.py). A voice choice is just a small
 * JSON file committed the same way to one well-known path, so the page needs
 * no server and the agent needs no new transport.
 *
 * Contract (pinned by tests/test_voice_selection.py):
 *   branch: arena/01a07c69-dub   (the same Sendbox branch uploads use)
 *   path:   inbox/voice-selection.json
 *   reader: scripts/fetch_voice_selection.py
 *
 * Overwriting the file is how the choice changes; the old choice survives in
 * the branch history like any other commit.
 */

import { OWNER, REPO } from './github-upload.js';

const API = 'https://api.github.com';

export const SELECTION_BRANCH = 'arena/01a07c69-dub';
export const SELECTION_PATH = 'inbox/voice-selection.json';

// Dialect names shown on the page → BCP-47 hint. Advisory only: the agent
// weighs the sample, the dialect text and the note together; this tag is a
// starting point, not a decision.
const DIALECT_CODES = {
  'فصحى': 'ar',
  'مصرية': 'ar-EG',
  'شامية': 'ar-Levantine',
};

export function dialectCode(name) {
  return DIALECT_CODES[name] || '';
}

/**
 * Shape the page's picks into the payload the reader expects.
 *
 * `chosen` is { role: sample } exactly as voices.html collects it. `now` is
 * injectable so tests pin the id format instead of racing the clock.
 */
export function buildSelection(chosen, { note = '', page = 'voices.html', now = new Date() } = {}) {
  const pad = (n) => String(n).padStart(2, '0');
  const stamp = `${now.getUTCFullYear()}${pad(now.getUTCMonth() + 1)}${pad(now.getUTCDate())}` +
                `-${pad(now.getUTCHours())}${pad(now.getUTCMinutes())}${pad(now.getUTCSeconds())}`;

  const roles = Object.entries(chosen || {}).map(([role, s]) => ({
    role,
    voice: s.voice,
    dialect: s.dialect,
    dialect_code: dialectCode(s.dialect),
    sample_id: s.id,
    sample: s.mp3,
    f0: s.f0,
    cv: s.cv,
    target_f0: s.target,
    phrase: s.text,
  }));

  const selection = {
    kind: 'voice-selection',
    selection_id: `voice-sel-${stamp}`,
    created_at: now.toISOString(),
    page,
    roles,
    note: String(note || '').trim(),
  };
  if (typeof location !== 'undefined') {
    selection.page_origin = location.origin;
  }
  return selection;
}

/** One-line Arabic commit message naming the voices, so `git log` reads it. */
export function commitMessage(selection) {
  const parts = (selection.roles || []).map(
    (r) => `${r.role}=${r.voice} ${r.dialect}`,
  );
  const head = parts.join(' ، ') || 'بدون أصوات';
  const note = selection.note ? ` — ${selection.note.slice(0, 60)}` : '';
  return `اختيار صوت من الصفحة: ${head}${note}`;
}

// ── GitHub plumbing ─────────────────────────────────────────────────────
// Same shape as github-upload.js: a blob, a tree on top of the current head,
// one commit, one ref move. A selection is tiny, so there is no part-splitting
// and no retry ladder — GitHub either accepts it or says why not.

async function api(path, token, options = {}) {
  const res = await fetch(`${API}${path}`, {
    ...options,
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...options.headers,
    },
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body.message) detail = body.message;
    } catch { /* keep the status */ }
    if (res.status === 401) detail = 'الرمز غير صالح أو منتهي الصلاحية';
    if (res.status === 403) detail = 'الرمز لا يملك صلاحية الكتابة — تأكد من تفعيل public_repo';
    if (res.status === 404) detail = 'تعذّر الوصول للمستودع أو الفرع — غالباً الرمز بلا صلاحية public_repo';
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

/**
 * Commit the selection JSON to the Sendbox branch in one commit.
 * Returns where the agent will read it from, so the page can show the proof.
 */
export async function commitSelection(selection, token) {
  const body = JSON.stringify(selection, null, 2);
  const { sha } = await api(`/repos/${OWNER}/${REPO}/git/blobs`, token, {
    method: 'POST',
    body: JSON.stringify({ encoding: 'utf-8', content: body }),
  });

  const ref = await api(`/repos/${OWNER}/${REPO}/git/ref/heads/${SELECTION_BRANCH}`, token);
  const head = ref.object.sha;
  const parent = await api(`/repos/${OWNER}/${REPO}/git/commits/${head}`, token);

  const tree = await api(`/repos/${OWNER}/${REPO}/git/trees`, token, {
    method: 'POST',
    body: JSON.stringify({
      base_tree: parent.tree.sha,
      tree: [{ path: SELECTION_PATH, mode: '100644', type: 'blob', sha }],
    }),
  });

  const commit = await api(`/repos/${OWNER}/${REPO}/git/commits`, token, {
    method: 'POST',
    body: JSON.stringify({
      message: commitMessage(selection),
      tree: tree.sha,
      parents: [head],
    }),
  });

  await api(`/repos/${OWNER}/${REPO}/git/refs/heads/${SELECTION_BRANCH}`, token, {
    method: 'PATCH',
    body: JSON.stringify({ sha: commit.sha }),
  });

  return {
    commit: commit.sha,
    url: `https://github.com/${OWNER}/${REPO}/commit/${commit.sha}`,
    branch: SELECTION_BRANCH,
    path: SELECTION_PATH,
  };
}
