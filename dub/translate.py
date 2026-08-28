"""Translation backends.

Backends:
  - google : Google Translate via deep-translator, with automatic fallback
             to MyMemory when Google blocks datacenter IPs (e.g. CI runners)
  - mymemory: MyMemory via deep-translator (free, no key)
  - argos  : argostranslate (offline, open source)
  - file   : human translations from a JSON file {"0": "...", "1": "..."}
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger("dub")


def _norm(s: str) -> str:
    """Normalize for fuzzy matching: lowercase, strip punctuation, collapse spaces."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", s.lower())).strip()


def _from_file(texts: list[str], path: str) -> list[str]:
    """Human translations from JSON.

    Two forms are accepted:
      {"0": "...", "1": "..."}                — keyed by segment index
      {"source sentence": "translation"}      — keyed by source text (robust
        to segmentation differences; merged segments containing several keys
        get the joined translations)
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if all(k.isdigit() for k in data):
        return [data.get(str(i), t) for i, t in enumerate(texts)]

    lut = {_norm(k): v for k, v in data.items()}
    out = []
    for i, t in enumerate(texts):
        key = _norm(t)
        if key in lut:
            out.append(lut[key])
            continue
        # merged segment: join the translations of every key it contains,
        # in order of appearance
        hits = sorted(((key.find(k), v) for k, v in lut.items()
                       if k and k in key), key=lambda x: x[0])
        hits = [v for pos, v in hits if pos >= 0]
        if hits:
            out.append(" ".join(hits))
        else:
            log.warning("no human translation for segment %d — keeping source: %s", i, t)
            out.append(t)
    return out

# MyMemory wants full locale codes for some languages
_LOCALE = {"en": "en-US", "ar": "ar-SA", "fr": "fr-FR", "de": "de-DE",
           "es": "es-ES", "tr": "tr-TR", "hi": "hi-IN", "ur": "ur-PK"}


def _google(texts: list[str], src: str, tgt: str) -> list[str]:
    from deep_translator import GoogleTranslator  # optional dependency
    tr = GoogleTranslator(source=src or "auto", target=tgt)
    return [tr.translate(t) for t in texts]


def _mymemory(texts: list[str], src: str, tgt: str) -> list[str]:
    import time
    from deep_translator import MyMemoryTranslator  # optional dependency
    from deep_translator.exceptions import TooManyRequests
    tr = MyMemoryTranslator(source=_LOCALE.get(src, src or "en-US"),
                            target=_LOCALE.get(tgt, tgt))
    out = []
    for t in texts:
        for attempt in (1, 2, 3):
            try:
                out.append(tr.translate(t))
                break
            except TooManyRequests:
                if attempt == 3:
                    raise
                time.sleep(5 * attempt)  # datacenter IPs get throttled — back off
        time.sleep(1.2)  # stay under the free-tier rate limit
    return out


def translate_batch(texts: list[str], src: str, tgt: str,
                    backend: str = "google",
                    translations_file: str | None = None) -> list[str]:
    if backend == "file":
        if not translations_file:
            raise ValueError("backend 'file' requires --translations-file")
        return _from_file(texts, translations_file)

    if backend == "google":
        try:
            return _google(texts, src, tgt)
        except Exception as exc:  # Google often blocks datacenter IPs
            log.warning("google translate failed (%s) — falling back to mymemory", exc)
            try:
                return _mymemory(texts, src, tgt)
            except Exception as exc2:
                raise RuntimeError(
                    "all free online translators failed from this network "
                    f"(google: {exc}; mymemory: {exc2}). "
                    "Use --translate-backend file --translations-file lines.json "
                    "with your own translations, or --translate-backend argos "
                    "for fully offline translation."
                ) from exc2

    if backend == "mymemory":
        return _mymemory(texts, src, tgt)

    if backend == "argos":
        import argostranslate.translate  # optional dependency
        return [argostranslate.translate.translate(t, src, tgt) for t in texts]

    raise ValueError(f"unknown translation backend: {backend}")
