#!/usr/bin/env python3
"""Fetch checksum-pinned multilingual Whisper Small and build whisper.cpp.

Uses ordinary GitHub objects when Hugging Face and release-asset hosts are
unreachable. No workflow permissions, TTS service, or credentials in files.
Everything large is stored in the repository's ignored .cache directory.
Requires gh, a C++ compiler, make, and cmake (pip install cmake is sufficient).
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
CPP_REPO = "ggml-org/whisper.cpp"
CPP_REVISION = "a8d002cfd879315632a579e73f0148d06959de36"  # v1.7.6


def file_digest(path: Path, algorithm: str = "sha256", *, git_blob: bool = False) -> str:
    digest = hashlib.new(algorithm)
    if git_blob:
        digest.update(f"blob {path.stat().st_size}\0".encode())
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_part(path: Path, spec: dict) -> bool:
    return (
        path.is_file() and path.stat().st_size == spec["size"]
        and file_digest(path, "sha1", git_blob=True) == spec["sha"]
    )


def fetch_model(cache: Path, manifest: dict) -> Path:
    root = cache / "models/whisper-ggml"
    root.mkdir(parents=True, exist_ok=True)
    model = root / "ggml-small.bin"
    if model.is_file() and file_digest(model) == manifest["sha256"]:
        print(f"Verified cached model: {model}", flush=True)
        return model
    parts = root / "parts"
    parts.mkdir(exist_ok=True)

    def fetch(spec):
        name = spec["path"]
        if Path(name).name != name or name in ("", ".", ".."):
            raise ValueError("Invalid model part name")
        path = parts / name
        if check_part(path, spec):
            return path
        endpoint = f"repos/{manifest['mirror_repo']}/contents/{name}?ref={manifest['mirror_commit']}"
        temporary = path.with_suffix(".download")
        try:
            with temporary.open("wb") as out:
                subprocess.run(["gh", "api", endpoint, "-H", "Accept: application/vnd.github.raw+json"], stdout=out, check=True)
            if not check_part(temporary, spec):
                raise ValueError(f"Model part checksum/size mismatch: {name}")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        print(f"Verified model part: {name}", flush=True)
        return path

    with ThreadPoolExecutor(max_workers=2) as pool:
        paths = list(pool.map(fetch, manifest["parts"]))
    temporary = model.with_suffix(".partial")
    try:
        with temporary.open("wb") as out:
            for path in paths:
                with path.open("rb") as stream:
                    shutil.copyfileobj(stream, out)
        if temporary.stat().st_size != manifest["bytes"] or file_digest(temporary) != manifest["sha256"]:
            raise ValueError("Whisper Small model does not match the verified official weights")
        temporary.replace(model)
    finally:
        temporary.unlink(missing_ok=True)
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return model


def extract_source(archive: Path, destination: Path) -> None:
    """Strip the GitHub archive root; never follow archive links or '..'."""
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            parts = Path(member.name).parts[1:]
            if not parts:
                continue
            target = destination.joinpath(*parts).resolve()
            if not target.is_relative_to(destination):
                raise ValueError("Unsafe source archive path")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
            # Documentation symlinks/device files are not needed for the CLI.


def build_cli(cache: Path) -> Path:
    tools = cache / "tools"
    source = tools / "whisper.cpp"
    cli = source / "build/bin/whisper-cli"
    if cli.is_file():
        return cli
    cmake = shutil.which("cmake")
    if not cmake:
        candidate = ROOT / ".venv/bin/cmake"
        if candidate.is_file():
            cmake = str(candidate)
    if not cmake:
        raise RuntimeError("Install cmake first: .venv/bin/pip install cmake")
    tools.mkdir(parents=True, exist_ok=True)
    archive = tools / "whisper-cpp-v1.7.6.tar.gz"
    with archive.open("wb") as out:
        subprocess.run(["gh", "api", f"repos/{CPP_REPO}/tarball/{CPP_REVISION}"], stdout=out, check=True)
    extract_source(archive, source)
    subprocess.run([
        cmake, "-S", str(source), "-B", str(source / "build"),
        "-DWHISPER_BUILD_TESTS=OFF", "-DWHISPER_CURL=OFF", "-DCMAKE_BUILD_TYPE=Release",
    ], check=True)
    subprocess.run([cmake, "--build", str(source / "build"), "--target", "whisper-cli", "-j2"], check=True)
    return cli


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache")
    parser.add_argument("--model-only", action="store_true")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "config/whisper-small-ggml.json").read_text())
    model = fetch_model(args.cache, manifest)
    cli = None if args.model_only else build_cli(args.cache)
    print(json.dumps({"model": str(model), "whisper_cli": str(cli) if cli else None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
