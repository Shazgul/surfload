from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
from urllib.parse import quote

from .base import BaseHostPlugin, UploadError


class DummyLocalPlugin(BaseHostPlugin):
    host_key = "dummy_local"
    display_name = "Dummy Local Server"
    domain = "localhost"
    account_fields: list[str] = []

    def supports_resume(self) -> bool:
        return True

    def get_resume_offset(self, file_path: Path, metadata: Dict[str, Any] | None = None) -> int:
        _ = file_path
        _ = metadata
        base_url = str(self.host_config.get("upload_url", "http://127.0.0.1:8765/upload")).rstrip("/")
        url = f"{base_url}/{quote(file_path.name)}"
        response = self._request("HEAD", url)
        if response.status_code == 404:
            return 0
        if response.status_code >= 400:
            raise UploadError(
                f"dummy_local resume lookup failed ({response.status_code}): {response.text[:300]}"
            )
        raw = response.headers.get("X-Uploaded-Bytes", "0")
        try:
            return max(0, int(raw or 0))
        except ValueError:
            return 0

    def upload_file(self, stream: Any, size: int, metadata: Dict[str, Any]) -> Any:
        base_url = str(self.host_config.get("upload_url", "http://127.0.0.1:8765/upload")).rstrip("/")
        url = f"{base_url}/{quote(metadata['filename'])}"
        headers = {"Content-Length": str(size)}

        start_offset = int(metadata.get("start_offset", 0) or 0)
        file_size = int(metadata.get("file_size", size) or size)
        if start_offset > 0 and size > 0:
            end_offset = start_offset + size - 1
            headers["Content-Range"] = f"bytes {start_offset}-{end_offset}/{file_size}"

        response = self._upload_request("PUT", url, data=stream, headers=headers)
        if response.status_code >= 400:
            raise UploadError(f"dummy_local upload failed ({response.status_code}): {response.text[:300]}")
        return response.json()

    def finalize(self, response_data: Any, metadata: Dict[str, Any]) -> str:
        if isinstance(response_data, dict):
            for key in ("url", "download_url", "link"):
                value = response_data.get(key)
                if isinstance(value, str):
                    return value
        raise UploadError(f"dummy_local link not found: {response_data}")
