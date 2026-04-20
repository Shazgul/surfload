#!/usr/bin/env python3
"""CLI-Upload-Tool fuer Ubuntu/Linux mit mehreren Hostern.

Unterstuetzte Hoster (Stand jetzt):
- MultiUp
- VikingFile
- BuzzHeavier
- ZeroFS
- Rootz
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from uuid import uuid4

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "shazuploader" / "config.json"
DEFAULT_TXT_OUTPUT_PATH = Path("upload-results.txt")
ROOTZ_SIMPLE_UPLOAD_LIMIT_BYTES = 4 * 1024 * 1024
PRINT_LOCK = threading.Lock()
BYTE_PROGRESS_ENABLED = True
HTTP_DEBUG_ENABLED = False

HOST_LABELS = {
    "multiup": "MultiUp",
    "vikingfile": "VikingFile",
    "buzzheavier": "BuzzHeavier",
    "zerofs": "ZeroFS",
    "rootz": "Rootz",
}


DEFAULT_CONFIG = {
    "multiup": {
        "username": "",
        "password": "",
        "target_hosts": [],
        "timeout": 600,
    },
    "vikingfile": {
        "user_hash": "",
        "path": "",
        "path_public_share": "",
        "timeout": 600,
    },
    "buzzheavier": {
        "token": "",
        "parent_id": "",
        "location_id": "",
        "note": "",
        "timeout": 600,
    },
    "zerofs": {
        "api_url": "https://zerofs.link/api",
        "bucket_code": "eu",
        "token": "",
        "folder_id": "",
        "note": "",
        "content_type": "application/octet-stream",
        "timeout": 900,
    },
    "rootz": {
        "api_base_url": "https://rootz.so/api",
        "api_key": "",
        "folder_id": "",
        "multipart_enabled": True,
        "multipart_threshold_mb": 4,
        "multipart_chunk_mb": 8,
        "multipart_initiate_endpoint": "/files/multipart/init",
        "multipart_batch_urls_endpoint": "/files/multipart/batch-urls",
        "multipart_complete_endpoint": "/files/multipart/complete",
        "multipart_abort_endpoint": "/files/multipart/abort",
        "multipart_fallback_to_simple": True,
        "multipart_parallelism": 0,
        "multipart_part_retries": 3,
        "timeout": 600,
    },
}

ENV_CONFIG_MAP = {
    ("multiup", "username"): ["SHAZUPLOADER_MULTIUP_USERNAME", "MULTIUP_USERNAME"],
    ("multiup", "password"): ["SHAZUPLOADER_MULTIUP_PASSWORD", "MULTIUP_PASSWORD"],
    ("vikingfile", "user_hash"): ["SHAZUPLOADER_VIKINGFILE_USER_HASH", "VIKINGFILE_USER_HASH"],
    ("buzzheavier", "token"): ["SHAZUPLOADER_BUZZHEAVIER_TOKEN", "BUZZHEAVIER_TOKEN"],
    ("zerofs", "token"): ["SHAZUPLOADER_ZEROFS_TOKEN", "ZEROFS_TOKEN"],
    ("rootz", "api_key"): ["SHAZUPLOADER_ROOTZ_API_KEY", "ROOTZ_API_KEY"],
}


def safe_print(message: str) -> None:
    with PRINT_LOCK:
        print(message)


def to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    return default


def create_retry_session(total_retries: int = 3, backoff_factor: float = 1.0) -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=total_retries,
        connect=total_retries,
        read=total_retries,
        status=total_retries,
        allowed_methods=frozenset(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]),
        status_forcelist=[408, 425, 429, 500, 502, 503, 504],
        backoff_factor=backoff_factor,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


class ByteProgressTracker:
    def __init__(self, host_key: str, file_path: str, total_bytes: int, step_percent: int = 10) -> None:
        self.host_key = host_key
        self.file_name = Path(file_path).name
        self.total_bytes = max(total_bytes, 0)
        self.step_percent = max(1, min(step_percent, 50))
        self.uploaded_bytes = 0
        self.next_percent = self.step_percent

    def update(self, delta_bytes: int) -> None:
        if not BYTE_PROGRESS_ENABLED or self.total_bytes <= 0 or delta_bytes <= 0:
            return

        self.uploaded_bytes = min(self.total_bytes, self.uploaded_bytes + delta_bytes)
        percent = int((self.uploaded_bytes * 100) / self.total_bytes)

        if percent >= self.next_percent or self.uploaded_bytes >= self.total_bytes:
            host_label = HOST_LABELS.get(self.host_key, self.host_key)
            safe_print(f"[BYTE {percent:3d}%] [{host_label}] {self.file_name}")
            while self.next_percent <= percent:
                self.next_percent += self.step_percent


class ProgressFile:
    def __init__(self, file_handle: Any, tracker: ByteProgressTracker) -> None:
        self.file_handle = file_handle
        self.tracker = tracker

    def read(self, size: int = -1) -> bytes:
        chunk = self.file_handle.read(size)
        if chunk:
            self.tracker.update(len(chunk))
        return chunk

    def __getattr__(self, name: str) -> Any:
        return getattr(self.file_handle, name)

    def __enter__(self) -> "ProgressFile":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.file_handle.close()


class MultipartStream:
    def __init__(
        self,
        file_path: str,
        file_field_name: str,
        filename: str,
        mime_type: str,
        form_data: Dict[str, Any],
        tracker: ByteProgressTracker,
        boundary: str,
        chunk_size: int = 1024 * 1024,
    ) -> None:
        self.file_path = file_path
        self.tracker = tracker
        self.chunk_size = max(64 * 1024, chunk_size)

        self.form_chunks: List[bytes] = []
        for key, value in form_data.items():
            if value is None:
                continue
            self.form_chunks.append(
                (
                    f"--{boundary}\r\n"
                    f"Content-Disposition: form-data; name=\"{key}\"\r\n\r\n"
                    f"{value}\r\n"
                ).encode("utf-8")
            )

        self.file_header = (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"{file_field_name}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
        self.closing = f"\r\n--{boundary}--\r\n".encode("utf-8")

        file_size = os.path.getsize(file_path)
        self.content_length = sum(len(chunk) for chunk in self.form_chunks) + len(self.file_header) + file_size + len(self.closing)

    def __len__(self) -> int:
        return self.content_length

    def __iter__(self) -> Any:
        for chunk in self.form_chunks:
            yield chunk

        yield self.file_header
        with open(self.file_path, "rb") as file_handle:
            while True:
                data_chunk = file_handle.read(self.chunk_size)
                if not data_chunk:
                    break
                self.tracker.update(len(data_chunk))
                yield data_chunk

        yield self.closing


class UploadError(Exception):
    """Fachlicher Upload-Fehler."""


@dataclass
class UploadResult:
    host: str
    file_path: str
    success: bool
    download_url: Optional[str] = None
    error: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None
    duration_seconds: Optional[float] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class BaseUploader:
    host_key = "base"

    def __init__(self, host_config: Optional[Dict[str, Any]] = None) -> None:
        self.host_config = host_config or {}
        self.timeout = int(self.host_config.get("timeout", 600))
        retries = int(self.host_config.get("retries", 3) or 3)
        backoff_factor = float(self.host_config.get("backoff_factor", 1.0) or 1.0)
        self.session = create_retry_session(total_retries=max(0, retries), backoff_factor=max(0.0, backoff_factor))
        # Upload-Requests mit Body werden absichtlich ohne automatische Retries gesendet,
        # um Re-Uploads und hohen RAM-Verbrauch bei Fehlern zu vermeiden.
        self.upload_session = create_retry_session(total_retries=0, backoff_factor=0.0)

    def upload(self, file_path: str) -> UploadResult:
        raise NotImplementedError

    @staticmethod
    def _json_or_error(response: requests.Response) -> Dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            preview = response.text[:400]
            raise UploadError(f"Antwort ist kein JSON (HTTP {response.status_code}): {preview}") from exc

        if response.status_code >= 400:
            raise UploadError(f"HTTP {response.status_code}: {data}")
        return data

    def _request(self, method: str, url: str, timeout: Optional[int] = None, **kwargs: Any) -> requests.Response:
        return self._request_with_session(self.session, method, url, timeout=timeout, **kwargs)

    def _upload_request(self, method: str, url: str, timeout: Optional[int] = None, **kwargs: Any) -> requests.Response:
        return self._request_with_session(self.upload_session, method, url, timeout=timeout, **kwargs)

    def _request_with_session(
        self,
        session: requests.Session,
        method: str,
        url: str,
        timeout: Optional[int] = None,
        **kwargs: Any,
    ) -> requests.Response:
        host_label = HOST_LABELS.get(self.host_key, self.host_key)
        upper_method = method.upper()

        if HTTP_DEBUG_ENABLED:
            safe_print(f"[HTTP DEBUG] [{host_label}] -> {upper_method} {url}")

        try:
            response = session.request(method=method, url=url, timeout=timeout or self.timeout, **kwargs)
        except Exception as exc:
            if HTTP_DEBUG_ENABLED:
                safe_print(f"[HTTP DEBUG] [{host_label}] XX {upper_method} {url}: {exc.__class__.__name__}: {exc}")
            raise

        if HTTP_DEBUG_ENABLED:
            safe_print(f"[HTTP DEBUG] [{host_label}] <- {response.status_code} {upper_method} {url}")
            if response.status_code >= 400:
                preview = response.text[:500].replace("\n", "\\n")
                safe_print(f"[HTTP DEBUG] [{host_label}] body: {preview}")

        return response

    def _upload_bytes(self, method: str, url: str, file_path: str, timeout: Optional[int] = None, **kwargs: Any) -> requests.Response:
        file_size = os.path.getsize(file_path)
        tracker = ByteProgressTracker(self.host_key, file_path, file_size)
        with open(file_path, "rb") as file_handle:
            stream = ProgressFile(file_handle, tracker)
            return self._upload_request(method, url, timeout=timeout, data=stream, **kwargs)

    def _upload_multipart_file(
        self,
        url: str,
        file_field_name: str,
        file_path: str,
        mime_type: str,
        timeout: Optional[int] = None,
        **kwargs: Any,
    ) -> requests.Response:
        file_size = os.path.getsize(file_path)
        tracker = ByteProgressTracker(self.host_key, file_path, file_size)
        filename = Path(file_path).name

        form_data = kwargs.pop("data", None)
        if form_data is None:
            form_data = {}
        if not isinstance(form_data, dict):
            raise UploadError("multipart form data muss ein Dictionary sein")

        request_headers = kwargs.pop("headers", None) or {}
        headers = dict(request_headers)

        boundary = f"----ShazUploaderBoundary{uuid4().hex}"

        multipart_stream = MultipartStream(
            file_path=file_path,
            file_field_name=file_field_name,
            filename=filename,
            mime_type=mime_type,
            form_data=form_data,
            tracker=tracker,
            boundary=boundary,
        )

        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        headers.pop("Content-Length", None)
        headers.pop("Transfer-Encoding", None)

        return self._upload_request("POST", url, timeout=timeout, data=multipart_stream, headers=headers, **kwargs)


class BuzzHeavierUploader(BaseUploader):
    host_key = "buzzheavier"

    def upload(self, file_path: str) -> UploadResult:
        filename = Path(file_path).name
        token = self.host_config.get("token", "").strip()
        parent_id = self.host_config.get("parent_id", "").strip()
        location_id = self.host_config.get("location_id", "").strip()
        note = self.host_config.get("note", "").strip()

        if parent_id:
            upload_url = f"https://w.buzzheavier.com/{quote(parent_id, safe='')}/{quote(filename, safe='')}"
        else:
            upload_url = f"https://w.buzzheavier.com/{quote(filename, safe='')}"

        params: Dict[str, str] = {}
        if location_id:
            params["locationId"] = location_id
        if note:
            params["note"] = base64.b64encode(note.encode("utf-8")).decode("ascii")

        headers: Dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        response = self._upload_bytes(
            "PUT",
            upload_url,
            file_path,
            params=params,
            headers=headers,
            timeout=self.timeout,
        )

        data = self._json_or_error(response)
        download_url = self._extract_download_url(data)
        if not download_url:
            raise UploadError(f"Download-Link nicht in Antwort gefunden: {data}")

        return UploadResult(
            host=self.host_key,
            file_path=file_path,
            success=True,
            download_url=download_url,
            raw_response=data,
        )

    @staticmethod
    def _extract_download_url(data: Dict[str, Any]) -> Optional[str]:
        for key in ("url", "download_url", "downloadUrl", "link"):
            value = data.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value

        payload = data.get("data")
        if isinstance(payload, dict):
            for key in ("url", "download_url", "downloadUrl", "link"):
                value = payload.get(key)
                if isinstance(value, str) and value.startswith("http"):
                    return value

            file_id = payload.get("id")
            if isinstance(file_id, str) and file_id:
                return f"https://buzzheavier.com/{file_id}"

        file_id = data.get("id")
        if isinstance(file_id, str) and file_id:
            return f"https://buzzheavier.com/{file_id}"

        return None


class VikingFileUploader(BaseUploader):
    host_key = "vikingfile"

    def upload(self, file_path: str) -> UploadResult:
        server_response = self._request("GET", "https://vikingfile.com/api/get-server", timeout=30)
        server_json = self._json_or_error(server_response)
        upload_server = server_json.get("server")
        if not upload_server:
            raise UploadError(f"VikingFile Upload-Server fehlt in Antwort: {server_json}")

        form_data: Dict[str, str] = {
            "user": self.host_config.get("user_hash", "") or "",
        }

        optional_path = self.host_config.get("path", "")
        optional_public_share = self.host_config.get("path_public_share", "")
        if optional_path:
            form_data["path"] = str(optional_path)
        if optional_public_share:
            form_data["pathPublicShare"] = str(optional_public_share)

        filename = Path(file_path).name
        mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

        response = self._upload_multipart_file(
            upload_server,
            "file",
            file_path,
            mime_type,
            data=form_data,
            timeout=self.timeout,
        )

        data = self._json_or_error(response)
        download_url = data.get("url")
        if not download_url and data.get("hash"):
            download_url = f"https://vikingfile.com/f/{data['hash']}"

        if not download_url:
            raise UploadError(f"Download-Link nicht in Antwort gefunden: {data}")

        return UploadResult(
            host=self.host_key,
            file_path=file_path,
            success=True,
            download_url=download_url,
            raw_response=data,
        )


class MultiUpUploader(BaseUploader):
    host_key = "multiup"

    def upload(self, file_path: str) -> UploadResult:
        api_base = "https://multiup.io/api"
        filename = Path(file_path).name
        file_size = os.path.getsize(file_path)
        mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

        user_id = self._login_if_needed(api_base)
        upload_server = self._get_fastest_server(api_base, file_size)

        payload: Dict[str, str] = {}
        if user_id:
            payload["user"] = user_id

        configured_hosts = self.host_config.get("target_hosts", [])
        if isinstance(configured_hosts, list):
            for index, host_name in enumerate(configured_hosts, start=1):
                payload[f"host{index}"] = str(host_name)

        response = self._upload_multipart_file(
            upload_server,
            "files[]",
            file_path,
            mime_type,
            data=payload,
            timeout=self.timeout,
        )

        data = self._json_or_error(response)
        download_url = self._extract_download_url(data)
        if not download_url:
            raise UploadError(f"Download-Link nicht in Antwort gefunden: {data}")

        return UploadResult(
            host=self.host_key,
            file_path=file_path,
            success=True,
            download_url=download_url,
            raw_response=data,
        )

    def _login_if_needed(self, api_base: str) -> str:
        username = str(self.host_config.get("username", "")).strip()
        password = str(self.host_config.get("password", "")).strip()
        if not username or not password:
            return ""

        response = self._request(
            "POST",
            f"{api_base}/login",
            data={"username": username, "password": password},
            timeout=30,
        )
        data = self._json_or_error(response)
        user_id = data.get("user")
        if not user_id:
            raise UploadError(f"MultiUp-Login fehlgeschlagen: {data}")
        return str(user_id)

    def _get_fastest_server(self, api_base: str, file_size: int) -> str:
        response = self._request(
            "GET",
            f"{api_base}/get-fastest-server",
            params={"size": file_size},
            timeout=30,
        )

        if response.status_code >= 400:
            response = self._request("GET", f"{api_base}/get-fastest-server", timeout=30)

        data = self._json_or_error(response)
        server = data.get("server")
        if not server:
            raise UploadError(f"MultiUp lieferte keinen Upload-Server: {data}")
        return str(server)

    @staticmethod
    def _extract_download_url(data: Dict[str, Any]) -> Optional[str]:
        for key in ("url", "download_url", "downloadUrl", "link"):
            value = data.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value

        links = data.get("links")
        if isinstance(links, list) and links:
            first = links[0]
            if isinstance(first, str) and first.startswith("http"):
                return first
            if isinstance(first, dict):
                for key in ("url", "download_url", "link"):
                    value = first.get(key)
                    if isinstance(value, str) and value.startswith("http"):
                        return value
        return None


class ZeroFSUploader(BaseUploader):
    host_key = "zerofs"

    def upload(self, file_path: str) -> UploadResult:
        api_base = str(self.host_config.get("api_url", "https://zerofs.link/api")).rstrip("/")
        token = str(self.host_config.get("token", "")).strip() or None
        note = str(self.host_config.get("note", "")).strip()
        folder_id = str(self.host_config.get("folder_id", "")).strip() or None
        bucket_code = str(self.host_config.get("bucket_code", "eu")).strip() or "eu"
        content_type = str(self.host_config.get("content_type", "application/octet-stream"))

        file_size = os.path.getsize(file_path)
        filename = Path(file_path).name

        initiate_payload: Dict[str, Any] = {
            "filename": filename,
            "file_size": file_size,
            "bucket_code": bucket_code,
            "content_type": content_type,
            "note": base64.b64encode(note.encode("utf-8")).decode("ascii") if note else "",
            "token": token,
            "folder_id": folder_id,
        }

        metadata = self._api_post(api_base, "initiate-upload/", initiate_payload, timeout=60)
        upload_type = metadata.get("upload_type", "single")
        completion_token = metadata.get("completion_token")

        try:
            if upload_type == "single":
                self._single_upload(file_path, metadata)
                complete_data = self._api_post(
                    api_base,
                    "complete-single-upload/",
                    {"completion_token": completion_token, "token": token},
                    timeout=60,
                )
            elif upload_type == "multipart":
                parts = self._multipart_upload(file_path, metadata)
                complete_data = self._api_post(
                    api_base,
                    "complete-multipart-upload/",
                    {"completion_token": completion_token, "parts": parts, "token": token},
                    timeout=120,
                )
            else:
                raise UploadError(f"Unbekannter ZeroFS upload_type: {upload_type}")
        except Exception:
            if completion_token:
                self._api_post(
                    api_base,
                    "abort-multipart-upload/",
                    {"completion_token": completion_token, "token": token},
                    timeout=30,
                    allow_failure=True,
                )
            raise

        download_url = (
            complete_data.get("download_url")
            or metadata.get("download_url")
            or complete_data.get("url")
            or metadata.get("url")
        )

        file_id = complete_data.get("file_id") or metadata.get("file_id")
        if not download_url and file_id:
            public_base = api_base.rsplit("/", 1)[0]
            download_url = f"{public_base}/f/{file_id}/"

        if not download_url:
            raise UploadError(f"Download-Link nicht in ZeroFS-Antwort gefunden: {complete_data}")

        return UploadResult(
            host=self.host_key,
            file_path=file_path,
            success=True,
            download_url=str(download_url),
            raw_response=complete_data,
        )

    def _single_upload(self, file_path: str, metadata: Dict[str, Any]) -> None:
        upload_url = metadata.get("url")
        if not upload_url:
            raise UploadError(f"ZeroFS single-upload URL fehlt: {metadata}")

        headers = {
            key: value
            for key, value in (metadata.get("headers") or {}).items()
            if value is not None
        }

        response = self._upload_bytes("PUT", upload_url, file_path, headers=headers, timeout=self.timeout)

        if response.status_code >= 400:
            raise UploadError(f"ZeroFS single upload fehlgeschlagen (HTTP {response.status_code}): {response.text[:300]}")

    def _multipart_upload(self, file_path: str, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        chunk_size = int(metadata.get("chunk_size") or 0)
        part_urls = metadata.get("part_urls") or []

        if not chunk_size or not isinstance(part_urls, list):
            raise UploadError(f"ZeroFS multipart Metadaten unvollstaendig: {metadata}")

        sse_headers = {
            "x-amz-server-side-encryption-customer-algorithm": metadata.get("ssec_algorithm", "AES256"),
            "x-amz-server-side-encryption-customer-key": metadata.get("ssec_key"),
            "x-amz-server-side-encryption-customer-key-md5": metadata.get("ssec_key_md5"),
        }
        sse_headers = {key: value for key, value in sse_headers.items() if value}

        file_size = os.path.getsize(file_path)
        uploaded_parts: List[Dict[str, Any]] = []
        tracker = ByteProgressTracker(self.host_key, file_path, file_size)

        with open(file_path, "rb") as file_handle:
            for part_info in part_urls:
                part_number = int(part_info["part_number"])
                upload_url = str(part_info["url"])

                start = (part_number - 1) * chunk_size
                end = min(start + chunk_size, file_size)
                size = end - start

                file_handle.seek(start)
                chunk = file_handle.read(size)

                headers = {"Content-Length": str(size), **sse_headers}
                response = self._request("PUT", upload_url, data=chunk, headers=headers, timeout=self.timeout)
                if response.status_code >= 400:
                    raise UploadError(
                        f"ZeroFS Part {part_number} fehlgeschlagen (HTTP {response.status_code}): {response.text[:200]}"
                    )

                etag = response.headers.get("ETag", "").strip('"')
                uploaded_parts.append({"part_number": part_number, "etag": etag})
                tracker.update(size)

        uploaded_parts.sort(key=lambda item: item["part_number"])
        return uploaded_parts

    def _api_post(
        self,
        api_base: str,
        endpoint: str,
        payload: Dict[str, Any],
        timeout: int,
        allow_failure: bool = False,
    ) -> Dict[str, Any]:
        response = self._request("POST", f"{api_base}/{endpoint}", json=payload, timeout=timeout)
        if allow_failure and response.status_code >= 400:
            return {"error": response.text}
        return self._json_or_error(response)


class RootzUploader(BaseUploader):
    host_key = "rootz"

    def upload(self, file_path: str) -> UploadResult:
        api_base = str(self.host_config.get("api_base_url", "https://rootz.so/api")).rstrip("/")
        api_key = str(self.host_config.get("api_key", "")).strip()
        folder_id = str(self.host_config.get("folder_id", "")).strip()
        file_size = os.path.getsize(file_path)

        multipart_enabled = to_bool(self.host_config.get("multipart_enabled", True), True)
        multipart_fallback = to_bool(self.host_config.get("multipart_fallback_to_simple", True), True)
        multipart_threshold_mb = float(self.host_config.get("multipart_threshold_mb", 4) or 4)
        multipart_chunk_mb = float(self.host_config.get("multipart_chunk_mb", 8) or 8)
        multipart_threshold_bytes = max(1, int(multipart_threshold_mb * 1024 * 1024))
        chunk_size = max(1024 * 1024, int(multipart_chunk_mb * 1024 * 1024))

        headers = self._build_headers(api_key)

        try:
            if multipart_enabled and file_size > multipart_threshold_bytes:
                return self._multipart_upload(
                    api_base=api_base,
                    file_path=file_path,
                    folder_id=folder_id,
                    headers=headers,
                    chunk_size=chunk_size,
                )
        except Exception as exc:
            if multipart_fallback:
                safe_print(f"WARN: Rootz multipart fehlgeschlagen, fallback auf einfachen Upload: {exc}")
            else:
                raise

        if file_size > ROOTZ_SIMPLE_UPLOAD_LIMIT_BYTES:
            safe_print(
                "WARN: Rootz-Datei ist groesser als 4MB. Falls einfacher Upload fehlschlaegt, "
                "bitte konkrete Multipart-Endpunkte in der Config setzen."
            )

        return self._simple_upload(api_base=api_base, file_path=file_path, folder_id=folder_id, headers=headers)

    def _build_headers(self, api_key: str) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if api_key:
            if api_key.lower().startswith("bearer "):
                headers["Authorization"] = api_key
            else:
                headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _simple_upload(self, api_base: str, file_path: str, folder_id: str, headers: Dict[str, str]) -> UploadResult:
        mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        form_data: Dict[str, str] = {}
        if folder_id:
            form_data["folderId"] = folder_id

        response = self._upload_multipart_file(
            f"{api_base}/files/upload",
            "file",
            file_path,
            mime_type,
            headers=headers,
            data=form_data,
            timeout=self.timeout,
        )

        data = self._json_or_error(response)
        return self._result_from_rootz_payload(file_path, data)

    def _multipart_upload(
        self,
        api_base: str,
        file_path: str,
        folder_id: str,
        headers: Dict[str, str],
        chunk_size: int,
    ) -> UploadResult:
        filename = Path(file_path).name
        file_size = os.path.getsize(file_path)
        mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

        initiate_endpoint = str(self.host_config.get("multipart_initiate_endpoint", "/files/multipart/init"))
        batch_urls_endpoint = str(self.host_config.get("multipart_batch_urls_endpoint", "/files/multipart/batch-urls"))
        complete_endpoint = str(self.host_config.get("multipart_complete_endpoint", "/files/multipart/complete"))
        abort_endpoint = str(self.host_config.get("multipart_abort_endpoint", "/files/multipart/abort"))

        initiate_payload: Dict[str, Any] = {
            "fileName": filename,
            "fileSize": file_size,
            "fileType": mime_type,
            "chunkSize": chunk_size,
        }
        if folder_id:
            initiate_payload["folderId"] = folder_id

        initiate_response = self._request(
            "POST",
            f"{api_base}{initiate_endpoint}",
            json=initiate_payload,
            headers=headers,
            timeout=60,
        )
        initiate_data = self._json_or_error(initiate_response)
        payload = self._extract_rootz_payload(initiate_data)

        upload_id = payload.get("uploadId") or payload.get("id") or payload.get("multipartId")
        if not upload_id:
            raise UploadError(f"Rootz multipart: uploadId fehlt in Antwort: {initiate_data}")

        key = payload.get("key")
        server_chunk_size = int(payload.get("chunkSize") or chunk_size)
        total_parts = int(payload.get("totalParts") or ((file_size + server_chunk_size - 1) // server_chunk_size))

        part_url_map = self._get_part_url_map(
            api_base=api_base,
            batch_urls_endpoint=batch_urls_endpoint,
            headers=headers,
            payload=payload,
            key=key,
            upload_id=str(upload_id),
            total_parts=total_parts,
        )

        if not part_url_map:
            raise UploadError(f"Rootz multipart: part URLs fehlen in Antwort: {initiate_data}")

        desired_parallelism = int(self.host_config.get("multipart_parallelism", 0) or 0)
        parallelism = desired_parallelism if desired_parallelism > 0 else self._optimal_parallelism(file_size)
        parallelism = max(1, min(parallelism, len(part_url_map)))

        safe_print(
            f"[ROOTZ MP] {filename}: {len(part_url_map)} parts x {server_chunk_size / (1024 ** 2):.1f} MB | {parallelism}x parallel"
        )

        uploaded_parts: List[Dict[str, Any]] = []
        tracker = ByteProgressTracker(self.host_key, file_path, file_size, step_percent=5)
        upload_started = monotonic()
        uploaded_bytes = 0
        completed_parts = 0

        def upload_part(part_number: int, part_url: str) -> Dict[str, Any]:
            start = (part_number - 1) * server_chunk_size
            size = min(server_chunk_size, file_size - start)
            with open(file_path, "rb") as file_handle:
                file_handle.seek(start)
                chunk = file_handle.read(size)

            etag = self._upload_part_with_retry(part_number=part_number, part_url=part_url, data_chunk=chunk)
            return {
                "partNumber": part_number,
                "part_number": part_number,
                "etag": etag,
                "ETag": etag,
                "_size": size,
            }

        try:
            with ThreadPoolExecutor(max_workers=parallelism) as executor:
                futures = {
                    executor.submit(upload_part, part_number, part_url): part_number
                    for part_number, part_url in sorted(part_url_map.items())
                }

                for future in as_completed(futures):
                    part_number = futures[future]
                    try:
                        part_result = future.result()
                    except Exception as exc:
                        raise UploadError(f"Rootz multipart: Part {part_number} fehlgeschlagen: {exc}") from exc

                    size = int(part_result.pop("_size", 0))
                    uploaded_bytes += size
                    completed_parts += 1
                    tracker.update(size)
                    uploaded_parts.append(part_result)

                    elapsed = max(monotonic() - upload_started, 0.001)
                    speed_mb = (uploaded_bytes / elapsed) / (1024 ** 2)
                    progress = (uploaded_bytes / file_size) * 100 if file_size else 100.0
                    eta_seconds = ((file_size - uploaded_bytes) / (uploaded_bytes / elapsed)) if uploaded_bytes > 0 else 0
                    safe_print(
                        f"[ROOTZ MP] {filename}: {progress:5.1f}% | {completed_parts}/{total_parts} parts | "
                        f"{speed_mb:.1f} MB/s | ETA {eta_seconds:.0f}s"
                    )
        except Exception:
            self._try_abort_multipart(
                api_base=api_base,
                abort_endpoint=abort_endpoint,
                headers=headers,
                upload_id=str(upload_id),
                key=str(key or ""),
            )
            raise

        uploaded_parts.sort(key=lambda item: int(item.get("partNumber") or item.get("part_number") or 0))

        complete_payload: Dict[str, Any] = {
            "key": key,
            "uploadId": upload_id,
            "id": upload_id,
            "multipartId": upload_id,
            "parts": uploaded_parts,
            "fileName": filename,
            "filename": filename,
            "name": filename,
            "fileSize": file_size,
            "size": file_size,
            "contentType": mime_type,
            "mimeType": mime_type,
        }
        if folder_id:
            complete_payload["folderId"] = folder_id

        try:
            complete_response = self._request(
                "POST",
                f"{api_base}{complete_endpoint}",
                json=complete_payload,
                headers=headers,
                timeout=120,
            )
            complete_data = self._json_or_error(complete_response)
        except Exception:
            self._try_abort_multipart(
                api_base=api_base,
                abort_endpoint=abort_endpoint,
                headers=headers,
                upload_id=str(upload_id),
                key=str(key or ""),
            )
            raise

        return self._result_from_rootz_payload(file_path, complete_data)

    @staticmethod
    def _optimal_parallelism(file_size: int) -> int:
        if file_size > 50 * 1024 ** 3:
            return 3
        if file_size > 10 * 1024 ** 3:
            return 4
        if file_size > 1 * 1024 ** 3:
            return 5
        return 6

    def _extract_rootz_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if data.get("success") is False:
            raise UploadError(str(data.get("error") or data))

        if isinstance(data.get("data"), dict):
            return data["data"]
        if isinstance(data.get("file"), dict):
            return data["file"]
        return data if isinstance(data, dict) else {}

    def _normalize_part_urls(self, urls_obj: Any) -> Dict[int, str]:
        normalized: Dict[int, str] = {}

        if isinstance(urls_obj, dict):
            for key, value in urls_obj.items():
                try:
                    part_number = int(key)
                except (TypeError, ValueError):
                    continue
                normalized[part_number] = str(value)
            return normalized

        if isinstance(urls_obj, list):
            for index, item in enumerate(urls_obj, start=1):
                if isinstance(item, dict):
                    part_number = int(item.get("partNumber") or item.get("part_number") or index)
                    url = str(item.get("url") or item.get("signedUrl") or "")
                else:
                    part_number = index
                    url = str(item)
                if url:
                    normalized[part_number] = url
            return normalized

        return normalized

    def _get_part_url_map(
        self,
        api_base: str,
        batch_urls_endpoint: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        key: Any,
        upload_id: str,
        total_parts: int,
    ) -> Dict[int, str]:
        if key and batch_urls_endpoint:
            batch_response = self._request(
                "POST",
                f"{api_base}{batch_urls_endpoint}",
                json={"key": key, "uploadId": upload_id, "totalParts": total_parts},
                headers=headers,
                timeout=60,
            )
            batch_data = self._json_or_error(batch_response)
            if batch_data.get("success") is False:
                raise UploadError(str(batch_data.get("error") or batch_data))

            urls_obj = batch_data.get("urls")
            if urls_obj is None and isinstance(batch_data.get("data"), dict):
                urls_obj = batch_data["data"].get("urls")

            normalized = self._normalize_part_urls(urls_obj)
            if normalized:
                return normalized

        return self._normalize_part_urls(payload.get("partUrls") or payload.get("parts") or payload.get("urls"))

    def _upload_part_with_retry(self, part_number: int, part_url: str, data_chunk: bytes) -> str:
        retries = max(1, int(self.host_config.get("multipart_part_retries", 3) or 3))

        for attempt in range(retries):
            try:
                response = self._request("PUT", part_url, data=data_chunk, timeout=max(300, self.timeout))
                if response.status_code >= 400:
                    raise UploadError(f"HTTP {response.status_code}")
                return response.headers.get("ETag", "").strip('"')
            except Exception as exc:
                if attempt >= retries - 1:
                    raise UploadError(f"Part {part_number} fehlgeschlagen: {exc}") from exc

                wait_seconds = 2 ** attempt
                safe_print(f"WARN: Rootz part {part_number} retry {attempt + 1}/{retries} in {wait_seconds}s")
                sleep(wait_seconds)

        raise UploadError(f"Part {part_number} fehlgeschlagen")

    def _try_abort_multipart(
        self,
        api_base: str,
        abort_endpoint: str,
        headers: Dict[str, str],
        upload_id: str,
        key: str,
    ) -> None:
        if not abort_endpoint:
            return

        abort_payload: Dict[str, Any] = {
            "uploadId": upload_id,
            "id": upload_id,
            "multipartId": upload_id,
        }
        if key:
            abort_payload["key"] = key

        try:
            self._request(
                "POST",
                f"{api_base}{abort_endpoint}",
                json=abort_payload,
                headers=headers,
                timeout=30,
            )
        except Exception:
            pass

    def _result_from_rootz_payload(self, file_path: str, data: Dict[str, Any]) -> UploadResult:
        payload = self._extract_rootz_payload(data)
        signed_url = payload.get("url") if isinstance(payload, dict) else None
        short_id = payload.get("shortId") if isinstance(payload, dict) else None

        download_url = f"https://rootz.so/d/{short_id}" if short_id else signed_url
        if not download_url:
            raise UploadError(f"Download-Link nicht in Antwort gefunden: {data}")

        return UploadResult(
            host=self.host_key,
            file_path=file_path,
            success=True,
            download_url=str(download_url),
            raw_response=data,
        )


UPLOADER_REGISTRY = {
    "multiup": MultiUpUploader,
    "vikingfile": VikingFileUploader,
    "buzzheavier": BuzzHeavierUploader,
    "zerofs": ZeroFSUploader,
    "rootz": RootzUploader,
}


def init_config(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise SystemExit(f"Config existiert bereits: {path} (nutze --force zum Ueberschreiben)")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
    print(f"Config geschrieben: {path}")


def load_config(path: Path) -> Dict[str, Any]:
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    if not path.exists():
        return apply_env_overrides(merged)

    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Config ist kein gueltiges JSON: {path}\n{exc}") from exc

    if not isinstance(loaded, dict):
        raise SystemExit("Config muss ein JSON-Objekt sein.")

    deep_merge(merged, loaded)
    return apply_env_overrides(merged)


def deep_merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in extra.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def apply_env_overrides(config: Dict[str, Any]) -> Dict[str, Any]:
    for (host_key, field_key), env_names in ENV_CONFIG_MAP.items():
        for env_name in env_names:
            env_value = os.getenv(env_name)
            if env_value:
                config.setdefault(host_key, {})
                config[host_key][field_key] = env_value
                break
    return config


def validate_config(config: Dict[str, Any], selected_hosts: List[str]) -> List[str]:
    errors: List[str] = []

    for host in selected_hosts:
        host_cfg = config.get(host, {})
        if not isinstance(host_cfg, dict):
            errors.append(f"Host '{host}': Konfiguration muss ein Objekt sein.")
            continue

        timeout = host_cfg.get("timeout", 600)
        try:
            if int(timeout) <= 0:
                errors.append(f"Host '{host}': timeout muss > 0 sein.")
        except (TypeError, ValueError):
            errors.append(f"Host '{host}': timeout ist ungueltig ({timeout}).")

        if host == "multiup":
            target_hosts = host_cfg.get("target_hosts", [])
            if target_hosts is not None and not isinstance(target_hosts, list):
                errors.append("Host 'multiup': target_hosts muss eine Liste sein.")

        if host == "rootz":
            api_base_url = str(host_cfg.get("api_base_url", "")).strip()
            if not api_base_url.startswith("http"):
                errors.append("Host 'rootz': api_base_url muss mit http/https beginnen.")

            threshold = host_cfg.get("multipart_threshold_mb", 4)
            chunk = host_cfg.get("multipart_chunk_mb", 8)
            try:
                if float(threshold) <= 0:
                    errors.append("Host 'rootz': multipart_threshold_mb muss > 0 sein.")
            except (TypeError, ValueError):
                errors.append("Host 'rootz': multipart_threshold_mb ist ungueltig.")

            try:
                if float(chunk) <= 0:
                    errors.append("Host 'rootz': multipart_chunk_mb muss > 0 sein.")
            except (TypeError, ValueError):
                errors.append("Host 'rootz': multipart_chunk_mb ist ungueltig.")

            parallelism = host_cfg.get("multipart_parallelism", 0)
            try:
                if int(parallelism) < 0:
                    errors.append("Host 'rootz': multipart_parallelism darf nicht negativ sein.")
            except (TypeError, ValueError):
                errors.append("Host 'rootz': multipart_parallelism ist ungueltig.")

            retries = host_cfg.get("multipart_part_retries", 3)
            try:
                if int(retries) < 1:
                    errors.append("Host 'rootz': multipart_part_retries muss >= 1 sein.")
            except (TypeError, ValueError):
                errors.append("Host 'rootz': multipart_part_retries ist ungueltig.")

            for endpoint_key in (
                "multipart_initiate_endpoint",
                "multipart_batch_urls_endpoint",
                "multipart_complete_endpoint",
            ):
                endpoint_value = str(host_cfg.get(endpoint_key, "")).strip()
                if not endpoint_value.startswith("/"):
                    errors.append(f"Host 'rootz': {endpoint_key} sollte mit '/' beginnen.")

        if host == "zerofs":
            api_url = str(host_cfg.get("api_url", "")).strip()
            if not api_url.startswith("http"):
                errors.append("Host 'zerofs': api_url muss mit http/https beginnen.")

    return errors


def build_preflight_messages(files: List[str], hosts: List[str], config: Dict[str, Any]) -> List[str]:
    messages: List[str] = []
    total_bytes = sum(os.path.getsize(path) for path in files)
    total_gb = total_bytes / (1024 ** 3)
    messages.append(f"Dateien: {len(files)}")
    messages.append(f"Gesamtgroesse: {total_gb:.2f} GB")
    messages.append(f"Hoster: {', '.join(hosts)}")

    for host in hosts:
        host_cfg = config.get(host, {})
        if host == "rootz":
            has_key = bool(str(host_cfg.get("api_key", "")).strip())
            messages.append(
                f"Rootz Auth: {'API-Key gesetzt' if has_key else 'anonym (25GB/15 Tage laut Doku)'}"
            )

        if host == "multiup":
            has_login = bool(str(host_cfg.get("username", "")).strip() and str(host_cfg.get("password", "")).strip())
            messages.append(f"MultiUp Login: {'gesetzt' if has_login else 'anonym'}")

    return messages


def expand_file_inputs(raw_inputs: List[str], recursive: bool) -> List[str]:
    discovered: List[str] = []

    for raw_path in raw_inputs:
        path = Path(raw_path).expanduser().resolve()
        if not path.exists():
            print(f"WARN: Pfad existiert nicht und wird uebersprungen: {path}")
            continue

        if path.is_file():
            discovered.append(str(path))
            continue

        if path.is_dir():
            iterator = path.rglob("*") if recursive else path.glob("*")
            for child in iterator:
                if child.is_file():
                    discovered.append(str(child.resolve()))

    unique_sorted = sorted(set(discovered))
    if not unique_sorted:
        raise SystemExit("Keine gueltigen Dateien gefunden.")
    return unique_sorted


def interactive_file_input() -> List[str]:
    print("Dateipfade eingeben (Datei oder Ordner), eine Zeile pro Eintrag. Leerzeile beendet:")
    entries: List[str] = []
    while True:
        value = input("  Pfad: ").strip()
        if not value:
            break
        entries.append(value)

    if not entries:
        raise SystemExit("Keine Dateien angegeben.")
    return entries


def parse_hosts(host_values: Optional[List[str]]) -> List[str]:
    if host_values:
        hosts = [item.strip().lower() for item in host_values if item.strip()]
    else:
        hosts = interactive_host_selection()

    invalid = [host for host in hosts if host not in UPLOADER_REGISTRY]
    if invalid:
        raise SystemExit(f"Unbekannte Hoster: {invalid}")

    deduped = []
    for host in hosts:
        if host not in deduped:
            deduped.append(host)

    if not deduped:
        raise SystemExit("Keine Hoster ausgewaehlt.")
    return deduped


def interactive_host_selection() -> List[str]:
    keys = list(UPLOADER_REGISTRY.keys())
    print("Waehle Hoster (z. B. 1,3,4):")
    for index, key in enumerate(keys, start=1):
        print(f"  {index}) {HOST_LABELS[key]} ({key})")

    raw = input("Auswahl: ").strip()
    if not raw:
        raise SystemExit("Keine Hoster ausgewaehlt.")

    selected: List[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if part.isdigit() and 1 <= int(part) <= len(keys):
            selected.append(keys[int(part) - 1])
        else:
            selected.append(part.lower())
    return selected


def execute_upload(host: str, file_path: str, config: Dict[str, Any]) -> UploadResult:
    uploader_cls = UPLOADER_REGISTRY[host]
    uploader = uploader_cls(config.get(host, {}))
    started = datetime.now(timezone.utc)
    started_ts = monotonic()

    try:
        result = uploader.upload(file_path)
        result.started_at = started.isoformat()
        result.finished_at = datetime.now(timezone.utc).isoformat()
        result.duration_seconds = round(monotonic() - started_ts, 3)
        return result
    except Exception as exc:
        error_text = str(exc).strip()
        if not error_text:
            error_text = f"{exc.__class__.__name__}: {repr(exc)}"
        return UploadResult(
            host=host,
            file_path=file_path,
            success=False,
            error=error_text,
            started_at=started.isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=round(monotonic() - started_ts, 3),
        )


def write_txt_report(results: List[UploadResult], output_path: Path, append: bool = True) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    success = [item for item in results if item.success and item.download_url]

    lines: List[str] = []
    lines.append("ShazUploader Export")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    if not success:
        lines.append("Keine erfolgreichen Uploads.")
    else:
        lines.append("Erfolgreiche Uploads:")
        for item in sorted(success, key=lambda row: (row.file_path, row.host)):
            host_label = HOST_LABELS.get(item.host, item.host)
            lines.append(f"[{host_label}] {item.file_path}")
            lines.append(f"  -> {item.download_url}")
            if item.duration_seconds is not None:
                lines.append(f"  Dauer: {item.duration_seconds:.2f}s")
            lines.append("")

    text_block = "\n".join(lines).rstrip() + "\n"
    mode = "a" if append else "w"

    with output_path.open(mode, encoding="utf-8") as file_handle:
        if append and output_path.exists() and output_path.stat().st_size > 0:
            file_handle.write("\n")
        file_handle.write(text_block)


def print_summary(results: List[UploadResult]) -> int:
    success = [result for result in results if result.success]
    failed = [result for result in results if not result.success]

    safe_print("\n=== Upload fertig ===")
    safe_print(f"Erfolgreich: {len(success)} | Fehlgeschlagen: {len(failed)}")

    safe_print("\n=== Download-Links ===")
    if success:
        for result in sorted(success, key=lambda item: (item.file_path, item.host)):
            name = Path(result.file_path).name
            host_label = HOST_LABELS.get(result.host, result.host)
            duration_suffix = f" ({result.duration_seconds:.2f}s)" if result.duration_seconds is not None else ""
            safe_print(f"[{host_label}] {name}: {result.download_url}{duration_suffix}")
    else:
        safe_print("Keine erfolgreichen Uploads.")

    if failed:
        safe_print("\n=== Fehler ===")
        for result in failed:
            name = Path(result.file_path).name
            host_label = HOST_LABELS.get(result.host, result.host)
            safe_print(f"[{host_label}] {name}: {result.error}")

    return 0 if not failed else 2


def format_elapsed(seconds: float) -> str:
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def main() -> int:
    global BYTE_PROGRESS_ENABLED, HTTP_DEBUG_ENABLED

    parser = argparse.ArgumentParser(
        description="Mehrere Dateien auf mehrere Hoster hochladen und am Ende alle Download-Links ausgeben.",
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Pfad zur JSON-Config")
    parser.add_argument("--init-config", action="store_true", help="Beispiel-Config erstellen")
    parser.add_argument("--force", action="store_true", help="Bei --init-config: bestehende Config ueberschreiben")
    parser.add_argument("--list-hosts", action="store_true", help="Unterstuetzte Hoster anzeigen und beenden")
    parser.add_argument("--check", action="store_true", help="Nur Preflight-Pruefungen ausfuehren, kein Upload")

    parser.add_argument(
        "--hosts",
        nargs="+",
        help="Hoster (Leerzeichen-getrennt), z. B. --hosts multiup buzzheavier",
    )
    parser.add_argument(
        "--files",
        nargs="+",
        help="Dateien/Ordner (Leerzeichen-getrennt). Ohne Angabe: interaktive Eingabe.",
    )
    parser.add_argument("--recursive", action="store_true", help="Ordner rekursiv einlesen")
    parser.add_argument("--concurrency", type=int, default=3, help="Parallele Uploads (Default: 3)")
    parser.add_argument("--json-out", default="", help="Optional: Ergebnisse als JSON-Datei schreiben")
    parser.add_argument("--txt-out", default=str(DEFAULT_TXT_OUTPUT_PATH), help="TXT-Export fuer erfolgreiche Uploads")
    parser.add_argument("--no-txt-out", action="store_true", help="TXT-Export deaktivieren")
    parser.add_argument("--overwrite-txt-out", action="store_true", help="TXT-Export-Datei ueberschreiben statt anhaengen")
    parser.add_argument("--no-byte-progress", action="store_true", help="Byte-Fortschrittslogs deaktivieren")
    parser.add_argument("--debug-http", action="store_true", help="HTTP-Requests/Responses detailliert loggen")

    args = parser.parse_args()
    BYTE_PROGRESS_ENABLED = not args.no_byte_progress
    HTTP_DEBUG_ENABLED = bool(args.debug_http)

    config_path = Path(args.config).expanduser().resolve()

    if args.init_config:
        init_config(config_path, args.force)
        return 0

    if args.list_hosts:
        safe_print("Unterstuetzte Hoster:")
        for key in UPLOADER_REGISTRY:
            safe_print(f"- {HOST_LABELS[key]} ({key})")
        return 0

    raw_files = args.files if args.files else interactive_file_input()
    files = expand_file_inputs(raw_files, recursive=args.recursive)
    hosts = parse_hosts(args.hosts)
    config = load_config(config_path)

    config_errors = validate_config(config, hosts)
    if config_errors:
        for error in config_errors:
            safe_print(f"CONFIG ERROR: {error}")
        return 1

    preflight_lines = build_preflight_messages(files, hosts, config)
    safe_print("=== Preflight ===")
    for line in preflight_lines:
        safe_print(f"- {line}")

    if args.check:
        safe_print("Check abgeschlossen. Keine Uploads ausgefuehrt (--check).")
        return 0

    safe_print(f"Dateien: {len(files)} | Hoster: {len(hosts)} | Jobs: {len(files) * len(hosts)}")

    results: List[UploadResult] = []
    max_workers = max(1, int(args.concurrency))
    total_jobs = len(files) * len(hosts)
    completed_jobs = 0
    started_at = monotonic()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(execute_upload, host, file_path, config): (host, file_path)
            for host in hosts
            for file_path in files
        }

        for future in as_completed(futures):
            host, file_path = futures[future]
            result = future.result()
            results.append(result)
            completed_jobs += 1

            progress_pct = (completed_jobs / total_jobs) * 100 if total_jobs else 100.0
            elapsed = format_elapsed(monotonic() - started_at)
            progress_prefix = f"[{completed_jobs}/{total_jobs} | {progress_pct:5.1f}% | {elapsed}]"

            host_label = HOST_LABELS.get(host, host)
            filename = Path(file_path).name
            if result.success:
                safe_print(f"{progress_prefix} OK  [{host_label}] {filename}")
            else:
                safe_print(f"{progress_prefix} ERR [{host_label}] {filename}: {result.error}")

    if args.json_out:
        output_path = Path(args.json_out).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps([asdict(item) for item in results], indent=2), encoding="utf-8")
        safe_print(f"JSON-Ergebnis geschrieben: {output_path}")

    if not args.no_txt_out and args.txt_out:
        txt_path = Path(args.txt_out).expanduser().resolve()
        write_txt_report(results, txt_path, append=not args.overwrite_txt_out)
        safe_print(f"TXT-Export geschrieben: {txt_path}")

    return print_summary(results)


if __name__ == "__main__":
    raise SystemExit(main())
