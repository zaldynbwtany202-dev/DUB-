"""A FLOAT separation bed may peak above 0 dBFS and must never be clipped."""
from pathlib import Path

import numpy as np
import soundfile as sf

from scripts.separate_background_segmented import join_float_stems


def test_join_preserves_over_full_scale_peak(tmp_path: Path) -> None:
    a, b, out = (tmp_path / name for name in ('a.wav', 'b.wav', 'joined.wav'))
    sf.write(a, np.full((100, 2), 1.15, dtype='float32'), 1000, subtype='FLOAT')
    sf.write(b, np.full((100, 2), .70, dtype='float32'), 1000, subtype='FLOAT')
    join_float_stems([a, b], out, crossfade=.02, sample_rate=1000)
    pcm, sr = sf.read(out, dtype='float32', always_2d=True)
    assert sr == 1000 and sf.info(out).subtype == 'FLOAT'
    assert pcm.shape == (180, 2)
    assert np.isclose(np.max(pcm), 1.15)
    assert np.allclose(pcm[:80], 1.15)
    assert np.allclose(pcm[-80:], .70)
    assert np.all(np.diff(pcm[80:100, 0]) <= 0)


def test_join_rejects_non_stereo_stems(tmp_path: Path) -> None:
    mono = tmp_path / 'mono.wav'
    sf.write(mono, np.ones(100), 1000, subtype='FLOAT')
    try:
        join_float_stems([mono], tmp_path / 'joined.wav', 0.0, sample_rate=1000)
    except ValueError as exc:
        assert 'sample format' in str(exc)
    else:
        raise AssertionError('A mono stem was accepted')
