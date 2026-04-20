from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

from .base import BaseHostPlugin, UploadError


class BuzzheavierPlugin(BaseHostPlugin):
    host_key = "buzzheavier"
    display_name = "buzzheavier.com"
    domain = "buzzheavier.com"
    account_fields = ["token"]

    def _base_upload_url(self) -> str:
        return str(self.host_config.get("upload_base_url", "https://w.buzzheavier.com")).rstrip("/")

    def _build_upload_url(self, filename: str) -> str:
        base_url = self._base_upload_url()
        parent_id = str(self.host_config.get("parent_id", "")).strip()
        if parent_id:
            return f"{base_url}/{quote(parent_id, safe='')}/{quote(filename, safe='')}"
        return f"{base_url}/{quote(filename, safe='')}"

    def supports_resume(self) -> bool:
        return bool(self.host_config.get("enable_resume", False))

    def get_resume_offset(self, file_path: Path, metadata: Dict[str, Any] | None = None) -> int:
        _ = metadata
        upload_url = self._build_upload_url(file_path.name)
        response = self._request("HEAD", upload_url)
        if response.status_code == 404:
            return 0
        if response.status_code >= 400:
            raise UploadError(f"Buzzheavier resume lookup failed ({response.status_code}): {response.text[:300]}")

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
        filename = metadata["filename"]
        location_id = str(self.host_config.get("location_id", "")).strip()
        note = str(self.host_config.get("note", "")).strip()
        upload_url = self._build_upload_url(filename)

        params: Dict[str, str] = {}
        if location_id:
            params["locationId"] = location_id
        if note:
            params["note"] = base64.b64encode(note.encode("utf-8")).decode("ascii")

        headers: Dict[str, str] = {"Content-Length": str(size)}
        token = str(self.account.get("token") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        start_offset = int(metadata.get("start_offset", 0) or 0)
        file_size = int(metadata.get("file_size", size) or size)
        if start_offset > 0 and size > 0:
            end_offset = start_offset + size - 1
            headers["Content-Range"] = f"bytes {start_offset}-{end_offset}/{file_size}"

        response = self._upload_request("PUT", upload_url, data=stream, params=params, headers=headers)
        if response.status_code >= 400:
            raise UploadError(f"Buzzheavier upload failed ({response.status_code}): {response.text[:300]}")

        try:
            return response.json()
        except ValueError as exc:
            raise UploadError(f"Buzzheavier did not return JSON: {response.text[:300]}") from exc

    def finalize(self, response_data: Any, metadata: Dict[str, Any]) -> str:
        if not isinstance(response_data, dict):
            raise UploadError("Buzzheavier response is not an object")

        direct = self._extract_download_url(response_data)
        if direct:
            return direct
        raise UploadError(f"Buzzheavier link not found in response: {response_data}")

    @staticmethod
    def _extract_download_url(data: Dict[str, Any]) -> Optional[str]:
        for key in ("url", "download_url", "downloadUrl", "link"):
            value = data.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value

        nested = data.get("data")
        if isinstance(nested, dict):
            for key in ("url", "download_url", "downloadUrl", "link"):
                value = nested.get(key)
                if isinstance(value, str) and value.startswith("http"):
                    return value

            file_id = nested.get("id")
            if isinstance(file_id, str) and file_id:
                return f"https://buzzheavier.com/{file_id}"

        file_id = data.get("id")
        if isinstance(file_id, str) and file_id:
            return f"https://buzzheavier.com/{file_id}"

        return None
