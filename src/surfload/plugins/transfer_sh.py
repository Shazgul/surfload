from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
from urllib.parse import quote

from .base import BaseHostPlugin, UploadError


class TransferShPlugin(BaseHostPlugin):
    host_key = "transfer_sh"
    display_name = "transfer.sh"
    domain = "transfer.sh"
    account_fields = ["token"]

    def supports_resume(self) -> bool:
        return bool(self.host_config.get("enable_resume", False))

    def get_resume_offset(self, file_path: Path, metadata: Dict[str, Any] | None = None) -> int:
        _ = metadata
        base_url = str(self.host_config.get("upload_url", "https://transfer.sh")).rstrip("/")
        target_url = f"{base_url}/{quote(file_path.name)}"
        response = self._request("HEAD", target_url)
        if response.status_code == 404:
            return 0
        if response.status_code >= 400:
            raise UploadError(f"transfer.sh resume lookup failed ({response.status_code}): {response.text[:300]}")

        for header in ("X-Uploaded-Bytes", "Content-Length"):
            if header not in response.headers:
                continue
            raw = response.headers.get(header, "0")
            try:
                return max(0, int(raw or 0))
            except ValueError:
                continue
        return 0

    def upload_file(self, stream: Any, size: int, metadata: Dict[str, Any]) -> Any:
        base_url = str(self.host_config.get("upload_url", "https://transfer.sh")).rstrip("/")
        target_url = f"{base_url}/{quote(metadata['filename'])}"

        headers: Dict[str, str] = {
            "Content-Length": str(size),
        }

        max_days = int(self.host_config.get("max_days", 14) or 14)
        max_downloads = int(self.host_config.get("max_downloads", 0) or 0)
        if max_days > 0:
            headers["Max-Days"] = str(max_days)
        if max_downloads > 0:
            headers["Max-Downloads"] = str(max_downloads)

        token = str(self.account.get("token") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        start_offset = int(metadata.get("start_offset", 0) or 0)
        file_size = int(metadata.get("file_size", size) or size)
        if start_offset > 0 and size > 0:
            end_offset = start_offset + size - 1
            headers["Content-Range"] = f"bytes {start_offset}-{end_offset}/{file_size}"

        response = self._upload_request("PUT", target_url, data=stream, headers=headers)
        if response.status_code >= 400:
            raise UploadError(f"transfer.sh upload failed ({response.status_code}): {response.text[:300]}")
        return response.text.strip()

    def finalize(self, response_data: Any, metadata: Dict[str, Any]) -> str:
        if isinstance(response_data, str) and response_data.startswith("http"):
            return response_data.splitlines()[0].strip()
        raise UploadError(f"transfer.sh link not found in response: {response_data!r}")
