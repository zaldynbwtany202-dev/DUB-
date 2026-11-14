"""Studio voice bank with pronunciation-safe lookup.

Version-one index keys ignored Arabic vowel marks and folded distinct letters,
which could make homographs share an entry. New entries use an exact-text,
voice-scoped v2 key. Legacy entries remain readable only when their stored text
and requested voice match exactly after whitespace normalization.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import unicodedata
from pathlib import Path
from typing import Optional

from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe
from youtube_auto_dub.models import SR_TTS

ROOT = Path(__file__).resolve().parent.parent
VOICE_DIR = ROOT / "samples" / "voices"
INDEX_PATH = VOICE_DIR / "index.json"

# Kept only to calculate v1 lookup keys. Never use this lossy form as proof
# that two scripts have the same pronunciation.
_LEGACY_DIACRITICS = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06dc\u0640]")
_LEGACY_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Return the conservative v2 identity text; preserve marks and letters.

    Only Unicode compatibility composition and whitespace are normalized.
    Punctuation, Arabic harakat, alef variants, ta marbuta and yaa maqsura
    remain distinct because they can affect wording or spoken delivery.
    """
    text = unicodedata.normalize("NFKC", text or "")
    return _SPACES.sub(" ", text).strip()


def key_for(text: str, voice: str | None = None) -> str:
    """Return a versioned SHA-256 key for exact text and optional voice ID."""
    payload = f"v2\0{normalize(text)}\0{(voice or '').strip()}".encode("utf-8")
    return "v2-" + hashlib.sha256(payload).hexdigest()


def _legacy_normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = _LEGACY_DIACRITICS.sub("", text)
    for src, dst in (("أإآٱ", "ا"), ("ى", "ي"), ("ة", "ه")):
        text = text.translate({ord(char): dst for char in src})
    text = _LEGACY_PUNCT.sub(" ", text)
    return _SPACES.sub(" ", text).strip().lower()


def _legacy_key_for(text: str) -> str:
    return hashlib.sha1(_legacy_normalize(text).encode("utf-8")).hexdigest()[:16]


def load_index() -> dict:
    if INDEX_PATH.exists():
        try:
            value = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_index(index: dict) -> None:
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    temp = INDEX_PATH.with_name(f".{INDEX_PATH.name}.tmp")
    temp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(INDEX_PATH)


def _text_matches(entry: dict, text: str) -> bool:
    stored = entry.get("text")
    return isinstance(stored, str) and normalize(stored) == normalize(text)


def _voice_matches(entry: dict, voice: str | None) -> bool:
    # If a caller pins a voice, an old/unknown voice must not silently pass.
    return voice is None or (isinstance(entry.get("voice"), str) and entry["voice"] == voice)


def _safe_take_path(entry: dict) -> Path | None:
    filename = entry.get("file")
    if not isinstance(filename, str) or not filename:
        return None
    try:
        root = VOICE_DIR.resolve()
        path = (root / filename).resolve()
        path.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    return path if path.is_file() else None


def lookup_entry(text: str, voice: str | None = None) -> dict | None:
    """Find an exact-text take, optionally pinned to the requested voice.

    Legacy keys are still honored for existing assets, but their lossy hash is
    only an index hint: the original text and voice are checked before reuse.
    """
    index = load_index()
    if not isinstance(index, dict):
        return None

    candidates: list[tuple[str, dict]] = []
    direct = index.get(key_for(text, voice))
    if isinstance(direct, dict):
        candidates.append((key_for(text, voice), direct))

    # A voice-less query is useful for coverage reports and direct studio use;
    # sort keys to keep selection deterministic if several exact voices exist.
    if voice is None:
        candidates.extend(sorted(
            ((key, entry) for key, entry in index.items()
             if isinstance(key, str) and key.startswith("v2-") and isinstance(entry, dict)
             and key != key_for(text, None)),
            key=lambda item: item[0],
        ))

    legacy_key = _legacy_key_for(text)
    legacy = index.get(legacy_key)
    if isinstance(legacy, dict):
        candidates.append((legacy_key, legacy))

    seen: set[str] = set()
    for key, entry in candidates:
        if key in seen:
            continue
        seen.add(key)
        if not _text_matches(entry, text) or not _voice_matches(entry, voice):
            continue
        path = _safe_take_path(entry)
        if path is not None:
            return {**entry, "path": path}
    return None


def register(text: str, audio: Path, voice: str = "studio") -> str:
    """Record an approved take without overwriting any legacy entry."""
    index = load_index()
    entry_key = key_for(text, voice)
    index[entry_key] = {
        "text": text,
        "file": Path(audio).name,
        "voice": voice,
        "key_version": 2,
    }
    save_index(index)
    return entry_key


def lookup(text: str, voice: str | None = None) -> Optional[Path]:
    """Return an exact-text, matching-voice approved take if one exists."""
    entry = lookup_entry(text, voice)
    return entry["path"] if entry else None


def speak_studio(text: str, dest: Path, voice: str | None = None) -> bool:
    """Render an approved bank take matching the requested text and voice."""
    source = lookup(text, voice)
    if not source:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg_exe(), "-y", "-i", str(source),
         "-ar", str(SR_TTS), "-ac", "1", str(dest)],
        check=True, capture_output=True,
    )
    return dest.exists() and dest.stat().st_size > 0


def coverage(lines: list[str], voice: str | None = None) -> tuple[int, int]:
    """Count exact lines with an approved take (optionally for one voice)."""
    have = sum(1 for line in lines if lookup(line, voice))
    return have, len(lines)
