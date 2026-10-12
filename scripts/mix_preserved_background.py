#!/usr/bin/env python3
"""Dub over the separated bed at *unity gain* with no sidechain or post-mix gain.

The user asked to retain the new video's background without lowering it. The
ordinary master path always ducks music (its --duck-db argument is only logged),
so it must NOT be used for this request. This path levels only the voice. If the
original-level bed leaves no space for -16 LUFS / -1.5 dBTP, reject the run;
never quietly attenuate the bed, clip the sum, or claim a passing master.

The bed is a separation estimate, not the studio's original stem. Its source
file and any processing must be disclosed in the report.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from master_dub import ffmpeg, measure_file


def run(*argv: str) -> None:
    subprocess.run([ffmpeg(), '-y', '-v', 'error', *map(str, argv)], check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--voice', type=Path, required=True, help='film-aligned raw voice wav')
    ap.add_argument('--bed', type=Path, required=True, help='estimated instrumental, not original mix')
    ap.add_argument('--start', type=float, required=True)
    ap.add_argument('--end', type=float, required=True)
    ap.add_argument('--out', type=Path, required=True, help='segment audio (PCM16 WAV)')
    ap.add_argument('--report', type=Path, required=True)
    ap.add_argument('--work', type=Path, required=True)
    a = ap.parse_args()
    if a.end <= a.start or a.start < 0:
        raise ValueError('Bad segment times')
    a.work.mkdir(parents=True, exist_ok=True)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.report.parent.mkdir(parents=True, exist_ok=True)
    dur = a.end - a.start
    voice = a.work / 'voice-trim.wav'
    level = a.work / 'voice-level.wav'
    trial = a.work / 'trial.wav'
    run('-ss', f'{a.start:.3f}', '-i', a.voice, '-t', f'{dur:.3f}',
        '-ar', '48000', '-ac', '2', '-c:a', 'pcm_f32le', voice)
    subprocess.run([sys.executable, str(Path(__file__).with_name('master_dub.py')),
                    '--voice', str(voice), '--plain-voice', '--out', str(level),
                    '--lufs', '-16', '--tp', '-1.5'], check=True)
    tested = []
    winner = None
    # Only the VOICE gets quieter. The music remains at exactly 0 dB; amix's
    # normalize=0 prevents FFmpeg from halving both tracks by default.
    for step in range(0, 33):
        gain = -0.5 * step
        filt = (f'[0:a]volume={gain:.1f}dB[v];[1:a]anull[b];'
                '[v][b]amix=inputs=2:normalize=0:duration=first:dropout_transition=0[out]')
        run('-i', level, '-ss', f'{a.start:.3f}', '-i', a.bed,
            '-t', f'{dur:.3f}', '-filter_complex', filt, '-map', '[out]',
            '-ar', '48000', '-ac', '2', '-c:a', 'pcm_f32le', trial)
        measured = measure_file(trial)
        tested.append({'voice_gain_db': gain, **measured})
        if (measured['lufs'] is not None and measured['true_peak'] is not None
                and abs(measured['lufs'] + 16.0) <= 0.3
                and measured['true_peak'] <= -1.5):
            winner = gain
            break
    result = {'source_voice': str(a.voice), 'source_bed': str(a.bed),
              'bed_gain_db': 0, 'ducking': False, 'post_mix_gain_db': 0,
              'start': a.start, 'end': a.end, 'tests': tested,
              'status': 'accepted' if winner is not None else 'rejected_no_safe_mix',
              'selected_voice_gain_db': winner,
              'note': 'Estimated instrumental is not an exact original stem; no intentional bed gain change.'}
    if winner is not None:
        run('-i', trial, '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', a.out)
        result['measured_output'] = measure_file(a.out)
        if (abs(result['measured_output']['lufs'] + 16.0) > .3 or
                result['measured_output']['true_peak'] > -1.4):
            result['status'] = 'rejected_after_pcm_conversion'
    a.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"Preserved bed: {result['status']} · music 0 dB · voice {winner} dB", flush=True)
    if result.get('measured_output'):
        print(result['measured_output'], flush=True)
    return 0 if result['status'] == 'accepted' else 2


if __name__ == '__main__':
    raise SystemExit(main())
