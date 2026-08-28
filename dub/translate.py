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
        # deep-translator: actively maintained Google Translate client.
        # (googletrans 4.0.0-rc1 breaks with modern httpcore — do not use it.)
        from deep_translator import GoogleTranslator  # optional dependency
        tr = GoogleTranslator(source=src or "auto", target=tgt)
        return [tr.translate(t) for t in texts]

    if backend == "argos":
        import argostranslate.translate  # optional dependency
        return [argostranslate.translate.translate(t, src, tgt) for t in texts]

    raise ValueError(f"unknown translation backend: {backend}")
