import hashlib
import io
import json
import tarfile

import pytest

from scripts.bootstrap_local_whisper import check_part, extract_source, file_digest
from scripts.transcribe_video import cpp_result, cpp_words, main


def token(text, start=0, end=100, ident=1, p=0.9):
    return {"text": text, "id": ident, "p": p, "offsets": {"from": start, "to": end}, "t_dtw": 5}


def native(rows=None):
    return {
        "params": {"language": "auto", "translate": False},
        "model": {"type": "small", "multilingual": True},
        "result": {"language": "ar"},
        "transcription": rows if rows is not None else [
            {"offsets": {"from": 0, "to": 1500}, "text": " مرحبا", "tokens": [token(" مرحبا")]},
        ],
    }


def test_arabic_bpe_bytes_rejoin_before_utf8_decode():
    # Mimics native JSON containing two separate bytes for the letter م.
    a = b'\xd9'.decode("utf-8", errors="surrogateescape")
    b = b'\x85'.decode("utf-8", errors="surrogateescape")
    words = cpp_words([
        token(" " + a), token(b, 100, 120), token("رحبا", 120, 300),
        token(" بك", 300, 500), token("[_TT_100]", 500, 500, ident=50464),
    ])
    assert [w["word"] for w in words] == ["مرحبا", "بك"]
    assert words[0]["start"] == 0
    assert words[0]["end"] == 0.3
    json.dumps(words, ensure_ascii=False).encode("utf-8")  # no broken Unicode


def test_result_remains_an_explicit_asr_draft(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"test fixture")
    result = cpp_result(native(), source, duration=2, elapsed=1, language_probability=0.99)
    assert result["review_required"]
    assert result["asr"]["engine"] == "whisper.cpp"
    assert result["asr"]["detected_language"] == "ar"
    assert result["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result["segments"][0]["text"] == "مرحبا"


def test_cpp_rejects_empty_or_translated_output(tmp_path):
    source = tmp_path / "source.mp4"
    with pytest.raises(RuntimeError, match="no speech"):
        cpp_result(native([]), source, duration=2, elapsed=1)
    data = native()
    data["params"]["translate"] = True
    with pytest.raises(ValueError, match="not English translation"):
        cpp_result(data, source, duration=2, elapsed=1)


def test_cpp_rejects_overlapping_and_outside_intervals(tmp_path):
    source = tmp_path / "source.mp4"
    rows = native()["transcription"]
    with pytest.raises(ValueError, match="Overlapping"):
        cpp_result(native(rows * 2), source, duration=2, elapsed=1)
    with pytest.raises(ValueError, match="outside source"):
        cpp_result(native(rows), source, duration=0.5, elapsed=1)


def test_native_long_uncertain_segments_are_flagged(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    data = native([{"text": "test", "offsets": {"from": 0, "to": 10000}, "tokens": [token("test", p=0.2)]}])
    result = cpp_result(data, source, duration=10, elapsed=1)
    assert result["segments"][0]["review_warnings"] == ["long_segment_check_timing", "low_confidence_words"]


def test_cpp_cli_validates_paths_without_loading_model(tmp_path, capsys):
    assert main(["--backend", "whisper-cpp", "--source", str(tmp_path / "missing.mp4")]) == 1
    assert "FileNotFoundError" in capsys.readouterr().err


def test_model_part_requires_size_and_git_blob_checksum(tmp_path):
    path = tmp_path / "partaa"
    data = b"test weights"
    path.write_bytes(data)
    expected = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
    assert file_digest(path, "sha1", git_blob=True) == expected
    assert check_part(path, {"size": len(data), "sha": expected})
    assert not check_part(path, {"size": len(data) + 1, "sha": expected})
    path.write_bytes(b"version https://git-lfs.github.com/spec/v1")
    assert not check_part(path, {"size": len(data), "sha": expected})


def source_archive(path, name, symlink=False):
    with tarfile.open(path, "w:gz") as tar:
        item = tarfile.TarInfo(name)
        if symlink:
            item.type = tarfile.SYMTYPE
            item.linkname = "../../outside"
            tar.addfile(item)
        else:
            item.size = 4
            tar.addfile(item, io.BytesIO(b"data"))


def test_source_archive_cannot_escape_destination(tmp_path):
    path = tmp_path / "source.tar.gz"
    source_archive(path, "package/../../outside")
    with pytest.raises(ValueError, match="Unsafe"):
        extract_source(path, tmp_path / "tool")
    assert not (tmp_path / "outside").exists()


def test_source_archive_strips_prefix_and_ignores_links(tmp_path):
    path = tmp_path / "source.tar.gz"
    source_archive(path, "package/src/hello.cpp")
    extract_source(path, tmp_path / "tool")
    assert (tmp_path / "tool/src/hello.cpp").read_bytes() == b"data"
    source_archive(path, "package/link", symlink=True)
    extract_source(path, tmp_path / "tool")
    assert not (tmp_path / "tool/link").is_symlink()
