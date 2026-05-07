from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote
from uuid import uuid4

from ..utils.streaming import MultipartStream
from .base import BaseHostPlugin, UploadError


class GofilePlugin(BaseHostPlugin):
    host_key = "gofile"
    display_name = "gofile.io"
    domain = "gofile.io"
    account_fields = ["token"]

    def supports_resume(self) -> bool:
        return bool(self.host_config.get("enable_resume", False))

    def _resolve_resume_probe_url(self, file_path: Path) -> str:
        template = str(self.host_config.get("resume_probe_url_template", "")).strip()
        if not template:
            return ""
        return template.format(
            filename=file_path.name,
            filename_quoted=quote(file_path.name, safe=""),
        )

    def get_resume_offset(self, file_path: Path, metadata: Dict[str, Any] | None = None) -> int:
        _ = metadata
        probe_url = self._resolve_resume_probe_url(file_path)
        if not probe_url:
            return 0

        response = self._request("HEAD", probe_url)
        if response.status_code == 404:
            return 0
        if response.status_code >= 400:
            raise UploadError(f"gofile resume lookup failed ({response.status_code}): {response.text[:300]}")

        for header in ("X-Uploaded-Bytes", "Content-Length"):
            if header not in response.headers:
                continue
            raw = response.headers.get(header, "0")
            try:
                return max(0, int(raw or 0))
            except ValueError:
                continue
        return 0

    def _resolve_upload_url(self) -> str:
        configured = str(self.host_config.get("upload_url", "https://upload.gofile.io/uploadfile")).strip()
        if configured:
            return configured

        server_api_url = str(self.host_config.get("server_api_url", "https://api.gofile.io/servers")).strip()
        response = self._request("GET", server_api_url)
        if response.status_code >= 400:
            raise UploadError(f"gofile server discovery failed ({response.status_code}): {response.text[:300]}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise UploadError(f"gofile server discovery did not return JSON: {response.text[:300]}") from exc

        server = self._extract_server(payload)
        if not server:
            raise UploadError(f"gofile server not found in response: {payload}")

        if server.startswith("http://") or server.startswith("https://"):
            base_url = server.rstrip("/")
        else:
            domain = str(self.host_config.get("server_domain", "gofile.io")).strip() or "gofile.io"
            base_url = f"https://{server}.{domain}".rstrip("/")

        upload_path = str(self.host_config.get("upload_path", "/uploadFile")).strip() or "/uploadFile"
        if not upload_path.startswith("/"):
            upload_path = f"/{upload_path}"
        return f"{base_url}{upload_path}"

    @staticmethod
    def _extract_server(payload: Dict[str, Any]) -> Optional[str]:
        if isinstance(payload.get("data"), dict):
            data = payload["data"]
            if isinstance(data.get("server"), dict):
                server_info = data["server"]
                for key in ("url", "uploadUrl"):
                    value = server_info.get(key)
                    if isinstance(value, str) and value:
                        return value.strip()

            if isinstance(data.get("servers"), list) and data["servers"]:
                first = data["servers"][0]
                if isinstance(first, dict):
                    for key in ("url", "uploadUrl"):
                        value = first.get(key)
                        if isinstance(value, str) and value:
                            return value.strip()

        data = payload.get("data")
        if isinstance(data, dict):
            server = data.get("server")
            if isinstance(server, str) and server:
                return server.strip()

        server = payload.get("server")
        if isinstance(server, str) and server:
            return server.strip()

        return None

    def upload_file(self, stream: Any, size: int, metadata: Dict[str, Any]) -> Any:
        _ = size
        boundary = f"----SurfloadBoundary{uuid4().hex}"
        upload_url = self._resolve_upload_url()

        token = str(self.account.get("token") or "").strip()
        form_data: Dict[str, str] = {}
        if token:
            form_data["token"] = token

        folder_id = str(self.host_config.get("folder_id", "")).strip()
        if folder_id:
            form_data["folderId"] = folder_id

        multipart = MultipartStream(
            file_path=metadata["path"],
            field_name="file",
            filename=metadata["filename"],
            mime_type=metadata["mime_type"],
            form_data=form_data,
            boundary=boundary,
            progress_callback=stream.progress_callback,
            chunk_size=stream.chunk_size,
            file_start_offset=int(metadata.get("start_offset", 0) or 0),
            file_size=int(metadata.get("remaining_size", 0) or 0),
        )

        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(multipart)),
        }

        start_offset = int(metadata.get("start_offset", 0) or 0)
        file_size = int(metadata.get("file_size", size) or size)
        if start_offset > 0 and size > 0:
            end_offset = start_offset + size - 1
            headers["Content-Range"] = f"bytes {start_offset}-{end_offset}/{file_size}"

        if token and bool(self.host_config.get("use_bearer_auth", False)):
            headers["Authorization"] = f"Bearer {token}"

        response = self._upload_request("POST", upload_url, data=multipart, headers=headers)
        if response.status_code >= 400:
            raise UploadError(f"gofile upload failed ({response.status_code}): {response.text[:300]}")

        try:
            return response.json()
        except ValueError as exc:
            raise UploadError(f"gofile did not return JSON: {response.text[:300]}") from exc

    def finalize(self, response_data: Any, metadata: Dict[str, Any]) -> str:
        _ = metadata
        if not isinstance(response_data, dict):
            raise UploadError("gofile response is not an object")

        data = response_data.get("data") if isinstance(response_data.get("data"), dict) else response_data
        if isinstance(data, dict):
            for key in ("downloadPage", "downloadPageUrl", "url", "link"):
                value = data.get(key)
                if isinstance(value, str) and value.startswith("http"):
                    return value

            file_id = data.get("id")
            if isinstance(file_id, str) and file_id:
                return f"https://gofile.io/d/{file_id}"

        raise UploadError(f"gofile link not found in response: {response_data}")
