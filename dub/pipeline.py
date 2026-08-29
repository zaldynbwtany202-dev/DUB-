"""End-to-end dubbing pipeline with resumable stages.

Stages (each cached in the workdir; re-running resumes where it stopped):
  1. extract     audio from the source video
  2. separate    vocals / background stems (optional)
  3. transcribe  word-level transcript -> dialogue segments
  4. speakers    per-speaker clean reference samples
  5. translate   segments to the target language
  6. synth       voice-cloned TTS per line (+ one faster re-synth pass)
  7. render      sync, mix with background, mux onto video
"""
from __future__ import annotations

import logging
from pathlib import Path

from . import media, mix, separate, sync, transcribe, translate
from .models import Project
from .tts import get_engine

log = logging.getLogger("dub")


class DubPipeline:
    def __init__(self, project: Project, workdir: str | Path,
                 tts_engine: str = "xtts",
                 max_stretch: float = 1.25,
                 bg_volume: float = 0.5,
                 do_separate: bool = True,
                 translate_backend: str = "google",
                 translations_file: str | None = None,
                 speaker_map: dict[str, str] | None = None,
                 whisper_model: str = "small",
                 emotion: str = "neutral"):
        self.p = project
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.tts_engine_name = tts_engine
        self.max_stretch = max_stretch
        self.bg_volume = bg_volume
        self.do_separate = do_separate
        self.translate_backend = translate_backend
        self.translations_file = translations_file
        self.speaker_map = speaker_map or {}
        self.whisper_model = whisper_model
        self.emotion = emotion

    @property
    def state_file(self) -> Path:
        return self.workdir / "project.json"

    def _save(self) -> None:
        self.p.save(self.state_file)

    # ---------------- stages ----------------

    def stage_extract(self) -> None:
        audio = self.workdir / "source_audio.wav"
        if not audio.exists():
            log.info("extracting audio …")
            media.extract_audio(self.p.src_video, audio)
        self.p.duration = media.probe_duration(self.p.src_video)
        self._audio = audio
        self._save()

    def stage_separate(self) -> None:
        if not self.do_separate:
            bg = self.workdir / "background.wav"
            if not bg.exists():
                log.info("separation disabled — ducking original mix as background")
                separate.duck_original(self._audio, bg)
            self.p.vocals_path = str(self._audio)
            self.p.background_path = str(bg)
            self._save()
            return
        if not (self.p.vocals_path and self.p.background_path):
            log.info("separating vocals / background …")
            v, b = separate.separate(self._audio, self.workdir)
            self.p.vocals_path, self.p.background_path = str(v), str(b)
            self._save()

    def stage_transcribe(self) -> None:
        if self.p.segments:
            return
        log.info("transcribing (%s) …", self.whisper_model)
        segs, lang = transcribe.transcribe(
            self.p.vocals_path or str(self._audio),
            language=self.p.src_lang or None,
            model_size=self.whisper_model,
        )
        transcribe.assign_speakers(segs, self.speaker_map)
        self.p.segments = segs
        if not self.p.src_lang:
            self.p.src_lang = lang
        self._save()

    def stage_speakers(self) -> None:
        """Build one clean reference sample per speaker from the vocals stem."""
        if self.p.speaker_refs:
            return
        vocals = self.p.vocals_path or str(self._audio)
        by_speaker: dict[str, list[tuple[float, float]]] = {}
        for s in self.p.segments:
            by_speaker.setdefault(s.speaker, []).append((s.start, s.end))
        for speaker, ranges in by_speaker.items():
            out = self.workdir / f"ref_{speaker}.wav"
            if not out.exists():
                log.info("building reference sample for %s …", speaker)
                media.cut_concat(vocals, ranges, out)
            self.p.speaker_refs[speaker] = str(out)
        self._save()

    def stage_translate(self) -> None:
        todo = [s for s in self.p.segments if not s.text_tgt]
        if not todo:
            return
        log.info("translating %d lines (%s → %s) …",
                 len(todo), self.p.src_lang, self.p.tgt_lang)
        texts = translate.translate_batch(
            [s.text_src for s in self.p.segments],
            self.p.src_lang, self.p.tgt_lang,
            backend=self.translate_backend,
            translations_file=self.translations_file,
        )
        for s, t in zip(self.p.segments, texts):
            s.text_tgt = t
        self._save()

    def stage_synth(self) -> None:
        engine = get_engine(self.tts_engine_name)
        # engines that don't clone (e.g. Piper) don't need per-speaker refs
        is_cloning = self.tts_engine_name not in ("piper",)
        for speaker, ref in self.p.speaker_refs.items():
            if speaker not in self.p.voice_ids:
                log.info("preparing voice for %s …", speaker)
                self.p.voice_ids[speaker] = engine.prepare_voice(
                    speaker, ref if is_cloning else None)
                self._save()

        def synth_all(speed_map: dict[int, float]) -> None:
            for seg in self.p.segments:
                out = self.workdir / f"line_{seg.id:03d}.wav"
                speed = speed_map.get(seg.id, 1.0)
                # re-synthesize if missing OR a new faster speed was requested
                if out.exists() and seg.id not in speed_map:
                    continue
                log.info("synthesizing line %d (speed %.2f, %s) …",
                         seg.id, speed, self.emotion)
                engine.synthesize(seg.text_tgt, self.p.voice_ids[seg.speaker],
                                  out, self.p.tgt_lang, speed=speed,
                                  emotion=self.emotion)
                trimmed = media.trim_silence(out, self.workdir / f"line_{seg.id:03d}_t.wav")
                seg.audio_path = str(trimmed)
                seg.speech_dur = media.probe_duration(trimmed)
                self._save()

        synth_all({})
        # one re-synthesis pass for lines that cannot fit even at max_stretch
        plans = sync.schedule(self.p.segments, self.p.duration,
                              max_stretch=self.max_stretch)
        retry = {s.id: pl.speed_hint for s, pl in zip(self.p.segments, plans)
                 if pl.needs_resynth}
        if retry:
            log.info("re-synthesizing %d long lines at higher speed …", len(retry))
            synth_all(retry)
        sync.apply(sync.schedule(self.p.segments, self.p.duration,
                                 max_stretch=self.max_stretch), self.p.segments)
        self._save()

    def stage_render(self, out_video: str | Path) -> Path:
        log.info("mixing and muxing → %s", out_video)
        return mix.render(self.p.src_video, self.p.segments,
                          self.p.background_path, out_video, self.workdir,
                          bg_volume=self.bg_volume)

    # ---------------- full run ----------------

    def run(self, out_video: str | Path) -> Path:
        self.stage_extract()
        self.stage_separate()
        self.stage_transcribe()
        self.stage_speakers()
        self.stage_translate()
        self.stage_synth()
        return self.stage_render(out_video)
