"""CPU-only neural background extraction; not an EQ pretending to remove speech.

Independent NumPy/SciPy processing for the public four-band VR ONNX interface.
The network estimates instrumental magnitudes. Its bounded masks are projected
onto the original spectrum: only original audio is retained, with original
phase and timing. This is an estimate, not a guarantee of speech-free stems.
"""
from __future__ import annotations

import gc
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

import numpy as np
import soundfile as sf
from scipy import fft, signal

from youtube_auto_dub.ffmpeg_bin import ffmpeg_exe

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path, algorithm: str = "sha256") -> str:
    h = hashlib.new(algorithm)
    with path.open("rb") as f:
        for data in iter(lambda: f.read(1024 * 1024), b""):
            h.update(data)
    return h.hexdigest()


def fetch_model(config: dict, cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    model = cache / "uvr-fp16-sim.onnx"
    if model.is_file() and digest(model) == config["model_sha256"]:
        return model
    endpoint = f"repos/{config['model_repo']}/contents/{config['model_path']}?ref={config['model_revision']}"
    temporary = model.with_suffix(".download")
    try:
        with temporary.open("wb") as out:
            subprocess.run(["gh", "api", endpoint, "-H", "Accept: application/vnd.github.raw+json"], stdout=out, check=True)
        if temporary.stat().st_size != config["model_bytes"] or digest(temporary) != config["model_sha256"]:
            raise ValueError("Model download is incomplete, a Git LFS pointer, or has the wrong SHA-256")
        temporary.replace(model)
    finally:
        temporary.unlink(missing_ok=True)
    return model


def decode_stereo(source: Path, sr: int) -> np.ndarray:
    result = subprocess.run([ffmpeg_exe(), "-v", "error", "-i", str(source), "-vn", "-ac", "2",
                             "-ar", str(sr), "-f", "f32le", "-"], check=True, capture_output=True)
    wave = np.frombuffer(result.stdout, dtype=np.float32).reshape(-1, 2).T.copy()
    if not wave.size or not np.isfinite(wave).all():
        raise ValueError("Source has no valid audio")
    return wave


def spectrum(wave: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    """Unnormalised, centred, periodic-Hann STFT (channel, frequency, time)."""
    padded = np.pad(wave, ((0, 0), (n_fft // 2, n_fft // 2)), mode="reflect")
    frames = np.lib.stride_tricks.sliding_window_view(padded, n_fft, axis=1)[:, ::hop, :]
    window = signal.windows.hann(n_fft, sym=False).astype(np.float32)
    return fft.rfft(frames * window, axis=-1, workers=2).transpose(0, 2, 1).astype(np.complex64)


def waveform(spec: np.ndarray, n_fft: int, hop: int, length: int) -> np.ndarray:
    frames = spec.shape[-1]
    window = signal.windows.hann(n_fft, sym=False).astype(np.float32)
    size = (frames - 1) * hop + n_fft
    out = np.zeros((spec.shape[0], size), dtype=np.float32)
    weight = np.zeros(size, dtype=np.float32)
    for begin in range(0, frames, 256):
        block = fft.irfft(spec[:, :, begin:begin + 256], n=n_fft, axis=1, workers=2)
        for j in range(block.shape[-1]):
            pos = (begin + j) * hop
            out[:, pos:pos + n_fft] += block[:, :, j] * window
            weight[pos:pos + n_fft] += window * window
    out /= np.maximum(weight, 1e-8)
    out = out[:, n_fft // 2:n_fft // 2 + length]
    return np.pad(out, ((0, 0), (0, max(0, length - out.shape[-1]))))


def encode_bands(wave: np.ndarray, config: dict) -> np.ndarray:
    """Encode the model's published sample-rate/crop layout; no learned weights here."""
    rates = {config["sample_rate"]: wave}
    crops = []
    for band in config["bands"]:
        sr = band["sr"]
        if sr not in rates:
            divisor = math.gcd(sr, config["sample_rate"])
            rates[sr] = signal.resample_poly(wave, sr // divisor, config["sample_rate"] // divisor, axis=1).astype(np.float32)
        lo, hi = band["crop"]
        crops.append(np.abs(spectrum(rates[sr], band["fft"], band["hop"])[:, lo:hi, :]).astype(np.float32))
    count = min(x.shape[-1] for x in crops)
    mag = np.concatenate([x[:, :, :count] for x in crops], axis=1)
    if mag.shape[1] != config["bins"]:
        raise ValueError("Model frequency layout mismatch")
    mag = np.pad(mag, ((0, 0), (0, 1), (0, 0)))
    previous = 1.0
    for index in range(config["prefilter_start"] + 1, config["prefilter_stop"]):
        gain = 10 ** (-(index - config["prefilter_start"]) * (3.5 - previous) / 20)
        mag[:, index, :] *= gain
        previous = gain
    return mag


def infer_masks(magnitude: np.ndarray, model: Path, config: dict, progress=None) -> np.ndarray:
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
    if session.get_inputs()[0].type != "tensor(float16)":
        raise ValueError("Unexpected model input type")
    coef = float(magnitude.max())
    if coef < 1e-8:
        return np.zeros_like(magnitude)
    normal = (magnitude / coef).astype(np.float16)
    frames = magnitude.shape[-1]
    context, window = config["context_frames"], config["window_frames"]
    step = window - 2 * context
    count = math.ceil(frames / step)
    padded = np.pad(normal, ((0, 0), (0, 0), (context, context + (-frames) % step)))
    masks = np.empty_like(magnitude)
    for i in range(count):
        start = i * step
        x = np.ascontiguousarray(padded[None, :, :, start:start + window])
        prediction = session.run(None, {session.get_inputs()[0].name: x})[0][0, :, :, context:context + step].astype(np.float32)
        take = min(step, frames - start)
        base = normal[:, :, start:start + take].astype(np.float32)
        ratio = np.divide(prediction[:, :, :take], base, out=np.zeros_like(base), where=base > 1e-7)
        if not np.isfinite(ratio).all():
            raise ValueError("Non-finite neural model output")
        masks[:, :, start:start + take] = np.clip(ratio, 0, 1)
        if progress:
            progress(i + 1, count)
    del session
    gc.collect()
    return masks


def band_weights(frequencies: np.ndarray, band: dict) -> np.ndarray:
    spacing = band["sr"] / band["fft"]
    lo, hi = band["crop"]
    weight = ((frequencies >= lo * spacing) & (frequencies <= (hi - 1) * spacing)).astype(np.float32)
    if "lowpass" in band:
        begin, end = np.array(band["lowpass"], dtype=float) * spacing
        weight *= np.clip((end - frequencies) / (end - begin), 0, 1)
    if "highpass" in band:
        begin, end = np.array(band["highpass"], dtype=float) * spacing
        weight *= np.clip((frequencies - begin) / (end - begin), 0, 1)
    return weight


def apply_masks(wave: np.ndarray, masks: np.ndarray, config: dict, power: float = 1.35) -> np.ndarray:
    """Blend overlapping learned bands onto original phase, never synthesize music."""
    if not math.isfinite(power) or power < 1:
        raise ValueError("Mask power must be finite and at least one")
    n_fft, hop = config["reference_fft"], config["reference_hop"]
    spec = spectrum(wave, n_fft, hop)
    count = spec.shape[-1]
    if masks.shape[-1] < count:
        masks = np.pad(masks, ((0, 0), (0, 0), (0, count - masks.shape[-1])), mode="edge")
    frequencies = fft.rfftfreq(n_fft, 1 / config["sample_rate"])
    for first in range(0, len(frequencies), 128):
        freq = frequencies[first:first + 128]
        total = np.zeros((2, len(freq), count), dtype=np.float32)
        divisor = np.zeros(len(freq), dtype=np.float32)
        offset = 0
        for band in config["bands"]:
            lo, hi = band["crop"]
            width = hi - lo
            spacing = band["sr"] / band["fft"]
            position = np.clip(freq / spacing - lo, 0, width - 1)
            left = np.floor(position).astype(int)
            right = np.minimum(left + 1, width - 1)
            fraction = (position - left).astype(np.float32)[None, :, None]
            values = masks[:, offset + left, :count] * (1 - fraction) + masks[:, offset + right, :count] * fraction
            weight = band_weights(freq, band)
            total += values * weight[None, :, None]
            divisor += weight
            offset += width
        total /= np.maximum(divisor[None, :, None], 1e-8)
        spec[:, first:first + len(freq), :] *= np.clip(total, 0, 1) ** power
    return waveform(spec, n_fft, hop, wave.shape[-1])


def separate_background(source: Path, output: Path, work_dir: Path, *, power: float = 1.35) -> dict:
    config = json.loads((ROOT / "config/uvr-background.json").read_text())
    if not source.is_file() or source.resolve() == output.resolve():
        raise ValueError("Need a real source and a separate output path")
    work_dir.mkdir(parents=True, exist_ok=True)
    model = fetch_model(config, ROOT / ".cache/separation/models")
    source_sha = digest(source)
    started = time.monotonic()
    wave = decode_stereo(source, config["sample_rate"])
    key = {"source_sha256": source_sha, "model_sha256": config["model_sha256"], "frames_source": wave.shape[-1]}
    cache = work_dir / "model-masks.npy"
    cache_meta = work_dir / "model-masks.json"
    if cache.is_file() and cache_meta.is_file() and json.loads(cache_meta.read_text()) == key:
        masks = np.load(cache)
        print("Reusing verified-source model masks", flush=True)
    else:
        magnitude = encode_bands(wave, config)
        print(f"Separating {wave.shape[-1] / config['sample_rate']:.2f}s with UVR on CPU", flush=True)
        masks = infer_masks(magnitude, model, config, lambda done, total: print(f"UVR window {done}/{total}", flush=True))
        np.save(cache, masks)
        cache_meta.write_text(json.dumps(key, indent=2) + "\n")
        del magnitude
    background = apply_masks(wave, masks, config, power)
    if background.shape != wave.shape or not np.isfinite(background).all():
        raise ValueError("Bad stem output shape or samples")
    if float(np.max(np.abs(background))) >= 1:
        raise ValueError("Extracted background would clip; refusing an unreported level change")
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, background.T, config["sample_rate"], subtype="PCM_24")
    sf.write(work_dir / "estimated-dialogue.wav", (wave - background).T, config["sample_rate"], subtype="PCM_24")
    def rms_db(x):
        return float(20 * np.log10(max(float(np.sqrt(np.mean(x * x))), 1e-12)))
    report = {"method": "neural UVR four-band ONNX instrumental mask projected onto source phase",
              "is_exact_original_stem": False, "speech_contamination": "not_yet_checked",
              "source": str(source), "source_sha256": source_sha, "model_sha256": config["model_sha256"],
              "output": str(output), "output_sha256": digest(output), "sample_rate": config["sample_rate"],
              "samples": wave.shape[-1], "duration": wave.shape[-1] / config["sample_rate"],
              "mask_power": power, "source_rms_dbfs": rms_db(wave), "background_rms_dbfs": rms_db(background),
              "background_peak_dbfs": float(20 * np.log10(max(float(np.max(np.abs(background))), 1e-12))),
              "elapsed_seconds": round(time.monotonic() - started, 3)}
    output.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report
