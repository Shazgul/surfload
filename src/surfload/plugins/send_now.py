from __future__ import annotations

from typing import Any, Dict
from uuid import uuid4

from ..utils.streaming import MultipartStream
from .base import BaseHostPlugin, UploadError


class SendNowPlugin(BaseHostPlugin):
    host_key = "send_now"
    display_name = "send.now"
    domain = "send.now"
    account_fields = ["token"]

    def upload_file(self, stream: Any, size: int, metadata: Dict[str, Any]) -> Any:
        _ = size
        boundary = f"----SurfloadBoundary{uuid4().hex}"
        upload_url = str(self.host_config.get("upload_url", "https://api.send.now/upload")).strip()
        field_name = str(self.host_config.get("file_field", "file")).strip() or "file"

        form_data: Dict[str, str] = {}
        for key in ("expires", "password", "message"):
            value = str(self.host_config.get(key, "")).strip()
            if value:
                form_data[key] = value

        one_time = self.host_config.get("one_time_download", None)
        if one_time is not None:
            form_data["one_time_download"] = "true" if bool(one_time) else "false"

        token = str(self.account.get("token") or "").strip()
        token_field = str(self.host_config.get("token_field", "")).strip()
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
        if token and bool(self.host_config.get("use_bearer_auth", True)):
            headers["Authorization"] = f"Bearer {token}"

        response = self._upload_request("POST", upload_url, data=multipart, headers=headers)
        if response.status_code >= 400:
            raise UploadError(f"send.now upload failed ({response.status_code}): {response.text[:300]}")

        try:
            return response.json()
        except ValueError:
            return response.text.strip()

    def finalize(self, response_data: Any, metadata: Dict[str, Any]) -> str:
        _ = metadata
        if isinstance(response_data, dict):
            data = response_data.get("data") if isinstance(response_data.get("data"), dict) else response_data
            if isinstance(data, dict):
                for key in ("download_url", "downloadUrl", "downloadPage", "url", "link"):
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
