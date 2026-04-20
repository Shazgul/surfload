from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import uuid4

from ..utils.streaming import MultipartStream
from .base import BaseHostPlugin, UploadError


class OneFichierPlugin(BaseHostPlugin):
    host_key = "onefichier"
    display_name = "1fichier.com"
    domain = "1fichier.com"
    account_fields = ["api_key"]

    def _resolve_upload_url(self) -> str:
        configured = str(self.host_config.get("upload_url", "")).strip()
        if configured:
            return configured

        fallback_url = "https://up.1fichier.com/upload.cgi"
        api_key = str(self.account.get("api_key") or "").strip()
        if not api_key:
            return fallback_url

        discovery_url = str(
            self.host_config.get("upload_server_url", "https://api.1fichier.com/v1/upload/get_upload_server.cgi")
        ).strip()
        if not discovery_url:
            return fallback_url

        response = self._request("POST", discovery_url, headers={"Authorization": f"Bearer {api_key}"})
        if response.status_code >= 400:
            return fallback_url

        try:
            payload = response.json()
        except ValueError:
            return fallback_url

        discovered_url = self._extract_upload_url(payload)
        return discovered_url or fallback_url

    @staticmethod
    def _extract_upload_url(payload: Dict[str, Any]) -> Optional[str]:
        for key in ("upload_url", "uploadUrl", "url"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value

        nested = payload.get("data")
        if isinstance(nested, dict):
            for key in ("upload_url", "uploadUrl", "url"):
                value = nested.get(key)
                if isinstance(value, str) and value.startswith("http"):
                    return value

        return None

    def upload_file(self, stream: Any, size: int, metadata: Dict[str, Any]) -> Any:
        boundary = f"----SurfloadBoundary{uuid4().hex}"
        upload_url = self._resolve_upload_url()

        field_name = str(self.host_config.get("file_field", "file[]")).strip() or "file[]"
        form_data: Dict[str, str] = {}

        folder_id = str(self.host_config.get("folder_id", "")).strip()
        if folder_id:
            form_data["folder_id"] = folder_id

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

        api_key = str(self.account.get("api_key") or "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        response = self._upload_request("POST", upload_url, data=multipart, headers=headers)
        if response.status_code >= 400:
            raise UploadError(f"1fichier upload failed ({response.status_code}): {response.text[:300]}")

        try:
            return response.json()
        except ValueError:
            return response.text.strip()

    def finalize(self, response_data: Any, metadata: Dict[str, Any]) -> str:
        _ = metadata
        if isinstance(response_data, dict):
            for key in ("download_url", "downloadUrl", "url", "link"):
                value = response_data.get(key)
                if isinstance(value, str) and value.startswith("http"):
                    return value

            nested = response_data.get("data")
            if isinstance(nested, dict):
                for key in ("download_url", "downloadUrl", "url", "link"):
                    value = nested.get(key)
                    if isinstance(value, str) and value.startswith("http"):
                        return value

                file_id = nested.get("id")
                if isinstance(file_id, str) and file_id:
                    template = str(
                        self.host_config.get("download_url_template", "https://1fichier.com/?{id}")
                    )
                    return template.format(id=file_id)

        if isinstance(response_data, str):
            for token in response_data.replace("\n", " ").split(" "):
                link = token.strip()
                if link.startswith("http"):
                    return link

        raise UploadError(f"1fichier link not found in response: {response_data!r}")
