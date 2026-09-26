#!/bin/bash
# Prepare and verify the voice clone of the film's narrator.
#
# The engine is on PyPI and installs; the weights are 1.9 GB and live on Hugging
# Face, which this sandbox cannot reach -- and no mirror on the allowed hosts has
# them. So the pipeline stops at a known point and waits: the moment the five
# files arrive through the upload door, this installs them, builds a reference
# clip from the narrator's own separated voice, and measures how close the clone
# comes to him before anybody listens to anything.
#
#     bash scripts/prepare_voice_clone.sh
set -u
cd /home/user/DUB-
SLUG=into-the-wild
MODEL_DIR="$HOME/.local/share/tts/tts_models--multilingual--multi-dataset--xtts_v2"
INCOMING=incoming
VENV=.venv-xtts

say() { printf '  %s\n' "$*"; }

# --- 1. the engine ---------------------------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
  say "[محرّك] إنشاء $VENV"
  python3 -m venv "$VENV" || exit 1
fi
if ! "$VENV/bin/python" -c "import TTS" 2>/dev/null; then
  say "[محرّك] تثبيت coqui-tts (تنزيل كبير، دقائق)"
  "$VENV/bin/pip" install --quiet --disable-pip-version-check coqui-tts soundfile numpy \
    || { say "فشل التثبيت"; exit 1; }
fi
say "[محرّك] جاهز: $("$VENV/bin/python" -c 'import TTS;print(TTS.__version__)')"

# --- 2. the weights --------------------------------------------------------
mkdir -p "$MODEL_DIR"
for f in model.pth config.json vocab.json speakers_xtts.pth; do
  if [ -s "$MODEL_DIR/$f" ]; then
    say "[أوزان] موجود: $f ($(du -h "$MODEL_DIR/$f" | cut -f1))"
  elif [ -s "$INCOMING/$f" ]; then
    say "[أوزان] نقل: $f"
    mv "$INCOMING/$f" "$MODEL_DIR/$f"
  else
    say "[أوزان] ناقص: $f"
    FOUND=0
  fi
done

if [ ! -s "$MODEL_DIR/model.pth" ] || [ ! -s "$MODEL_DIR/config.json" ]; then
  say ""
  say "الأوزان لم تصل بعد. المطلوب: model.pth (1.87 غ.بايت) وconfig.json"
  say "وvocab.json وspeakers_xtts.pth — تُرسل من صفحة الباب على المنفذ 8080."
  say "التفاصيل في work/$SLUG/VOICE-CLONE.md"
  exit 2
fi

# --- 3. the reference clip, from the narrator's own isolated voice ----------
REF="work/$SLUG/reference-narrator.wav"
if [ ! -s "$REF" ]; then
  say "[مرجع] بناء مقطع مرجعي من صوت الراوي"
  "$VENV/bin/python" - "$REF" <<'PY' || exit 1
import glob, subprocess, sys
import numpy as np, soundfile as sf
ff = glob.glob('.venv/lib/python3.11/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-*')[0]
out = sys.argv[1]
# Eight seconds from the separated dialogue: long enough for XTTS, short enough to
# load fast, and taken from a stretch the transcription shows as continuous speech.
subprocess.run([ff,'-v','error','-ss','12','-t','8','-i','work/into-the-wild/stems/vocal-00.mp3',
                '-ar','24000','-ac','1','-c:a','pcm_s16le','-y','/tmp/ref.wav'], check=True)
x, sr = sf.read('/tmp/ref.wav')
peak = float(np.max(np.abs(x)) or 0.0)
if peak < 1e-4:
    raise SystemExit('المقطع المرجعي صامت')
sf.write(out, (x/peak*0.89).astype(np.float32), sr)
print(f'  [مرجع] {out} · {len(x)/sr:.1f}ث · {sr} هرتز')
PY
fi

# --- 4. does the engine load, and does it speak Arabic --------------------
say "[تحقق] تحميل XTTS-v2"
"$VENV/bin/python" - <<'PY'
import os, sys
os.environ.setdefault("COQUI_TOS_AGREED", "1")
sys.path.insert(0, '.')
from TTS.api import TTS
try:
    TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cpu")
    print("  [تحقق] ✓ النموذج يُحمَّل")
except Exception as exc:
    print(f"  [تحقق] ✗ فشل التحميل: {type(exc).__name__}: {exc}")
    sys.exit(1)
PY
[ $? -ne 0 ] && { say "النموذج لا يُحمَّل — لن أُكمل"; exit 3; }

# --- 5. speak one sentence, then measure it against the narrator ----------
say "[تجربة] جملة من نص الفيلم بصوت مستنسخ"
"$VENV/bin/python" - <<'PY'
import os, sys, wave
os.environ.setdefault("COQUI_TOS_AGREED", "1")
sys.path.insert(0, '.')
from TTS.api import TTS
import numpy as np
model = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cpu")
text = ("عايزك تفتكر حاجة مهمة جدا، افتكر دايما حادثة رازول. كان في جسم زي المناطيد "
        "كده وقع في منطقة اسمها رازويل سنة ألف وتسعمية وسبعة وأربعين.")
wav = model.tts(text=text, speaker_wav="work/into-the-wild/reference-narrator.wav",
                language="ar")
import soundfile as sf
sf.write("work/into-the-wild/auditions/clone-v1.wav", np.asarray(wav, dtype=np.float32), 24000)
print("  [تجربة] ✓ work/into-the-wild/auditions/clone-v1.wav")
PY
[ $? -ne 0 ] && { say "فشل التوليد"; exit 4; }

say "[قياس] نبرة المستنسخ مقابل راوي الفيلم (166.7 هرتز)"
"$VENV/bin/python" - <<'PY'
import glob, subprocess
import numpy as np, soundfile as sf
ff = glob.glob('.venv/lib/python3.11/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-*')[0]
def f0(p):
    subprocess.run([ff,'-v','error','-i',p,'-ar','16000','-ac','1','-f','wav','-y','/tmp/m.wav'],check=True)
    d,_=sf.read('/tmp/m.wav'); d=np.asarray(d,float); sr=16000
    win,hop=int(0.04*sr),int(0.02*sr); v=[]
    for i in range(0,len(d)-win,hop):
        f=d[i:i+win]
        if np.sqrt(np.mean(f**2))<0.01: continue
        f=f-f.mean(); ac=np.correlate(f,f,'full')[win-1:]
        lo,hi=int(sr/350),int(sr/70)
        if hi>=len(ac): continue
        seg=ac[lo:hi]
        if len(seg)<5: continue
        lag=lo+int(np.argmax(seg))
        if ac[lag]>0.3*ac[0]: v.append(sr/lag)
    return float(np.median(v)) if v else 0.0
c = f0('work/into-the-wild/auditions/clone-v1.wav')
print(f"  المستنسخ: {c:.1f} هرتز · راوي الفيلم: 166.7 هرتز")
if c:
    st = 12*np.log2(c/166.7)
    verdict = "مطابق" if abs(st) < 2 else ("قريب" if abs(st) < 3.5 else "بعيد — الاستنساخ لم يلتقط الصوت")
    print(f"  الفارق: {st:+.2f} نصف نغمة → {verdict}")
PY

say ""
say "الخطوة التالية: اسمع work/$SLUG/auditions/clone-v1.wav واحكم بأذنك."
