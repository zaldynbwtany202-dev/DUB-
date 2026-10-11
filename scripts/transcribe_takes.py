#!/usr/bin/env python3
"""Whisper cache tied to the actual MP3 bytes, not merely the gNNN filename.

A pronunciation fix previously replaced g000.mp3 while g000.json still said the
old word. Keep that old report, transcribe the new take and embed its SHA-256.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / '.cache/tools/whisper.cpp/build/bin/whisper-cli'
MODEL = ROOT / '.cache/models/whisper-ggml/ggml-small.bin'


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as handle:
        while data := handle.read(1 << 20):
            h.update(data)
    return h.hexdigest()


def source_matches(report: Path, take: Path) -> bool:
    """Legacy reports without a recorded source hash are NOT evidence of current audio."""
    if not report.is_file() or not take.is_file():
        return False
    try:
        data = json.loads(report.read_text(encoding='utf-8'))
        return (data.get('_provenance', {}).get('take_sha256') == sha256(take)
                and bool(data.get('transcription')))
    except (ValueError, OSError, TypeError):
        return False


def transcribe(take: Path, report: Path) -> str:
    if not take.is_file():
        raise FileNotFoundError(take)
    digest = sha256(take)
    if source_matches(report, take):
        return 'up-to-date'
    if not CLI.is_file() or not MODEL.is_file():
        raise FileNotFoundError('Whisper binary/model missing; run scripts/rebuild_env.sh')
    report.parent.mkdir(parents=True, exist_ok=True)
    # Whisper emits <prefix>.json. Build out of view and replace only on success.
    with tempfile.TemporaryDirectory(prefix='asr-', dir=report.parent) as folder:
        prefix = Path(folder) / 'result'
        subprocess.run([str(CLI), '-m', str(MODEL), '-f', str(take),
                        '-oj', '-of', str(prefix), '-l', 'ar'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        doc = json.loads(prefix.with_suffix('.json').read_text(encoding='utf-8'))
        if not doc.get('transcription'):
            raise ValueError(f'Empty ASR output for {take}')
        if sha256(take) != digest:  # avoid tagging an ASR for audio changed mid-run
            raise RuntimeError(f'Audio changed while transcribing {take}')
        doc['_provenance'] = {'take_sha256': digest, 'take_file': take.name,
                              'model_file': MODEL.name, 'language': 'ar'}
        # Keep ALL previous reports, including unverified legacy reports.
        if report.exists():
            old_hash = sha256(report)[:16]
            archive = report.parent.parent / 'asr-takes-superseded'
            archive.mkdir(parents=True, exist_ok=True)
            old = archive / f'{report.stem}.{old_hash}.json'
            if not old.exists():
                shutil.copy2(report, old)
        tmp = Path(folder) / 'checked.json'
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        os.replace(tmp, report)
    return 'refreshed'
