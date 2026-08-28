# DUB- — the repo IS the runner: build once, dub anywhere.
#   docker build -t dub-forge .
#   docker run --rm -v $PWD:/work dub-forge run /work/clip.mp4 --src en --tgt ar --out /work/dubbed.mp4
FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml requirements.txt README.md ./
COPY dub ./dub

# CPU-only torch+torchaudio keep the image small; local engines included.
# Both must come from the CPU index or TTS pulls a CUDA torchaudio that
# crashes on machines without CUDA.
RUN pip install --no-cache-dir "torch<2.6" "torchaudio<2.6" --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -e '.[whisper,demucs,xtts,google]'

WORKDIR /work
ENTRYPOINT ["dub"]
CMD ["--help"]
