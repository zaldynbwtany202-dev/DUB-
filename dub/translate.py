"""Translation backends.

Backends:
  - google : googletrans (free, online, no key)
  - argos  : argostranslate (offline, open source)
  - file   : human translations from a JSON file {"0": "...", "1": "..."}
"""
from __future__ import annotations

import json
from pathlib import Path


def translate_batch(texts: list[str], src: str, tgt: str,
                    backend: str = "google",
                    translations_file: str | None = None) -> list[str]:
    if backend == "file":
        if not translations_file:
            raise ValueError("backend 'file' requires --translations-file")
        data = json.loads(Path(translations_file).read_text(encoding="utf-8"))
        return [data.get(str(i), t) for i, t in enumerate(texts)]

    if backend == "google":
        from googletrans import Translator  # optional dependency
        tr = Translator()
        # empty src means "auto-detect"
        results = tr.translate(texts, src=src or "auto", dest=tgt)
        return [r.text for r in results]

    if backend == "argos":
        import argostranslate.translate  # optional dependency
        return [argostranslate.translate.translate(t, src, tgt) for t in texts]

    raise ValueError(f"unknown translation backend: {backend}")
