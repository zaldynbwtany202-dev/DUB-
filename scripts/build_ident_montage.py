"""Build the identification montage: the narrator, then every voice from this chat.

The user said the voice he wants was heard in the conversation, so the montage
replays exactly those: the narrator's own recording of the sentence first, then
each synthetic voice, same sentence, same order, one after another with a short
gap. Numbers match the file names so he can answer with a digit.
"""
import json
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
import soundfile as sf

FF = imageio_ffmpeg.get_ffmpeg_exe()
WORK = Path("work/into-the-wild")
AUD = WORK / "auditions"

# ---- 1. the narrator's own words, cut from the stem that holds them ----------
groups = json.loads((WORK / "groups.json").read_text(encoding="utf-8"))["groups"]
words = json.loads((WORK / "full-timed-vocal.json").read_text(encoding="utf-8"))["words"]
g = groups[59]
text = g["text"]
want = ["وخلينا", "نرجع", "للحاضر", "والمحطه", "الاخيره", "لاليكس", "قبل", "ما", "يوصل", "الاسراسكا"]
span = [w for w in words if g["t0"] - 1 <= w["t0"] <= g["t1"] + 1]
start = next((w["t0"] for w in span if w["w"] in ("وخلينا", "خلينا")), None)
if start is None:
    sys.exit("  ✗ لم أجد بداية الجملة في التوقيتات")
idx = [i for i, w in enumerate(span) if w["t0"] >= start][0]
seq = span[idx: idx + len(want)]
end = seq[-1]["t1"]
print(f"  الجملة عند الراوي: {seq[0]['w']} … {seq[-1]['w']}  [{start:.2f} → {end:.2f}]")

seg = 280.0
k = int(start // seg)
offset = start - k * seg
stem = WORK / "stems" / f"vocal-{k:02d}.mp3"
print(f"  من المقطع {stem.name} عند {offset:.2f} ثانية داخلًا")

# ---- 2. normalise every sample to one format, then join with gaps -----------
tmp = AUD / "montage"
tmp.mkdir(parents=True, exist_ok=True)
run = lambda a: subprocess.run([FF, "-y", "-v", "error", *a], check=True)

run(["-ss", f"{offset - 0.15:.3f}", "-to", f"{offset + (end - start) + 0.25:.3f}",
     "-i", str(stem), "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le",
     str(tmp / "00.wav")])
print(f"  00 الراوي  {(end - start):.2f} ثانية")

items = [("00", "الراوي")]
for f in sorted(AUD.glob("ident-*.mp3")):
    tag = f.name.split("-")[1]
    voice = f.stem.split("-")[-1]
    dst = tmp / f"{tag}.wav"
    run(["-i", str(f), "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(dst)])
    items.append((tag, voice))
    print(f"  {tag} {voice:<9} {sf.info(str(dst)).duration:.2f} ثانية")

run(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "0.7",
     "-c:a", "pcm_s16le", str(tmp / "gap.wav")])

lst = tmp / "list.txt"
# The concat demuxer resolves relative paths against the list file's own
# directory, so these are bare names, not paths -- "montage/00.wav" here would
# be looked for inside montage/ again.
lst.write_text("".join(f"file '{t}.wav'\nfile 'gap.wav'\n"
                       for t, _ in items), encoding="utf-8")
out = AUD / "ident-all-voices.wav"
run(["-f", "concat", "-safe", "0", "-i", str(lst), "-ar", "44100", "-ac", "1",
     "-c:a", "pcm_s16le", str(out)])
final = AUD / "ident-all-voices.mp3"
run(["-i", str(out), "-codec:a", "libmp3lame", "-b:a", "160k", str(final)])
print(f"\n  ✓ {final}  {sf.info(str(out)).duration:.1f} ثانية")

# The montage wav and the per-voice normalised copies are scratch; the mp3 next
# to them is the sample, and it is the only thing worth keeping.
(final,) = (AUD / "ident-all-voices.mp3",)
for junk in [*(tmp.glob("*.wav")), out]:
    junk.unlink(missing_ok=True)
tmp.rmdir()

(AUD / "ident.txt").write_text(
    "ترتيب ملف الأصوات (الجملة نفسها للجميع):\n"
    + "".join(f"  {t} = {v}\n" for t, v in items)
    + "  ثم صمت 0.7 ثانية بين كل صوتين\n"
    + f"  الجملة: {text[:70]}…\n"
    + f"  ملف كامل: {final}\n", encoding="utf-8")
