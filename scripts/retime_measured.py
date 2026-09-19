#!/usr/bin/env python
"""Retime recorded takes phrase by phrase so the dub tracks the original's rate.

One atempo per group can only land a group's first and last word on time. Inside
a 25 s group the dub drifts freely: measured against the source in 10 s windows,
the offset swings between -4.9 s and +5.0 s, adjacent windows disagreeing by
several seconds. That is not drift, it is decoupling.

Two inputs decide whether this works:

1. The target slots. Use spoken-fixed, never spoken-capped. The capped file's
   best whole-window offset against the source is -4.33 s against +0.21 s for
   the fixed one, because the density cap traded sync for an achievable
   speaking rate.

2. Where each word falls inside the take. This must be measured, not modelled.
   A model of word duration as a function of the word gives every word the same
   ratio, which is uniform atempo again -- 628 of 1100 words hit the ceiling
   when the boundaries were estimated from character counts. The boundaries
   come from scripts/align_agent_takes.py.

Word-by-word, the measured boundaries are noisy enough that 459 of 1140 words
ask for a ratio outside the usable range, so words are merged into phrases of
about a second before stretching. That cut the clamped count to 55.

Usage:
    python scripts/retime_measured.py WORK_DIR --timings T.json --out OUT.wav
                                       [--start 0] [--end 88] [--segment-s 1.2]
"""
from __future__ import annotations

import argparse
import difflib
import glob
import json
import re
import subprocess
import tempfile
from pathlib import Path


def find_ffmpeg():
    hits = sorted(glob.glob(".venv/lib/python3*/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-*"))
    return hits[-1] if hits else "ffmpeg"


FF = find_ffmpeg()


def run(args):
    subprocess.run([FF, "-v", "error"] + args, check=True)


def duration(path):
    import os
    tmp = tempfile.mktemp(suffix=".wav")
    try:
        run(["-i", str(path), "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", "-y", tmp])
        return (os.path.getsize(tmp) - 44) / 2 / 44100
    finally:
        p = Path(tmp)
        if p.exists():
            p.unlink()


def normalize(s: str) -> str:
    s = re.sub(r"[^\w\s]", "", s)
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"),
                 ("ة", "ه"), ("ؤ", "و"), ("ئ", "ي")):
        s = s.replace(a, b)
    return s.strip()


def measure_index(work: Path, folder="voice-alignment"):
    """Map take name -> alignment json. The alignments carry a hash, not a name."""
    script = work / "takes-script.json"
    if not script.exists():
        return {}
    out = {}
    for t in json.loads(script.read_text(encoding="utf-8")).get("takes", []):
        f = work / folder / f"take-{int(t['index']):04d}.json"
        if f.exists():
            out[Path(t["audio"]).stem] = f
    return out


def load_measured(work: Path, names, index_map):
    out, offset = [], 0.0
    for name in names:
        f = index_map.get(name)
        if f is None:
            return None
        d = json.loads(f.read_text(encoding="utf-8"))
        for w in d.get("words", []):
            out.append((normalize(w["word"]), w["start"] + offset, w["end"] + offset))
        offset += d.get("duration", 0.0)
    return out


def pair_sequences(src, dst):
    """Match measured words to target words via characters, not whole words.

    The tiny alignment model hears "وحد" where the script says "واحد", so exact
    word pairing found only a third of them; character overlap recovers nearly all.
    """
    A = "".join(w for w, _, _ in src)
    B = "".join(w for w, _, _ in dst)
    if not A or not B:
        return []
    spans_a, at = [], 0
    for w, _, _ in src:
        spans_a.append((at, at + len(w)))
        at += len(w)
    spans_b, at = [], 0
    for w, _, _ in dst:
        spans_b.append((at, at + len(w)))
        at += len(w)

    cm = {}
    for tag, a0, a1, b0, b1 in difflib.SequenceMatcher(None, A, B, autojunk=False).get_opcodes():
        if tag in ("equal", "replace"):
            for k in range(min(a1 - a0, b1 - b0)):
                cm[a0 + k] = b0 + k

    def owner(spans, pos):
        for i, (s, e) in enumerate(spans):
            if s <= pos < e:
                return i
        return None

    score = {}
    for ap, bp in cm.items():
        i, j = owner(spans_a, ap), owner(spans_b, bp)
        if i is None or j is None:
            continue
        score[(i, j)] = score.get((i, j), 0) + 1
    best = {}
    for (i, j), n in score.items():
        if j not in best or n > best[j][1]:
            best[j] = (i, n)
    return sorted((i, j) for j, (i, _) in best.items())


def merge_short(bounds, target, min_s, nat_len):
    """Join neighbouring words into phrases of at least min_s of target time."""
    out_b, out_t = [], []
    cur_w, cur_b0, cur_t0, acc = [], None, None, 0.0
    for (w, p, q), (s, e) in zip(target, bounds):
        if cur_b0 is None:
            cur_b0, cur_t0 = s, p
        cur_w.append(w)
        acc += q - p
        if acc >= min_s:
            out_b.append((cur_b0, max(e, cur_b0 + 0.02)))
            out_t.append((" ".join(cur_w), cur_t0, q))
            cur_w, cur_b0, cur_t0, acc = [], None, None, 0.0
    if cur_w:
        b0 = cur_b0 if cur_b0 is not None else 0.0
        out_b.append((b0, max(bounds[-1][1], b0 + 0.02)))
        out_t.append((" ".join(cur_w),
                      cur_t0 if cur_t0 is not None else target[0][1],
                      target[-1][2]))
    return out_b, out_t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir")
    ap.add_argument("--timings", required=True)
    ap.add_argument("--plan", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=10 ** 9)
    ap.add_argument("--align-dir", default="voice-alignment")
    ap.add_argument("--segment-s", type=float, default=1.2)
    ap.add_argument("--max-ratio", type=float, default=2.6)
    ap.add_argument("--min-ratio", type=float, default=0.55)
    ap.add_argument("--pieces", default=None)
    a = ap.parse_args()

    work = Path(a.work_dir)
    words = json.loads(Path(a.timings).read_text(encoding="utf-8"))["words"]
    plan = Path(a.plan) if a.plan else (work / "groups-fixed.json")
    groups = json.loads(plan.read_text(encoding="utf-8"))["groups"]
    pieces_map = json.loads(a.pieces) if a.pieces else {}
    index_map = measure_index(work, a.align_dir)

    sel = [g for g in groups if a.start <= g["i"] < a.end]
    tmpdir = Path(tempfile.mkdtemp(prefix="dub-measured-"))

    ptr, spans = 0, {}
    for g in groups:
        spans[g["i"]] = (ptr, ptr + g["words_n"])
        ptr += g["words_n"]

    inputs, filters, placed = [], [], []
    stats = {"paired": 0, "target": 0, "clamped": 0, "groups": 0}

    for g in sel:
        i = g["i"]
        a0, a1 = spans[i]
        target = [(w["w"], w["t0"] - g["t0"], w["t1"] - g["t0"]) for w in words[a0:a1]]
        names = pieces_map.get(str(i), [f"g{i:03d}"])
        nat = tmpdir / f"g{i:03d}-nat.wav"
        if len(names) == 1:
            run(["-i", str(work / "takes" / f"{names[0]}.mp3"), "-ar", "44100", "-ac", "2",
                 "-c:a", "pcm_s16le", "-y", str(nat)])
        else:
            ps = []
            for n_, nm in enumerate(names):
                p = tmpdir / f"g{i:03d}-p{n_}.wav"
                run(["-i", str(work / "takes" / f"{nm}.mp3"), "-ar", "44100", "-ac", "2",
                     "-c:a", "pcm_s16le", "-y", str(p)])
                ps.append(p)
            lst = tmpdir / f"g{i:03d}.txt"
            lst.write_text("".join(f"file '{x.resolve()}'\n" for x in ps), encoding="utf-8")
            run(["-f", "concat", "-safe", "0", "-i", str(lst), "-ar", "44100", "-ac", "2",
                 "-c:a", "pcm_s16le", "-y", str(nat)])

        measured = load_measured(work, names, index_map)
        nat_len = duration(nat)

        if not measured:
            tempo = min(max(nat_len / g["window"], 1.0), a.max_ratio)
            fitted = tmpdir / f"g{i:03d}-fit.wav"
            run(["-i", str(nat), "-af", f"atempo={tempo:.5f}", "-ar", "44100", "-ac", "2",
                 "-c:a", "pcm_s16le", "-y", str(fitted)])
            print(f"  #{i:3d}  بلا قياس — atempo موحد {tempo:.2f}")
        else:
            measured = [(w, max(0.0, min(s, nat_len)), max(0.0, min(e, nat_len)))
                        for w, s, e in measured]
            pairs = pair_sequences(measured, target)
            stats["paired"] += len(pairs)
            stats["target"] += len(target)

            bounds = [None] * len(target)
            for mi, ti in pairs:
                bounds[ti] = (measured[mi][1], measured[mi][2])
            last = (0.0, 0.0)
            for k in range(len(target)):
                if bounds[k] is None:
                    nxt = next((bounds[m] for m in range(k + 1, len(target))
                                if bounds[m] is not None), None)
                    bounds[k] = (last[1], nat_len) if nxt is None else (last[1], nxt[0])
                last = bounds[k]

            if a.segment_s > 0:
                bounds, target = merge_short(bounds, target, a.segment_s, nat_len)

            chains, labels, clamped = [], [], 0
            for n, ((s, e), (_tw, p, q)) in enumerate(zip(bounds, target)):
                # An inverted or out-of-range atrim wedges ffmpeg; apad with no
                # length pads forever. Both hung this silently on group 9.
                s = max(0.0, min(s, max(nat_len - 0.03, 0.0)))
                e = max(s + 0.02, min(e, nat_len))
                take_len = e - s
                want = max(q - p, 1e-4)
                ratio = want / take_len
                if ratio < a.min_ratio:
                    ratio, clamped = a.min_ratio, clamped + 1
                elif ratio > a.max_ratio:
                    ratio, clamped = a.max_ratio, clamped + 1
                chains.append(
                    f"[0:a]atrim=start={s:.4f}:end={e:.4f},asetpts=PTS-STARTPTS,"
                    f"atempo={ratio:.5f},adelay={int(round(p*1000))}|{int(round(p*1000))}[w{n}]")
                labels.append(f"[w{n}]")
            stats["clamped"] += clamped
            fitted = tmpdir / f"g{i:03d}-fit.wav"
            span = target[-1][1]
            run(["-i", str(nat), "-filter_complex",
                 ";".join(chains) + f";{''.join(labels)}amix=inputs={len(labels)}:"
                 f"normalize=0:duration=longest:dropout_transition=0,atrim=0:{span:.4f}[o]",
                 "-map", "[o]", "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", "-y", str(fitted)])
            stats["groups"] += 1
            print(f"  #{i:3d}  {len(target):3d} مقطع · مزاوج {len(pairs):3d} · مقيد {clamped}")

        d = int(round(g["t0"] * 1000))
        inputs += ["-i", str(fitted)]
        filters.append(f"[{len(placed)}:a]adelay={d}|{d}[d{len(placed)}]")
        placed.append(f"[d{len(placed)}]")

    total = sel[-1]["t1"]
    run(inputs + ["-filter_complex",
                  ";".join(filters) + f";{''.join(placed)}amix=inputs={len(placed)}:"
                  f"normalize=0:duration=longest:dropout_transition=0,atrim=0:{total:.3f}[o]",
                  "-map", "[o]", "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", "-y", a.out])
    print(f"\nwrote {a.out} ({duration(a.out):.2f}s)")
    print(f"  مزاوج {stats['paired']}/{stats['target']} · مقيد {stats['clamped']} · "
          f"مجموعات بالقياس {stats['groups']}")


if __name__ == "__main__":
    main()
