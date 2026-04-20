from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterator, BinaryIO

ProgressCallback = Callable[[int], None]


def iter_file_chunks(file_path: Path, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    with file_path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            yield chunk


class ProgressFile:
    """File wrapper that guarantees chunked reads and emits byte progress."""

    def __init__(
        self,
        file_handle: BinaryIO,
        progress_callback: ProgressCallback | None = None,
        chunk_size: int = 1024 * 1024,
    ) -> None:
        self.file_handle = file_handle
        self.progress_callback = progress_callback
        self.chunk_size = max(64 * 1024, chunk_size)

    def read(self, size: int = -1) -> bytes:
        # requests can call read(-1). We still enforce bounded chunks.
        requested = self.chunk_size if size is None or size < 0 else min(size, self.chunk_size)
        data = self.file_handle.read(requested)
        if data and self.progress_callback:
            self.progress_callback(len(data))
        return data

    def __getattr__(self, item: str):
        return getattr(self.file_handle, item)


class MultipartStream:
    """Streaming multipart body generator with fixed content length."""

    def __init__(
        self,
        file_path: Path,
        field_name: str,
        filename: str,
        mime_type: str,
        form_data: dict[str, str] | None,
        boundary: str,
        progress_callback: ProgressCallback | None = None,
        chunk_size: int = 1024 * 1024,
        file_start_offset: int = 0,
        file_size: int | None = None,
    ) -> None:
        self.file_path = file_path
        self.boundary = boundary
        self.progress_callback = progress_callback
        self.chunk_size = max(64 * 1024, chunk_size)

        full_size = self.file_path.stat().st_size
        self.file_start_offset = max(0, min(int(file_start_offset), full_size))
        remaining = max(0, full_size - self.file_start_offset)
        if file_size is None:
            self.file_size = remaining
        else:
            self.file_size = max(0, min(int(file_size), remaining))

        self.form_chunks: list[bytes] = []
        for key, value in (form_data or {}).items():
            self.form_chunks.append(
                (
                    f"--{boundary}\r\n"
                    f"Content-Disposition: form-data; name=\"{key}\"\r\n\r\n"
                    f"{value}\r\n"
                ).encode("utf-8")
            )

        self.file_header = (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"{field_name}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
        self.closing = f"\r\n--{boundary}--\r\n".encode("utf-8")

        self.content_length = (
            sum(len(chunk) for chunk in self.form_chunks)
            + len(self.file_header)
            + self.file_size
            + len(self.closing)
        )

    def __len__(self) -> int:
        return self.content_length

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self.form_chunks:
            yield chunk

        yield self.file_header
        with self.file_path.open("rb") as handle:
            if self.file_start_offset:
                handle.seek(self.file_start_offset)
            remaining = self.file_size
            while True:
                if remaining <= 0:
                    break
                part = handle.read(min(self.chunk_size, remaining))
                if not part:
                    break
                remaining -= len(part)
                if self.progress_callback:
                    self.progress_callback(len(part))
                yield part

        yield self.closing
