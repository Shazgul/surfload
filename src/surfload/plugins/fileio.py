from __future__ import annotations

from typing import Any, Dict
from uuid import uuid4

from .base import BaseHostPlugin, UploadError
from ..utils.streaming import MultipartStream


class FileIoPlugin(BaseHostPlugin):
    host_key = "fileio"
    display_name = "file.io"
    domain = "file.io"
    account_fields = ["api_key"]

    def upload_file(self, stream: Any, size: int, metadata: Dict[str, Any]) -> Any:
        boundary = f"----SurfloadBoundary{uuid4().hex}"
        configured_upload_url = str(self.host_config.get("upload_url", "https://www.file.io")).strip()
        upload_urls = [
            url
            for url in [configured_upload_url, "https://www.file.io", "https://file.io"]
            if isinstance(url, str) and url.strip()
        ]

        max_downloads = int(self.host_config.get("max_downloads", 0) or 0)
        auto_delete = bool(self.host_config.get("auto_delete", False))
        expires = str(self.host_config.get("expires", "")).strip()

        form_data: Dict[str, str] = {}
        if max_downloads > 0:
            form_data["maxDownloads"] = str(max_downloads)
        if auto_delete:
            form_data["autoDelete"] = "true"
        if expires:
            form_data["expires"] = expires

        token = str(self.account.get("api_key") or "").strip()
        last_error = "file.io upload failed"

        for upload_url in dict.fromkeys(upload_urls):
            multipart = MultipartStream(
                file_path=metadata["path"],
                field_name="file",
                filename=metadata["filename"],
                mime_type=metadata["mime_type"],
                form_data=form_data,
                boundary=boundary,
                progress_callback=stream.progress_callback,
                chunk_size=stream.chunk_size,
            )

            headers = {
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(multipart)),
                "Accept": "application/json",
            }
            if token:
                headers["Authorization"] = f"Bearer {token}"

            response = self._upload_request("POST", upload_url, data=multipart, headers=headers)
            if response.status_code == 405:
                last_error = f"file.io upload failed ({response.status_code}) at {upload_url}: {response.text[:300]}"
                continue
            if response.status_code >= 400:
                raise UploadError(f"file.io upload failed ({response.status_code}): {response.text[:300]}")

            try:
                return response.json()
            except ValueError as exc:
                preview = response.text[:300]
                if "<html" in response.text.lower():
                    raise UploadError(
                        f"file.io returned HTML instead of API JSON at {upload_url}; endpoint may be unavailable or require auth: {preview}"
                    ) from exc
                raise UploadError(f"file.io did not return JSON: {preview}") from exc

        raise UploadError(last_error)

    def finalize(self, response_data: Any, metadata: Dict[str, Any]) -> str:
        _ = metadata
        if not isinstance(response_data, dict):
            raise UploadError("file.io response is not an object")

        payload = response_data.get("data") if isinstance(response_data.get("data"), dict) else response_data
        if isinstance(payload, dict):
            for key in ("link", "url", "downloadPage"):
                value = payload.get(key)
                if isinstance(value, str) and value.startswith("http"):
                    return value

            key_value = payload.get("key")
            if isinstance(key_value, str) and key_value:
                return f"https://www.file.io/{key_value}"

        raise UploadError(f"file.io link not found in response: {response_data}")
