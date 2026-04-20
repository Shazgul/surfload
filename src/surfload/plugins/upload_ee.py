from __future__ import annotations

from typing import Any, Dict
from uuid import uuid4

from ..utils.streaming import MultipartStream
from .base import BaseHostPlugin, UploadError


class UploadEePlugin(BaseHostPlugin):
    host_key = "upload_ee"
    display_name = "upload.ee"
    domain = "upload.ee"
    account_fields = ["token"]

    def upload_file(self, stream: Any, size: int, metadata: Dict[str, Any]) -> Any:
        _ = size
        boundary = f"----SurfloadBoundary{uuid4().hex}"
        upload_url = str(self.host_config.get("upload_url", "https://upload.ee/upload_api.php")).strip()
        field_name = str(self.host_config.get("file_field", "file")).strip() or "file"

        form_data: Dict[str, str] = {}
        folder = str(self.host_config.get("folder", "")).strip()
        if folder:
            form_data["folder"] = folder

        token = str(self.account.get("token") or "").strip()
        token_field = str(self.host_config.get("token_field", "token")).strip()
        if token and token_field:
            form_data[token_field] = token

        multipart = MultipartStream(
            file_path=metadata["path"],
            field_name=field_name,
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
        }

        if token and bool(self.host_config.get("use_bearer_auth", False)):
            headers["Authorization"] = f"Bearer {token}"

        response = self._upload_request("POST", upload_url, data=multipart, headers=headers)
        if response.status_code >= 400:
            raise UploadError(f"upload.ee upload failed ({response.status_code}): {response.text[:300]}")

        try:
            return response.json()
        except ValueError:
            return response.text.strip()

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
                    template = str(self.host_config.get("download_url_template", "https://upload.ee/files/{id}.html"))
                    return template.format(id=file_id)

        if isinstance(response_data, str):
            for token in response_data.replace("\n", " ").split(" "):
                link = token.strip()
                if link.startswith("http"):
                    return link

        raise UploadError(f"upload.ee link not found in response: {response_data!r}")
