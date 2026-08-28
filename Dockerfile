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

# CPU-only torch keeps the image small; local engines (whisper/xtts/demucs) included
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -e '.[whisper,demucs,xtts,google]'

WORKDIR /work
ENTRYPOINT ["dub"]
CMD ["--help"]
