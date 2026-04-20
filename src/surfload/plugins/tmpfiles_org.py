from __future__ import annotations

from typing import Any, Dict
from uuid import uuid4

from ..utils.streaming import MultipartStream
from .base import BaseHostPlugin, UploadError


class TmpfilesOrgPlugin(BaseHostPlugin):
    host_key = "tmpfiles_org"
    display_name = "tmpfiles.org"
    domain = "tmpfiles.org"
    account_fields: list[str] = []

    def upload_file(self, stream: Any, size: int, metadata: Dict[str, Any]) -> Any:
        boundary = f"----SurfloadBoundary{uuid4().hex}"
        upload_url = str(self.host_config.get("upload_url", "https://tmpfiles.org/api/v1/upload"))

        multipart = MultipartStream(
            file_path=metadata["path"],
            field_name="file",
            filename=metadata["filename"],
            mime_type=metadata["mime_type"],
            form_data=None,
            boundary=boundary,
            progress_callback=stream.progress_callback,
            chunk_size=stream.chunk_size,
        )

        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(multipart)),
        }

        response = self._upload_request("POST", upload_url, data=multipart, headers=headers)
        if response.status_code >= 400:
            raise UploadError(f"tmpfiles.org upload failed ({response.status_code}): {response.text[:300]}")

        try:
            return response.json()
        except ValueError as exc:
            raise UploadError(f"tmpfiles.org did not return JSON: {response.text[:300]}") from exc

    def finalize(self, response_data: Any, metadata: Dict[str, Any]) -> str:
        if not isinstance(response_data, dict):
            raise UploadError("tmpfiles.org response is not an object")

        data = response_data.get("data")
        if isinstance(data, dict):
            for key in ("url", "download_url", "downloadUrl", "link"):
                value = data.get(key)
                if isinstance(value, str) and value.startswith("http"):
                    return value

        for key in ("url", "download_url", "downloadUrl", "link"):
            value = response_data.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value

        raise UploadError(f"tmpfiles.org link not found in response: {response_data}")
