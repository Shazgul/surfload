from __future__ import annotations

from typing import Any, Dict, Optional
import re

from .base import BaseHostPlugin, UploadError


class DailyUploadsPlugin(BaseHostPlugin):
    host_key = "dailyuploads"
    display_name = "dailyuploads.net"
    domain = "dailyuploads.net"
    account_fields: list[str] = []

    def _resolve_upload_url(self) -> str:
        configured = str(self.host_config.get("upload_url", "")).strip()
        if configured:
            return configured

        server_url = str(self.host_config.get("server_url", "https://dailyuploads.net/server")).strip()
        response = self._request("GET", server_url)
        if response.status_code >= 400:
            raise UploadError(f"dailyuploads server discovery failed ({response.status_code}): {response.text[:300]}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise UploadError(f"dailyuploads server discovery did not return JSON: {response.text[:300]}") from exc

        upload_base = payload.get("url") if isinstance(payload, dict) else None
        if not isinstance(upload_base, str) or not upload_base:
            raise UploadError(f"dailyuploads upload URL missing in response: {payload}")

        return f"{upload_base.rstrip('/')}/upload.cgi"

    def upload_file(self, stream: Any, size: int, metadata: Dict[str, Any]) -> Any:
        upload_url = self._resolve_upload_url()
        files = {
            "file_0": (metadata["filename"], stream, metadata["mime_type"]),
        }
        response = self._upload_request("POST", upload_url, files=files)
        if response.status_code >= 400:
            raise UploadError(f"dailyuploads upload failed ({response.status_code}): {response.text[:300]}")

        try:
            return response.json()
        except ValueError:
            return response.text.strip()

    def finalize(self, response_data: Any, metadata: Dict[str, Any]) -> str:
        _ = metadata
        file_code = self._extract_file_code(response_data)
        if file_code:
            return f"https://dailyuploads.net/{file_code}"

        if isinstance(response_data, str):
            match = re.search(r"https?://[^\s\"']+", response_data)
            if match:
                return match.group(0)

        raise UploadError(f"dailyuploads link not found in response: {response_data!r}")

    def _extract_file_code(self, response_data: Any) -> Optional[str]:
        if isinstance(response_data, list) and response_data:
            first = response_data[0]
            if isinstance(first, dict):
                value = first.get("file_code") or first.get("filecode") or first.get("id")
                if isinstance(value, str) and value:
                    return value

        if isinstance(response_data, dict):
            data = response_data.get("data") if isinstance(response_data.get("data"), dict) else response_data
            if isinstance(data, dict):
                file_id = data.get("id") or data.get("code") or data.get("file_code")
                if isinstance(file_id, str) and file_id:
                    return file_id
        return None
