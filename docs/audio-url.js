/**
 * Where the site's audio really lives.
 *
 * Two measured lessons are baked in here:
 *   1. github.io answers HTTP 500 for mp3 requests (2026-09-10) while serving
 *      the same bytes' JSON neighbours happily — so audio never loads from the
 *      Pages origin; it loads from raw.githubusercontent.com.
 *   2. A raw URL needs a branch. The branch that has every audio file is the
 *      branch Pages deploys — the session branch — and that changes between
 *      sessions, while the upload branch stays fixed. So the deployment
 *      carries docs/audio-branch.json naming its own branch (Pages serves
 *      JSON fine), every page reads that manifest first, and the fixed upload
 *      branch stays as a fallback for the legacy role samples.
 *
 * audioBranches() resolves to the ordered branch list; rawDocsUrl builds one
 * play URL. Both pure parts are unit-tested from node (no fetch there).
 */

import { OWNER, REPO } from './github-upload.js';

export const DEFAULT_AUDIO_BRANCHES = ['arena/01a08cae-dub', 'arena/01a07c69-dub'];
export const MANIFEST_PATH = 'audio-branch.json';

export function rawDocsUrl(branch, path) {
  // The branch goes in verbatim: encodeURIComponent would turn the slashes
  // inside "arena/…-dub" into %2F and raw would 404 every URL (this exact
  // bug silenced the studio's audio until 2026-09-10). The branch names come
  // from this repo's own constants and manifest — never from user input.
  return `https://raw.githubusercontent.com/${OWNER}/${REPO}/${branch}/docs/` +
    String(path || '').split('/').map(encodeURIComponent).join('/');
}

/** Ordered, deduped play URLs — best candidate first. */
export function audioCandidatesFor(branches, path) {
  const seen = new Set();
  return (branches || [])
    .filter((b) => typeof b === 'string' && b.trim() && !seen.has(b) && seen.add(b))
    .map((b) => rawDocsUrl(b, path));
}

/** {"branch": "..."} → that branch; anything else → null (never a guess). */
export function parseManifest(text) {
  try {
    const b = JSON.parse(text).branch;
    return typeof b === 'string' && b.trim() ? b.trim() : null;
  } catch {
    return null;
  }
}

/**
 * GitHub Pages deployments → the ref each one deployed, newest first.
 *
 * The manifest records the branch its own build came from, but a later
 * session deploys a NEW branch carrying the OLD manifest — the trap that
 * silenced playback once already. The deployments API is public, so the
 * pages can ask GitHub which branch is really live right now and heal
 * without anyone touching this repo. Response: [{"ref": "arena/…-dub"}, …].
 */
export function parseDeployments(text) {
  try {
    const list = JSON.parse(text);
    if (!Array.isArray(list)) return [];
    return list
      .map((d) => (d && typeof d.ref === 'string' ? d.ref.trim() : ''))
      .filter((ref, i, arr) => ref && arr.indexOf(ref) === i);
  } catch {
    return [];
  }
}

function mergeBranches(lists) {
  const seen = new Set();
  return lists.flat().filter((b) => {
    if (typeof b !== 'string' || !b.trim() || seen.has(b)) return false;
    seen.add(b);
    return true;
  });
}

let branchesPromise = null;
export function audioBranches() {
  if (typeof fetch === 'undefined') {
    return Promise.resolve(DEFAULT_AUDIO_BRANCHES);
  }
  if (!branchesPromise) {
    const deployments = fetch(
      `https://api.github.com/repos/${OWNER}/${REPO}/deployments?environment=github-pages&per_page=5`,
      { headers: { Accept: 'application/vnd.github+json' } },
    )
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((t) => parseDeployments(t))
      .catch(() => []);
    const manifest = fetch(`${MANIFEST_PATH}?ts=${Date.now()}`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((t) => parseManifest(t))
      .then((b) => (b ? [b] : []))
      .catch(() => []);
    branchesPromise = Promise.all([deployments, manifest])
      .then(([deployed, fromManifest]) =>
        mergeBranches([deployed, fromManifest, DEFAULT_AUDIO_BRANCHES]))
      .catch(() => DEFAULT_AUDIO_BRANCHES);
  }
  return branchesPromise;
}

/** Absolute raw URL for any repo path (no /docs/ prefix) — dashboards, dubs. */
export function rawRepoUrl(branch, path) {
  return `https://raw.githubusercontent.com/${OWNER}/${REPO}/${branch}/` +
    String(path || '').split('/').map(encodeURIComponent).join('/');
}

export function audioCandidates(path) {
  return audioBranches().then((list) => audioCandidatesFor(list, path));
}
