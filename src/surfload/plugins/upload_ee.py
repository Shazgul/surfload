from __future__ import annotations

import re
import time
from typing import Any, Dict
from .base import BaseHostPlugin, UploadError


class UploadEePlugin(BaseHostPlugin):
    host_key = "upload_ee"
    display_name = "upload.ee"
    domain = "upload.ee"
    account_fields: list[str] = []

    def _resolve_upload_id(self) -> str:
        timestamp = int(time.time() * 1000)
        configured = str(self.host_config.get("bootstrap_url", "")).strip()
        default_bootstrap = f"https://www.upload.ee/ubr_link_upload.php?page=uploadsimple&rnd_id={timestamp}"
        candidates = [url for url in [configured, default_bootstrap] if url]

        last_error = ""
        for bootstrap_url in dict.fromkeys(candidates):
            response = self._request("GET", bootstrap_url)
            if response.status_code >= 400:
                last_error = f"upload.ee bootstrap failed ({response.status_code}): {response.text[:300]}"
                continue

            match = re.search(r'startUpload\(["\']([A-Za-z0-9]+)["\'](?:\s*,\s*\d+)?\)', response.text)
            if not match:
                match = re.search(r'upload_id=([A-Za-z0-9]+)', response.text)
            if not match:
                match = re.search(r'name=["\']upload_id["\']\s+value=["\']([A-Za-z0-9]+)["\']', response.text)
            if match:
                return match.group(1)

            last_error = "upload.ee upload_id not found in bootstrap response"

        raise UploadError(last_error or "upload.ee upload_id not found in bootstrap response")

    def upload_file(self, stream: Any, size: int, metadata: Dict[str, Any]) -> Any:
        _ = size
        upload_id = self._resolve_upload_id()
        upload_url = str(
            self.host_config.get(
                "upload_url_template",
                "https://www.upload.ee/cgi-bin/ubr_upload.pl?X-Progress-ID={upload_id}&upload_id={upload_id}",
            )
        ).format(upload_id=upload_id)

        files = {
            "upfile_0": (metadata["filename"], stream, metadata["mime_type"]),
        }
        form_data = {
            "link": "",
            "email": "",
            "category": "cat_file",
            "big_resize": "none",
        }

        response = self._upload_request("POST", upload_url, data=form_data, files=files)
        if response.status_code >= 400:
            raise UploadError(f"upload.ee upload failed ({response.status_code}): {response.text[:300]}")

        if re.search(r"https?://www\.upload\.ee/files/[A-Za-z0-9]+/[^\"'\s<]+", response.text):
            return response.text

        finished_url = str(
            self.host_config.get("finished_url_template", "https://www.upload.ee/?page=finishedsimple&upload_id={upload_id}")
        ).format(upload_id=upload_id)
        finished_response = self._request("GET", finished_url)
        if finished_response.status_code >= 400:
            raise UploadError(
                f"upload.ee finished page failed ({finished_response.status_code}): {finished_response.text[:300]}"
            )
        return finished_response.text or response.text

    def finalize(self, response_data: Any, metadata: Dict[str, Any]) -> str:
        _ = metadata
        if isinstance(response_data, dict):
            data = response_data.get("data") if isinstance(response_data.get("data"), dict) else response_data
            if isinstance(data, dict):
                for key in ("download_url", "downloadUrl", "url", "link"):
                    value = data.get(key)
                    if isinstance(value, str) and value.startswith("http"):
                        return value

                file_id = data.get("id") or data.get("code")
                if isinstance(file_id, str) and file_id:
                    return f"https://upload.ee/files/{file_id}.html"

        if isinstance(response_data, str):
            match = re.search(r"https?://www\.upload\.ee/files/[A-Za-z0-9]+/[^\"'\s<]+", response_data)
            if match:
                return match.group(0)

            for token in response_data.replace("\n", " ").split(" "):
                link = token.strip()
                if link.startswith("http://") or link.startswith("https://"):
                    return link

        raise UploadError(f"upload.ee link not found in response: {response_data!r}")
