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

let branchesPromise = null;
export function audioBranches() {
  if (typeof fetch === 'undefined') {
    return Promise.resolve(DEFAULT_AUDIO_BRANCHES);
  }
  if (!branchesPromise) {
    branchesPromise = fetch(`${MANIFEST_PATH}?ts=${Date.now()}`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((t) => parseManifest(t))
      .then((b) => (b ? [b, ...DEFAULT_AUDIO_BRANCHES] : DEFAULT_AUDIO_BRANCHES))
      .catch(() => DEFAULT_AUDIO_BRANCHES);
  }
  return branchesPromise;
}

export function audioCandidates(path) {
  return audioBranches().then((list) => audioCandidatesFor(list, path));
}
