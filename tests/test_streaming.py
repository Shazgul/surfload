from __future__ import annotations

from pathlib import Path

from surfload.utils.streaming import MultipartStream, ProgressFile, iter_file_chunks


def test_iter_file_chunks_reads_all_bytes(tmp_path: Path) -> None:
    payload = b"a" * (2 * 1024 * 1024 + 123)
    source = tmp_path / "data.bin"
    source.write_bytes(payload)

    chunks = list(iter_file_chunks(source, chunk_size=1024 * 1024))
    assert b"".join(chunks) == payload
    assert max(len(chunk) for chunk in chunks) <= 1024 * 1024


def test_progress_file_enforces_chunk_size_on_read_minus_one(tmp_path: Path) -> None:
    payload = b"b" * (3 * 1024 * 1024 + 17)
    source = tmp_path / "data.bin"
    source.write_bytes(payload)

    seen = 0

    with source.open("rb") as handle:
        wrapped = ProgressFile(handle, progress_callback=lambda sent: None, chunk_size=1024 * 1024)
        parts = []
        while True:
            data = wrapped.read(-1)
            if not data:
                break
            seen += len(data)
            parts.append(data)

    assert seen == len(payload)
    assert b"".join(parts) == payload
    assert max(len(part) for part in parts) <= 1024 * 1024


def test_multipart_stream_length_matches_iterated_bytes(tmp_path: Path) -> None:
    source = tmp_path / "payload.txt"
    source.write_text("hello\n" * 1000, encoding="utf-8")

    progressed = 0

    def callback(sent: int) -> None:
        nonlocal progressed
        progressed += sent

    stream = MultipartStream(
        file_path=source,
        field_name="file",
        filename=source.name,
        mime_type="text/plain",
        form_data={"token": "abc123"},
        boundary="----TestBoundary",
        progress_callback=callback,
        chunk_size=64 * 1024,
    )

    body = b"".join(iter(stream))
    assert len(body) == len(stream)
    assert progressed == source.stat().st_size


def test_multipart_stream_respects_file_offset_and_size(tmp_path: Path) -> None:
    source = tmp_path / "payload.bin"
    payload = b"0123456789"
    source.write_bytes(payload)

    stream = MultipartStream(
        file_path=source,
        field_name="file",
        filename=source.name,
        mime_type="application/octet-stream",
        form_data={},
        boundary="----TestBoundarySlice",
        progress_callback=None,
        chunk_size=4,
        file_start_offset=3,
        file_size=4,
    )

    body = b"".join(iter(stream))
    assert b"3456" in body
    assert b"0123456789" not in body
    assert len(body) == len(stream)
