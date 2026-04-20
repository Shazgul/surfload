from __future__ import annotations

from pathlib import Path
import zipfile
from types import SimpleNamespace

import pytest

from surfload.utils.compression import normalize_archive_stem, parse_size_to_bytes, prepare_upload_paths


def test_prepare_upload_paths_zip_preserves_structure(tmp_path: Path) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    nested = folder / "nested.txt"
    nested.write_text("nested", encoding="utf-8")

    loose = tmp_path / "loose.txt"
    loose.write_text("loose", encoding="utf-8")

    outputs, cleanup = prepare_upload_paths([folder, loose], compress_mode="zip")
    try:
        assert len(outputs) == 1
        archive = outputs[0]
        assert archive.exists()

        with zipfile.ZipFile(archive, "r") as zf:
            names = set(zf.namelist())
            assert "folder/nested.txt" in names
            assert "loose.txt" in names
    finally:
        cleanup()


def test_prepare_upload_paths_uses_custom_archive_name(tmp_path: Path) -> None:
    source = tmp_path / "demo.txt"
    source.write_text("demo", encoding="utf-8")

    outputs, cleanup = prepare_upload_paths([source], compress_mode="zip", archive_name="release-build")
    try:
        assert len(outputs) == 1
        assert outputs[0].name == "release-build.zip"
    finally:
        cleanup()


def test_parse_size_to_bytes_supports_mb_and_gb() -> None:
    assert parse_size_to_bytes("500MB") == 500 * 1024 * 1024
    assert parse_size_to_bytes("1GB") == 1024 * 1024 * 1024
    assert parse_size_to_bytes("1.5GiB") == int(1.5 * 1024 * 1024 * 1024)


def test_normalize_archive_stem_strips_known_extension() -> None:
    assert normalize_archive_stem("bundle.zip") == "bundle"
    assert normalize_archive_stem("bundle.7z") == "bundle"


def test_prepare_upload_paths_zip_with_password_uses_7z_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "secret.txt"
    source.write_text("secret", encoding="utf-8")

    captured_cmd: dict[str, list[str]] = {}

    def fake_run(cmd, check, capture_output, text):
        _ = check
        _ = capture_output
        _ = text
        captured_cmd["cmd"] = list(cmd)
        output_path = Path(cmd[4])
        output_path.write_bytes(b"ZIP")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr("surfload.utils.compression.shutil.which", lambda name: "/usr/bin/7z" if name == "7z" else None)
    monkeypatch.setattr("surfload.utils.compression.subprocess.run", fake_run)

    outputs, cleanup = prepare_upload_paths(
        [source],
        compress_mode="zip",
        archive_name="secret-archive",
        archive_password="topsecret",
    )
    try:
        assert len(outputs) == 1
        assert outputs[0].name == "secret-archive.zip"

        cmd = captured_cmd["cmd"]
        assert "-tzip" in cmd
        assert "-ptopsecret" in cmd
        assert "-mem=AES256" in cmd
    finally:
        cleanup()


def test_prepare_upload_paths_with_part_size_returns_split_parts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "big.bin"
    source.write_bytes(b"X" * 1024)

    def fake_run(cmd, check, capture_output, text):
        _ = check
        _ = capture_output
        _ = text
        output_path = Path(cmd[4])
        (output_path.parent / f"{output_path.name}.001").write_bytes(b"part1")
        (output_path.parent / f"{output_path.name}.002").write_bytes(b"part2")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr("surfload.utils.compression.shutil.which", lambda name: "/usr/bin/7z" if name == "7z" else None)
    monkeypatch.setattr("surfload.utils.compression.subprocess.run", fake_run)

    outputs, cleanup = prepare_upload_paths(
        [source],
        compress_mode="zip",
        archive_name="chunked",
        archive_part_size="100MB",
    )
    try:
        names = sorted(path.name for path in outputs)
        assert names == ["chunked.zip.001", "chunked.zip.002"]
    finally:
        cleanup()


def test_prepare_upload_paths_auto_for_video(tmp_path: Path) -> None:
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"0" * 1024)

    outputs, cleanup = prepare_upload_paths([video], compress_mode="auto")
    try:
        assert len(outputs) == 1
        assert outputs[0].suffix == ".zip"
    finally:
        cleanup()
