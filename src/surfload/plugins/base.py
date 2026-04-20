from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from ..utils.streaming import ProgressFile


class UploadError(Exception):
    """Domain-specific upload error."""


@dataclass
class PluginUploadResult:
    download_url: str
    raw_response: Any


def create_retry_session(total_retries: int = 3, backoff_factor: float = 1.0) -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=total_retries,
        connect=total_retries,
        read=total_retries,
        status=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=[408, 425, 429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


class BaseHostPlugin:
    """Plugin API for hoster integrations."""

    host_key = "base"
    display_name = "Base"
    domain = "example.com"
    account_fields: list[str] = []

    def __init__(self, host_config: Optional[Dict[str, Any]] = None, logger: Any = None) -> None:
        self.host_config = host_config or {}
        self.logger = logger
        self.timeout = int(self.host_config.get("timeout", 120))

        retries = int(self.host_config.get("retries", 3) or 3)
        backoff_factor = float(self.host_config.get("backoff_factor", 1.0) or 1.0)
        self.session = create_retry_session(max(0, retries), max(0.0, backoff_factor))
        # Upload calls are intentionally not retried at HTTP-adapter level,
        # to avoid hidden full-body re-uploads and memory spikes.
        self.upload_session = create_retry_session(0, 0.0)

        self.account: Dict[str, Any] = {}

    def init(self) -> None:
        """Optional host initialization hook."""

    def auth(self, account: Optional[Dict[str, Any]]) -> None:
        """Receive selected account data from credential store."""
        self.account = account or {}

    def supports_resume(self) -> bool:
        """Whether this host plugin can resume interrupted uploads."""
        return False

    def get_resume_offset(self, file_path: Path, metadata: Optional[Dict[str, Any]] = None) -> int:
        """Return already-uploaded byte count for resume, if supported."""
        _ = file_path
        _ = metadata
        return 0

    def upload_file(self, stream: Any, size: int, metadata: Dict[str, Any]) -> Any:
        """Send stream to host and return host response payload."""
        raise NotImplementedError

    def finalize(self, response_data: Any, metadata: Dict[str, Any]) -> str:
        """Extract final downloadable URL from host response payload."""
        raise NotImplementedError

    def upload_path(
        self,
        file_path: Path,
        chunk_size: int,
        progress_callback,
        start_offset: int = 0,
    ) -> PluginUploadResult:
        file_size = file_path.stat().st_size
        start_offset = max(0, min(int(start_offset), file_size))
        remaining_size = file_size - start_offset
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        metadata = {
            "filename": file_path.name,
            "mime_type": mime_type,
            "path": file_path,
            "file_size": file_size,
            "start_offset": start_offset,
            "remaining_size": remaining_size,
        }

        if start_offset and progress_callback:
            progress_callback(start_offset)

        with file_path.open("rb") as file_handle:
            if start_offset:
                file_handle.seek(start_offset)
            stream = ProgressFile(file_handle, progress_callback=progress_callback, chunk_size=chunk_size)
            raw = self.upload_file(stream=stream, size=remaining_size, metadata=metadata)

        download_url = self.finalize(raw, metadata=metadata)
        return PluginUploadResult(download_url=download_url, raw_response=raw)

    def _request(self, method: str, url: str, timeout: Optional[int] = None, **kwargs: Any) -> requests.Response:
        response = self.session.request(method=method, url=url, timeout=timeout or self.timeout, **kwargs)
        return response

    def _upload_request(self, method: str, url: str, timeout: Optional[int] = None, **kwargs: Any) -> requests.Response:
        response = self.upload_session.request(method=method, url=url, timeout=timeout or self.timeout, **kwargs)
        return response
