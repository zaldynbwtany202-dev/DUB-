"""dub — professional AI video dubbing CLI.

Examples:
  dub run clip.mp4 --src en --tgt ar --engine xtts --out dubbed.mp4
  dub run clip.mp4 --src en --tgt ar --engine minimax --no-separate
  dub engines
"""
from __future__ import annotations

import logging
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from .models import Project
from .pipeline import DubPipeline
from .tts import available

console = Console()
logging.basicConfig(level=logging.INFO, format="%(message)s",
                    handlers=[RichHandler(console=console, show_path=False)])


def _parse_speaker_map(value: str | None) -> dict[str, str]:
    """Parse '0:S2,3:S1' into {'0': 'S2', '3': 'S1'}."""
    if not value:
        return {}
    out = {}
    for pair in value.split(","):
        k, v = pair.split(":")
        out[k.strip()] = v.strip()
    return out


@click.group()
@click.version_option(package_name="dub-forge")
def main() -> None:
    """Professional AI video dubbing: transcribe → translate → clone → sync → mix."""


@main.command()
def engines() -> None:
    """List the available TTS / voice-cloning engines."""
    table = Table(title="TTS engines")
    table.add_column("engine")
    table.add_column("notes")
    notes = {
        "xtts": "local, free, offline — Coqui XTTS v2 (pip install TTS)",
        "elevenlabs": "cloud — needs ELEVENLABS_API_KEY",
        "minimax": "cloud — needs FAL_KEY and pip install fal-client",
    }
    for name in available():
        table.add_row(name, notes.get(name, ""))
    console.print(table)


@main.command()
@click.argument("video", type=click.Path(exists=True, dir_okay=False))
@click.option("--src", default="", help="source language code (empty = auto-detect)")
@click.option("--tgt", required=True, help="target language code, e.g. ar")
@click.option("--engine", default="xtts", help="TTS engine: xtts | elevenlabs | minimax")
@click.option("--out", default="dubbed.mp4", type=click.Path())
@click.option("--workdir", default="dub_work", type=click.Path())
@click.option("--max-stretch", default=1.25, show_default=True,
              help="max atempo factor before re-synthesis at higher speed")
@click.option("--bg-volume", default=0.5, show_default=True,
              help="background stem volume relative to the dubbed voice")
@click.option("--no-separate", is_flag=True,
              help="skip stem separation; duck the original mix instead")
@click.option("--translate-backend", default="google", show_default=True,
              type=click.Choice(["google", "argos", "file"]))
@click.option("--translations-file", default=None,
              help="JSON file with human translations (backend=file)")
@click.option("--speaker-map", default=None,
              help="force speakers, e.g. '0:S2,1:S1' (default: alternating turns)")
@click.option("--whisper-model", default="small", show_default=True)
def run(video, src, tgt, engine, out, workdir, max_stretch, bg_volume,
        no_separate, translate_backend, translations_file, speaker_map,
        whisper_model) -> None:
    """Dub VIDEO into another language end-to-end."""
    project = Project(src_video=str(Path(video).resolve()),
                      src_lang=src, tgt_lang=tgt)
    pipe = DubPipeline(
        project, workdir,
        tts_engine=engine,
        max_stretch=max_stretch,
        bg_volume=bg_volume,
        do_separate=not no_separate,
        translate_backend=translate_backend,
        translations_file=translations_file,
        speaker_map=_parse_speaker_map(speaker_map),
        whisper_model=whisper_model,
    )
    result = pipe.run(out)
    console.print(f"[bold green]Done →[/bold green] {result}")


if __name__ == "__main__":
    main()
