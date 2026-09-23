#!/bin/bash
# Rebuild everything that lives outside git, after a wipe.
#
# The sandbox has been wiped twelve times. The repository survives, because
# everything that matters is committed and pushed; the toolchain does not,
# because .venv and .cache are scratch by design and are excluded from the
# snapshot. So the film and the takes come back by themselves, and the tools that
# read them have to be rebuilt.
#
# This is the rebuild, in the order the work needs it: the ffmpeg that assembles
# and measures, the runtime that separates music, cmake, and the pinned Whisper
# Small plus whisper.cpp. It is safe to run when some of it already exists --
# every step checks first, so a partial wipe costs only the missing part.
#
#     bash scripts/rebuild_env.sh
set -u
cd /home/user/DUB-

say() { printf '  %s\n' "$*"; }

# ---------------------------------------------------------------------------
# 1. The virtual environment
# ---------------------------------------------------------------------------
if [ ! -x .venv/bin/python ]; then
  say "[بيئة] إنشاء .venv"
  python3 -m venv .venv || { say "فشل إنشاء البيئة"; exit 1; }
else
  say "[بيئة] .venv موجود"
fi

# imageio-ffmpeg carries its own static ffmpeg, which is how this project muxes
# without ffmpeg on PATH. numpy and soundfile read and write the wavs; pyyaml is
# read by the studio config; onnxruntime runs the separator; cmake builds
# whisper.cpp.
need=()
for pkg in imageio-ffmpeg numpy soundfile pyyaml onnxruntime cmake; do
  .venv/bin/python -c "import ${pkg//-/_}" 2>/dev/null || need+=("$pkg")
done
if [ "${#need[@]}" -gt 0 ]; then
  say "[بيئة] تثبيت: ${need[*]}"
  .venv/bin/pip install --quiet --disable-pip-version-check "${need[@]}" \
    || { say "فشل التثبيت"; exit 1; }
else
  say "[بيئة] كل الحزم موجودة"
fi
say "[بيئة] ffmpeg: $(.venv/bin/python -c 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())')"

# ---------------------------------------------------------------------------
# 2. Whisper: the checksum-pinned model and the built binary
# ---------------------------------------------------------------------------
WCLI=.cache/tools/whisper.cpp/build/bin/whisper-cli
MODEL=.cache/models/whisper-ggml/ggml-small.bin

if [ -s "$WCLI" ] && [ -s "$MODEL" ]; then
  say "[whisper] موجود: $(stat -c%s "$MODEL") بايت"
else
  say "[whisper] بناء من المصدر المثبَّت (الدقائق القادمة)"
  .venv/bin/python scripts/bootstrap_local_whisper.py || {
    say "فشل بناء whisper"; exit 1; }
  say "[whisper] النموذج: $(stat -c%s "$MODEL" 2>/dev/null) بايت"
  say "[whisper] المحرك: $WCLI"
fi

# ---------------------------------------------------------------------------
# 3. The source film, reassembled from its tracked parts
# ---------------------------------------------------------------------------
SRC=library/into-the-wild/source.local.mp4
if [ ! -s "$SRC" ]; then
  say "[فيلم] جمع الأجزاء"
  cat library/into-the-wild/parts/part-000 library/into-the-wild/parts/part-001 > "$SRC"
fi
want=$(cut -d' ' -f1 library/into-the-wild/SOURCE.sha256 2>/dev/null)
got=$(sha256sum "$SRC" | cut -d' ' -f1)
if [ "$want" = "$got" ]; then
  say "[فيلم] البصمة مطابقة · $(stat -c%s "$SRC") بايت"
else
  say "[فيلم] تحذير: البصمة مختلفة — المصدر تالف"
fi

say "انتهت إعادة البناء"
