from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest

from surfload.core import UploadManager
from surfload.utils.credentials import CredentialStore


def _start_resume_test_server(root: Path) -> tuple[ThreadingHTTPServer, dict[str, Any]]:
    state: dict[str, Any] = {
        "fail_once": True,
        "put_ranges": [],
    }

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_HEAD(self):  # noqa: N802
            if not self.path.startswith("/upload/"):
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            filename = unquote(self.path.split("/upload/", 1)[1]).strip("/")
            target = root / filename
            if not target.exists():
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            uploaded = target.stat().st_size
            self.send_response(200)
            self.send_header("X-Uploaded-Bytes", str(uploaded))
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_PUT(self):  # noqa: N802
            if not self.path.startswith("/upload/"):
                self._send_json(404, {"error": "not found"})
                return

            filename = unquote(self.path.split("/upload/", 1)[1]).strip("/")
            if not filename:
                self._send_json(400, {"error": "filename missing"})
                return

            length = int(self.headers.get("Content-Length", "0") or 0)
            content_range = self.headers.get("Content-Range", "")
            state["put_ranges"].append(content_range)

            target = root / filename
            start = 0
            if content_range.startswith("bytes "):
                bounds = content_range.split(" ", 1)[1].split("/", 1)[0]
                start_text, end_text = bounds.split("-", 1)
                start = int(start_text)
                end = int(end_text)
                expected = max(0, end - start + 1)
                if expected != length:
                    self._send_json(400, {"error": "length mismatch"})
                    return

            body = self.rfile.read(length)

            mode = "r+b" if target.exists() else "wb"
            with target.open(mode) as handle:
                handle.seek(start)
                if state["fail_once"] and not content_range:
                    keep = max(1, len(body) // 2)
                    handle.write(body[:keep])
                    state["fail_once"] = False
                    self._send_json(500, {"error": "simulated interruption"})
                    return
                handle.write(body)

            url = f"http://127.0.0.1:{self.server.server_address[1]}/files/{filename}"
            self._send_json(200, {"url": url})

        def do_GET(self):  # noqa: N802
            if not self.path.startswith("/files/"):
                self._send_json(404, {"error": "not found"})
                return

            filename = unquote(self.path.split("/files/", 1)[1]).strip("/")
            target = root / filename
            if not target.exists():
                self._send_json(404, {"error": "missing"})
                return

            payload = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args):
            _ = format
            _ = args
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, state


def _start_gofile_resume_test_server(root: Path) -> tuple[ThreadingHTTPServer, dict[str, Any]]:
    state: dict[str, Any] = {
        "fail_once": True,
        "post_ranges": [],
    }

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        @staticmethod
        def _extract_file_payload(body: bytes, content_type: str) -> bytes:
            marker = "boundary="
            if marker not in content_type:
                return b""
            boundary = content_type.split(marker, 1)[1].strip().strip('"')
            separator = f"--{boundary}".encode("utf-8")
            for part in body.split(separator):
                if b"Content-Disposition" not in part or b'name="file"' not in part:
                    continue
                header_end = part.find(b"\r\n\r\n")
                if header_end < 0:
                    continue
                payload = part[header_end + 4 :]
                if payload.endswith(b"\r\n"):
                    payload = payload[:-2]
                return payload
            return b""

        def do_GET(self):  # noqa: N802
            if self.path != "/getServer":
                self._send_json(404, {"status": "error", "message": "not found"})
                return

            port = int(self.server.server_address[1])
            self._send_json(
                200,
                {
                    "status": "ok",
                    "data": {
                        "server": f"http://127.0.0.1:{port}",
                    },
                },
            )

        def do_HEAD(self):  # noqa: N802
            if not self.path.startswith("/resume/"):
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            filename = unquote(self.path.split("/resume/", 1)[1]).strip("/")
            target = root / filename
            if not target.exists():
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            uploaded = target.stat().st_size
            self.send_response(200)
            self.send_header("Content-Length", str(uploaded))
            self.end_headers()

        def do_POST(self):  # noqa: N802
            if self.path != "/uploadFile":
                self._send_json(404, {"status": "error", "message": "not found"})
                return

            length = int(self.headers.get("Content-Length", "0") or 0)
            content_range = self.headers.get("Content-Range", "")
            state["post_ranges"].append(content_range)

            body = self.rfile.read(length)
            content_type = self.headers.get("Content-Type", "")
            file_payload = self._extract_file_payload(body, content_type)
            filename = "upload.bin"
            disposition_marker = b'filename="'
            marker_pos = body.find(disposition_marker)
            if marker_pos >= 0:
                marker_end = body.find(b'"', marker_pos + len(disposition_marker))
                if marker_end > marker_pos:
                    filename = body[marker_pos + len(disposition_marker) : marker_end].decode("utf-8", "ignore")

            target = root / filename
            start = 0
            if content_range.startswith("bytes "):
                bounds = content_range.split(" ", 1)[1].split("/", 1)[0]
                start_text, _end_text = bounds.split("-", 1)
                start = int(start_text)

            mode = "r+b" if target.exists() else "wb"
            with target.open(mode) as handle:
                handle.seek(start)
                if state["fail_once"] and not content_range:
                    keep = max(1, len(file_payload) // 2)
                    handle.write(file_payload[:keep])
                    state["fail_once"] = False
                    self._send_json(500, {"status": "error", "message": "simulated interruption"})
                    return
                handle.write(file_payload)

            port = int(self.server.server_address[1])
            self._send_json(
                200,
                {
                    "status": "ok",
                    "data": {
                        "downloadPage": f"http://127.0.0.1:{port}/d/{filename}",
                    },
                },
            )

        def log_message(self, format: str, *args):
            _ = format
            _ = args
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, state


def _start_buzzheavier_resume_test_server(root: Path) -> tuple[ThreadingHTTPServer, dict[str, Any]]:
    state: dict[str, Any] = {
        "fail_once": True,
        "put_ranges": [],
    }

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _target_path(self) -> Path | None:
            filename = unquote(self.path.split("?", 1)[0]).strip("/").split("/")[-1]
            if not filename:
                return None
            return root / filename

        def do_HEAD(self):  # noqa: N802
            target = self._target_path()
            if target is None or not target.exists():
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            uploaded = target.stat().st_size
            self.send_response(200)
            self.send_header("Content-Length", str(uploaded))
            self.end_headers()

        def do_PUT(self):  # noqa: N802
            target = self._target_path()
            if target is None:
                self._send_json(400, {"error": "filename missing"})
                return

            length = int(self.headers.get("Content-Length", "0") or 0)
            content_range = self.headers.get("Content-Range", "")
            state["put_ranges"].append(content_range)

            start = 0
            if content_range.startswith("bytes "):
                bounds = content_range.split(" ", 1)[1].split("/", 1)[0]
                start_text, end_text = bounds.split("-", 1)
                start = int(start_text)
                end = int(end_text)
                expected = max(0, end - start + 1)
                if expected != length:
                    self._send_json(400, {"error": "length mismatch"})
                    return

            body = self.rfile.read(length)
            mode = "r+b" if target.exists() else "wb"
            with target.open(mode) as handle:
                handle.seek(start)
                if state["fail_once"] and not content_range:
                    keep = max(1, len(body) // 2)
                    handle.write(body[:keep])
                    state["fail_once"] = False
                    self._send_json(500, {"error": "simulated interruption"})
                    return
                handle.write(body)

            self._send_json(200, {"url": f"http://127.0.0.1:{self.server.server_address[1]}/d/{target.name}"})

        def log_message(self, format: str, *args):
            _ = format
            _ = args
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, state


def _start_transfer_resume_test_server(root: Path) -> tuple[ThreadingHTTPServer, dict[str, Any]]:
    state: dict[str, Any] = {
        "fail_once": True,
        "put_ranges": [],
    }

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send_text(self, status: int, payload: str) -> None:
            body = payload.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _target_path(self) -> Path | None:
            filename = unquote(self.path).strip("/")
            if not filename:
                return None
            return root / filename

        def do_HEAD(self):  # noqa: N802
            target = self._target_path()
            if target is None or not target.exists():
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            uploaded = target.stat().st_size
            self.send_response(200)
            self.send_header("Content-Length", str(uploaded))
            self.end_headers()

        def do_PUT(self):  # noqa: N802
            target = self._target_path()
            if target is None:
                self._send_text(400, "filename missing")
                return

            length = int(self.headers.get("Content-Length", "0") or 0)
            content_range = self.headers.get("Content-Range", "")
            state["put_ranges"].append(content_range)

            start = 0
            if content_range.startswith("bytes "):
                bounds = content_range.split(" ", 1)[1].split("/", 1)[0]
                start_text, end_text = bounds.split("-", 1)
                start = int(start_text)
                end = int(end_text)
                expected = max(0, end - start + 1)
                if expected != length:
                    self._send_text(400, "length mismatch")
                    return

            body = self.rfile.read(length)
            mode = "r+b" if target.exists() else "wb"
            with target.open(mode) as handle:
                handle.seek(start)
                if state["fail_once"] and not content_range:
                    keep = max(1, len(body) // 2)
                    handle.write(body[:keep])
                    state["fail_once"] = False
                    self._send_text(500, "simulated interruption")
                    return
                handle.write(body)

            url = f"http://127.0.0.1:{self.server.server_address[1]}/{target.name}"
            self._send_text(200, url)

        def log_message(self, format: str, *args):
            _ = format
            _ = args
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, state


def test_upload_manager_resume_on_retry_uses_content_range(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("surfload.core.time.sleep", lambda _seconds: None)

    source = tmp_path / "payload.bin"
    payload = (b"abc123xyz" * 65536) + b"tail"
    source.write_bytes(payload)

    server_root = tmp_path / "server"
    server_root.mkdir()
    server, state = _start_resume_test_server(server_root)

    try:
        port = int(server.server_address[1])
        config = {
            "timeout": 30,
            "retries": 2,
            "backoff_base_seconds": 1,
            "host_defaults": {
                "dummy_local": {
                    "upload_url": f"http://127.0.0.1:{port}/upload",
                }
            },
        }

        store = CredentialStore(path=tmp_path / "credentials.enc", backend="file")
        logger = logging.getLogger("surfload_test_resume")
        manager = UploadManager(config=config, credential_store=store, logger=logger)

        results = manager.upload(
            files=[source],
            hosts=["dummy_local"],
            parallelism=1,
            chunk_size=64 * 1024,
            retries=2,
            backoff_base_seconds=1,
            backoff_max_seconds=1,
            show_progress=False,
            resume_on_retry=True,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert len(results) == 1
    result = results[0]
    assert result.success is True
    assert result.attempts == 2

    uploaded = (server_root / source.name).read_bytes()
    assert uploaded == payload

    assert len(state["put_ranges"]) == 2
    assert state["put_ranges"][0] in {"", None}

    range_header = str(state["put_ranges"][1])
    assert range_header.startswith("bytes ")
    start = int(range_header.split(" ", 1)[1].split("-", 1)[0])
    assert 0 < start < len(payload)


def test_gofile_resume_on_retry_uses_content_range(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("surfload.core.time.sleep", lambda _seconds: None)

    source = tmp_path / "payload_gofile.bin"
    payload = (b"gofile-retry-" * 32768) + b"done"
    source.write_bytes(payload)

    server_root = tmp_path / "server_gofile"
    server_root.mkdir()
    server, state = _start_gofile_resume_test_server(server_root)

    try:
        port = int(server.server_address[1])
        config = {
            "timeout": 30,
            "retries": 2,
            "backoff_base_seconds": 1,
            "host_defaults": {
                "gofile": {
                    "upload_url": "",
                    "server_api_url": f"http://127.0.0.1:{port}/getServer",
                    "upload_path": "/uploadFile",
                    "folder_id": "",
                    "use_bearer_auth": False,
                    "enable_resume": True,
                    "resume_probe_url_template": f"http://127.0.0.1:{port}/resume/{{filename_quoted}}",
                }
            },
        }

        store = CredentialStore(path=tmp_path / "credentials_gofile.enc", backend="file")
        logger = logging.getLogger("surfload_test_gofile_resume")
        manager = UploadManager(config=config, credential_store=store, logger=logger)

        results = manager.upload(
            files=[source],
            hosts=["gofile"],
            parallelism=1,
            chunk_size=64 * 1024,
            retries=2,
            backoff_base_seconds=1,
            backoff_max_seconds=1,
            show_progress=False,
            resume_on_retry=True,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert len(results) == 1
    result = results[0]
    assert result.success is True
    assert result.attempts == 2

    uploaded = (server_root / source.name).read_bytes()
    assert uploaded == payload

    assert len(state["post_ranges"]) == 2
    assert state["post_ranges"][0] in {"", None}

    range_header = str(state["post_ranges"][1])
    assert range_header.startswith("bytes ")
    start = int(range_header.split(" ", 1)[1].split("-", 1)[0])
    assert 0 < start < len(payload)


def test_buzzheavier_resume_on_retry_uses_content_range(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("surfload.core.time.sleep", lambda _seconds: None)

    source = tmp_path / "payload_buzz.bin"
    payload = (b"buzz-retry-" * 32768) + b"done"
    source.write_bytes(payload)

    server_root = tmp_path / "server_buzz"
    server_root.mkdir()
    server, state = _start_buzzheavier_resume_test_server(server_root)

    try:
        port = int(server.server_address[1])
        config = {
            "timeout": 30,
            "retries": 2,
            "backoff_base_seconds": 1,
            "host_defaults": {
                "buzzheavier": {
                    "upload_base_url": f"http://127.0.0.1:{port}",
                    "parent_id": "",
                    "location_id": "",
                    "note": "",
                    "enable_resume": True,
                }
            },
        }

        store = CredentialStore(path=tmp_path / "credentials_buzz.enc", backend="file")
        logger = logging.getLogger("surfload_test_buzz_resume")
        manager = UploadManager(config=config, credential_store=store, logger=logger)

        results = manager.upload(
            files=[source],
            hosts=["buzzheavier"],
            parallelism=1,
            chunk_size=64 * 1024,
            retries=2,
            backoff_base_seconds=1,
            backoff_max_seconds=1,
            show_progress=False,
            resume_on_retry=True,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert len(results) == 1
    result = results[0]
    assert result.success is True
    assert result.attempts == 2

    uploaded = (server_root / source.name).read_bytes()
    assert uploaded == payload

    assert len(state["put_ranges"]) == 2
    assert state["put_ranges"][0] in {"", None}

    range_header = str(state["put_ranges"][1])
    assert range_header.startswith("bytes ")
    start = int(range_header.split(" ", 1)[1].split("-", 1)[0])
    assert 0 < start < len(payload)


def test_upload_manager_retry_without_resume_reuploads_full_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("surfload.core.time.sleep", lambda _seconds: None)

    source = tmp_path / "payload_no_resume.bin"
    payload = (b"resume-off-" * 32768) + b"end"
    source.write_bytes(payload)

    server_root = tmp_path / "server_no_resume"
    server_root.mkdir()
    server, state = _start_resume_test_server(server_root)

    try:
        port = int(server.server_address[1])
        config = {
            "timeout": 30,
            "retries": 2,
            "backoff_base_seconds": 1,
            "host_defaults": {
                "dummy_local": {
                    "upload_url": f"http://127.0.0.1:{port}/upload",
                }
            },
        }

        store = CredentialStore(path=tmp_path / "credentials.enc", backend="file")
        logger = logging.getLogger("surfload_test_no_resume")
        manager = UploadManager(config=config, credential_store=store, logger=logger)

        results = manager.upload(
            files=[source],
            hosts=["dummy_local"],
            parallelism=1,
            chunk_size=64 * 1024,
            retries=2,
            backoff_base_seconds=1,
            backoff_max_seconds=1,
            show_progress=False,
            resume_on_retry=False,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert len(results) == 1
    result = results[0]
    assert result.success is True
    assert result.attempts == 2

    uploaded = (server_root / source.name).read_bytes()
    assert uploaded == payload

    assert len(state["put_ranges"]) == 2
    assert str(state["put_ranges"][0]) in {"", "None"}
    assert str(state["put_ranges"][1]) in {"", "None"}


def test_transfer_sh_resume_on_retry_uses_content_range(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("surfload.core.time.sleep", lambda _seconds: None)

    source = tmp_path / "payload_transfer.bin"
    payload = (b"transfer-retry-" * 32768) + b"done"
    source.write_bytes(payload)

    server_root = tmp_path / "server_transfer"
    server_root.mkdir()
    server, state = _start_transfer_resume_test_server(server_root)

    try:
        port = int(server.server_address[1])
        config = {
            "timeout": 30,
            "retries": 2,
            "backoff_base_seconds": 1,
            "host_defaults": {
                "transfer_sh": {
                    "upload_url": f"http://127.0.0.1:{port}",
                    "max_days": 0,
                    "max_downloads": 0,
                    "enable_resume": True,
                }
            },
        }

        store = CredentialStore(path=tmp_path / "credentials_transfer.enc", backend="file")
        logger = logging.getLogger("surfload_test_transfer_resume")
        manager = UploadManager(config=config, credential_store=store, logger=logger)

        results = manager.upload(
            files=[source],
            hosts=["transfer_sh"],
            parallelism=1,
            chunk_size=64 * 1024,
            retries=2,
            backoff_base_seconds=1,
            backoff_max_seconds=1,
            show_progress=False,
            resume_on_retry=True,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert len(results) == 1
    result = results[0]
    assert result.success is True
    assert result.attempts == 2

    uploaded = (server_root / source.name).read_bytes()
    assert uploaded == payload

    assert len(state["put_ranges"]) == 2
    assert state["put_ranges"][0] in {"", None}

    range_header = str(state["put_ranges"][1])
    assert range_header.startswith("bytes ")
    start = int(range_header.split(" ", 1)[1].split("-", 1)[0])
    assert 0 < start < len(payload)
