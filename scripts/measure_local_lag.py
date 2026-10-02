#!/usr/bin/env python3
"""قِس تأخّر الدبلجة محليًّا (نافذة بنافذة) بارتباط مُطبَّع إزاحةً بإزاحة.

الأداة السابقة (`measure_envelope_lag.py`) كانت تقسم على طول المغلّف كله بدل
طول المقطع المتقاطع، فيمال المقياس نحو الإزاحة صفر ويجعل الارتباط ضعيفًا
وراسمًا مسطّحًا. هنا يُطبَّع كل مقطع على حِدة (متوسط صفر ووحدة انحراف)، وتُقاس
الإزاحة في نوافذ متتالية، ثم يُجمَع منحنى الارتباط لتقدير عام متين.

    python scripts/measure_local_lag.py --source library/into-the-wild/source.local.mp4 \
        --dub previews/voice25-run-0-31-timed.mp4 --dub-offset 0.12 --range 739.09 \
        --label after --json work/into-the-wild/qa/local-lag-after.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np

SR = 100  # مللي ثانية


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def env(path: Path, lo=300.0, hi=3000.0, smooth=0.2, start=0.0, dur=None) -> np.ndarray:
    cmd = [ffmpeg(), '-v', 'error']
    if start:
        cmd += ['-ss', f'{start:.3f}']
    if dur:
        cmd += ['-t', f'{dur:.3f}']
    cmd += ['-i', str(path), '-vn', '-ac', '1', '-ar', '16000',
            '-af', f'highpass=f={lo:.0f},lowpass=f={hi:.0f}', '-f', 'f32le', '-']
    x = np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, dtype='<f4')
    hop = 16000 // SR
    n = len(x) // hop
    e = np.sqrt((x[:n * hop].astype(np.float64).reshape(n, hop) ** 2).mean(axis=1) + 1e-12)
    e = 20 * np.log10(e + 1e-9)                     # ديسيبل: يقلّل أثر اختلاف الجهارة
    k = max(1, int(smooth * SR))
    ker = np.hanning(k + 2)[1:-1]
    ker /= ker.sum()
    return np.convolve(e, ker, mode='same')


def z(a: np.ndarray) -> np.ndarray:
    a = a - a.mean()
    s = a.std()
    return a / s if s > 1e-9 else a


def ncc_curve(src_win: np.ndarray, dub_pad: np.ndarray, lags: np.ndarray,
              pad: int) -> np.ndarray:
    """ارتباط تطبيعي لكل إزاحة: موجبة = الدبلجة متأخرة.

    `dub_pad` يغطي النافذة زائد `pad` مللي ثانية من كل جهة، فمقطع الإزاحة λ هو
    `dub_pad[λ+pad : λ+pad+W]`.
    """
    b = z(src_win)
    a = z(dub_pad)
    w = len(b)
    out = np.empty(len(lags))
    for k, lag in enumerate(lags):
        p = int(lag) + pad
        if p < 0 or p + w > len(a):
            out[k] = -2.0
            continue
        x = a[p:p + w]
        out[k] = float(x @ b) / w
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', type=Path, required=True)
    ap.add_argument('--dub', type=Path, required=True, help='ملف الدبلجة (صوت أو تسليم)')
    ap.add_argument('--dub-offset', type=float, default=0.0, help='أين يبدأ الدبلج في الفيلم')
    ap.add_argument('--range', type=float, required=True)
    ap.add_argument('--win', type=float, default=24.0)
    ap.add_argument('--step', type=float, default=12.0)
    ap.add_argument('--search', type=float, default=1.5)
    ap.add_argument('--band', default='300,3000')
    ap.add_argument('--label', default='')
    ap.add_argument('--json', type=Path, default=None)
    a = ap.parse_args()
    lo, hi = (float(v) for v in a.band.split(','))

    src = env(a.source, lo=lo, hi=hi, dur=a.range)
    dub = env(a.dub, lo=lo, hi=hi)
    sh = int(round(a.dub_offset * SR))
    if sh > 0:
        dub = np.concatenate([np.full(sh, dub[0] if len(dub) else 0.0), dub])
    print(f'مغلّف الأصل {len(src) / SR:.1f} ث · الدبلجة {len(dub) / SR:.1f} ث')

    L = int(a.search * SR)
    lags = np.arange(-L, L + 1)
    w, s = int(a.win * SR), int(a.step * SR)
    total = np.zeros(len(lags))
    rows = []
    for t0 in range(0, len(src) - w, s):
        seg = src[t0:t0 + w]
        if seg.std() < 3.0:                          # صمت: لا معلومة
            continue
        d1, d0 = t0 - L, t0 + w + L
        if d1 < 0 or d0 > len(dub):
            continue
        curve = ncc_curve(seg, dub[d1:d0], lags, L)
        total += np.where(curve > -1.5, curve, 0.0)
        best = lags[int(np.argmax(curve))]
        rows.append({'t': round((t0 + w / 2) / SR, 1), 'lag': round(float(best) / SR, 3),
                     'r': round(float(curve.max()), 3)})
    med = float(np.median([r['lag'] for r in rows])) if rows else float('nan')
    g = float(lags[int(np.argmax(total))]) / SR
    print(f'{a.label}نوافذ مُقاسة: {len(rows)}')
    print(f'  الإزاحة المجمّعة (متينة): {g:+.2f} ث')
    print(f'  وسيط الإزاحات المحلية: {med:+.2f} ث')
    lags_arr = np.array([r['lag'] for r in rows])
    if len(lags_arr):
        print(f'  مدى محلي: {lags_arr.min():+.2f} … {lags_arr.max():+.2f} · '
              f'الربيع 25%-75%: {np.percentile(lags_arr, 25):+.2f} … {np.percentile(lags_arr, 75):+.2f}')
    if a.json:
        a.json.write_text(json.dumps({'label': a.label, 'aggregate_lag': round(g, 3),
                                      'median_local_lag': round(med, 3),
                                      'windows': rows}, ensure_ascii=False, indent=1),
                          encoding='utf-8')
        print(f'  → {a.json}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
