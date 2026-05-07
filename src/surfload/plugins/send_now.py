from __future__ import annotations

import re
from typing import Any, Dict
from .base import BaseHostPlugin, UploadError


class SendNowPlugin(BaseHostPlugin):
    host_key = "send_now"
    display_name = "send.now"
    domain = "send.now"
    account_fields: list[str] = []

    def _resolve_upload_url(self) -> str:
        configured = str(self.host_config.get("upload_url", "")).strip()
        if configured:
            return configured

        homepage_url = str(self.host_config.get("homepage_url", "https://send.now/")).strip()
        response = self._request("GET", homepage_url)
        if response.status_code >= 400:
            raise UploadError(f"send.now bootstrap failed ({response.status_code}): {response.text[:300]}")

        match = re.search(r"https://([a-zA-Z0-9]{4,})\.send\.now", response.text)
        if not match:
            raise UploadError("send.now upload server prefix not found on homepage")

        prefix = match.group(1)
        return f"https://{prefix}.send.now/cgi-bin/upload.cgi?upload_type=file&utype=anon"

    def upload_file(self, stream: Any, size: int, metadata: Dict[str, Any]) -> Any:
        _ = size
        upload_url = self._resolve_upload_url()
        files = {
            "file_0": (metadata["filename"], stream, metadata["mime_type"]),
        }
        response = self._upload_request("POST", upload_url, files=files)
        if response.status_code >= 400:
            raise UploadError(f"send.now upload failed ({response.status_code}): {response.text[:300]}")

        try:
            return response.json()
        except ValueError:
            return response.text.strip()

    def finalize(self, response_data: Any, metadata: Dict[str, Any]) -> str:
        _ = metadata
        if isinstance(response_data, list) and response_data:
            first = response_data[0]
            if isinstance(first, dict):
                file_code = first.get("file_code") or first.get("id")
                if isinstance(file_code, str) and file_code:
                    return f"https://send.now/{file_code}"

        if isinstance(response_data, dict):
            data = response_data.get("data") if isinstance(response_data.get("data"), dict) else response_data
            if isinstance(data, dict):
                for key in ("download_url", "downloadUrl", "downloadPage", "url", "link", "file_url"):
                    value = data.get(key)
                    if isinstance(value, str) and value.startswith("http"):
                        return value

                file_id = data.get("id")
                if isinstance(file_id, str) and file_id:
                    template = str(self.host_config.get("download_url_template", "https://send.now/{id}"))
                    return template.format(id=file_id)

        if isinstance(response_data, str):
            for token in response_data.replace("\n", " ").split(" "):
                link = token.strip()
                if link.startswith("http"):
                    return link

        raise UploadError(f"send.now link not found in response: {response_data!r}")
