from __future__ import annotations

from typing import Any, Dict
from uuid import uuid4

from ..utils.streaming import MultipartStream
from .base import BaseHostPlugin, UploadError


class VikingfilePlugin(BaseHostPlugin):
    host_key = "vikingfile"
    display_name = "vikingfile.com"
    domain = "vikingfile.com"
    account_fields = ["user"]

    @staticmethod
    def _normalize_url_candidate(value: Any) -> str:
        if not isinstance(value, str):
            return ""

        url = value.strip()
        if not url:
            return ""
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("/"):
            return f"https://vikingfile.com{url}"
        if " " in url:
            return ""
        if "." in url:
            return f"https://{url}"
        return ""

    @classmethod
    def _extract_upload_url(cls, payload: Dict[str, Any]) -> str:
        for key in ("server", "upload_url", "uploadUrl", "url"):
            candidate = cls._normalize_url_candidate(payload.get(key))
            if candidate:
                return candidate

        nested = payload.get("data")
        if isinstance(nested, dict):
            for key in ("server", "upload_url", "uploadUrl", "url"):
                candidate = cls._normalize_url_candidate(nested.get(key))
                if candidate:
                    return candidate

        return ""

    def _resolve_upload_url(self) -> str:
        configured = str(self.host_config.get("upload_url", "")).strip()
        if configured:
            return configured

        fallback_url = "https://vikingfile.com/api/upload"
        discovery_url = str(self.host_config.get("server_api_url", "https://vikingfile.com/api/get-server")).strip()
        if not discovery_url:
            return fallback_url

        response = self._request("GET", discovery_url, timeout=30)
        if response.status_code >= 400:
            return fallback_url

        try:
            payload = response.json()
        except ValueError:
            return fallback_url

        if not isinstance(payload, dict):
            return fallback_url

        discovered = self._extract_upload_url(payload)
        return discovered or fallback_url

    def upload_file(self, stream: Any, size: int, metadata: Dict[str, Any]) -> Any:
        _ = size
        boundary = f"----SurfloadBoundary{uuid4().hex}"
        upload_url = self._resolve_upload_url()
        field_name = str(self.host_config.get("file_field", "file")).strip() or "file"

        form_data: Dict[str, str] = {}
        user_hash = str(
            self.account.get("user")
            or self.account.get("user_hash")
            or self.account.get("api_key")
            or self.host_config.get("user")
            or self.host_config.get("user_hash", "")
        ).strip()
        user_field = str(self.host_config.get("user_field", "user")).strip() or "user"
        if user_hash and user_field:
            form_data[user_field] = user_hash

        folder_id = str(self.host_config.get("folder_id", "")).strip()
        if folder_id:
            form_data["folder_id"] = folder_id

        path_value = str(self.host_config.get("path", "")).strip()
        path_field = str(self.host_config.get("path_field", "path")).strip() or "path"
        if path_value:
            form_data[path_field] = path_value

        public_share_value = str(self.host_config.get("path_public_share", "")).strip()
        public_share_field = (
            str(self.host_config.get("path_public_share_field", "pathPublicShare")).strip() or "pathPublicShare"
        )
        if public_share_value:
            form_data[public_share_field] = public_share_value

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
        auth_token = str(self.account.get("api_key") or self.account.get("token") or "").strip()
        if auth_token and bool(self.host_config.get("use_bearer_auth", False)):
            headers["Authorization"] = f"Bearer {auth_token}"

        response = self._upload_request("POST", upload_url, data=multipart, headers=headers)
        if response.status_code >= 400:
            raise UploadError(f"vikingfile upload failed ({response.status_code}): {response.text[:300]}")

        try:
            return response.json()
        except ValueError:
            text = response.text.strip()
            if "<html" in text[:500].lower():
                raise UploadError(
                    "vikingfile returned HTML instead of API JSON. "
                    "Check host_defaults.vikingfile.upload_url/server_api_url."
                )
            return text

    def finalize(self, response_data: Any, metadata: Dict[str, Any]) -> str:
        _ = metadata
        if isinstance(response_data, dict):
            data = response_data.get("data") if isinstance(response_data.get("data"), dict) else response_data
            if isinstance(data, dict):
                for key in ("download_url", "downloadUrl", "url", "link"):
                    value = data.get(key)
                    if isinstance(value, str) and value.startswith("http"):
                        return value

                file_id = data.get("id")
                if isinstance(file_id, str) and file_id:
                    template = str(self.host_config.get("download_url_template", "https://vikingfile.com/f/{id}"))
                    return template.format(id=file_id)

        if isinstance(response_data, str):
            for token in response_data.replace("\n", " ").split(" "):
                link = token.strip()
                if link.startswith("http"):
                    return link

        raise UploadError(f"vikingfile link not found in response: {response_data!r}")
