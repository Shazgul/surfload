from __future__ import annotations

import re
from typing import Any, Dict
from uuid import uuid4

from ..utils.streaming import MultipartStream
from .base import BaseHostPlugin, UploadError


class MegaupPlugin(BaseHostPlugin):
    host_key = "megaup"
    display_name = "megaup.net"
    domain = "megaup.net"
    account_fields = ["login", "key"]

    def _resolve_api_upload_url(self) -> str:
        login = str(self.account.get("login") or self.account.get("username") or "").strip()
        key = str(self.account.get("key") or self.account.get("api_key") or self.account.get("token") or "").strip()
        if not login or not key:
            return ""

        folder = str(self.host_config.get("folder_id", "")).strip()
        template = str(
            self.host_config.get(
                "upload_api_url_template",
                "https://api.megaup.cc/v1/file/upload?login={login}&key={key}&folder={folder}",
            )
        )
        api_url = template.format(login=login, key=key, folder=folder)

        response = self._request("GET", api_url)
        if response.status_code >= 400:
            raise UploadError(f"megaup API upload URL request failed ({response.status_code}): {response.text[:300]}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise UploadError(f"megaup API upload URL response is not JSON: {response.text[:300]}") from exc

        result = payload.get("result") if isinstance(payload, dict) else None
        if isinstance(result, dict):
            upload_url = result.get("url")
            if isinstance(upload_url, str) and upload_url:
                return upload_url.strip()

        raise UploadError(f"megaup API upload URL missing in response: {payload}")

    def _resolve_upload_params(self) -> tuple[str, str, str]:
        response = self._request("GET", str(self.host_config.get("bootstrap_url", "https://megaup.net/")).strip())
        if response.status_code >= 400:
            raise UploadError(f"megaup bootstrap failed ({response.status_code}): {response.text[:300]}")

        html = response.text
        tracker_match = re.search(r"cTracker\s*:\s*['\"]([a-zA-Z0-9]+)['\"]", html)
        session_match = re.search(r"_sessionid\s*:\s*['\"]([a-zA-Z0-9]+)['\"]", html)
        upload_match = re.search(r"(https:\\\/\\\/[^'\"\s]+file_upload_handler[^'\"\s]*)", html)
        if not upload_match:
            upload_match = re.search(r"(https://[^'\"\s]+file_upload_handler[^'\"\s]*)", html)
        if not upload_match:
            url_match = re.search(r"url\s*:\s*['\"]([^'\"]*file_upload_handler[^'\"]*)['\"]", html)
            if url_match:
                upload_match = url_match

        if not upload_match:
            raise UploadError("megaup upload parameters not found on bootstrap page")

        upload_url = upload_match.group(1).replace("\\/", "/")
        tracker = tracker_match.group(1) if tracker_match else ""
        session_id = session_match.group(1) if session_match else ""
        return tracker, session_id, upload_url

    def upload_file(self, stream: Any, size: int, metadata: Dict[str, Any]) -> Any:
        _ = size
        boundary = f"----SurfloadBoundary{uuid4().hex}"
        upload_url = ""
        try:
            upload_url = self._resolve_api_upload_url()
        except UploadError:
            upload_url = ""
        if upload_url:
            field_name = str(self.host_config.get("api_file_field", "file")).strip() or "file"
            form_data: Dict[str, str] = {}
        else:
            tracker, session_id, upload_url = self._resolve_upload_params()
            field_name = str(self.host_config.get("file_field", "files[]")).strip() or "files[]"

            form_data = {
                "maxChunkSize": str(int(self.host_config.get("max_chunk_size", 100000000) or 100000000)),
                "folderId": str(self.host_config.get("folder_id", "")).strip(),
            }
            if session_id:
                form_data["_sessionid"] = session_id
            if tracker:
                form_data["cTracker"] = tracker

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

        response = self._upload_request("POST", upload_url, data=multipart, headers=headers)
        if response.status_code >= 400:
            raise UploadError(f"megaup upload failed ({response.status_code}): {response.text[:300]}")

        try:
            return response.json()
        except ValueError:
            return response.text.strip()

    def finalize(self, response_data: Any, metadata: Dict[str, Any]) -> str:
        _ = metadata
        if isinstance(response_data, list) and response_data:
            first = response_data[0]
            if isinstance(first, dict):
                for key in ("url", "link"):
                    value = first.get(key)
                    if isinstance(value, str) and value.startswith("http"):
                        return value

        if isinstance(response_data, dict):
            data = response_data.get("data") if isinstance(response_data.get("data"), dict) else response_data
            if isinstance(data, dict):
                for key in ("download_url", "downloadUrl", "url", "link"):
                    value = data.get(key)
                    if isinstance(value, str) and value.startswith("http"):
                        return value

                file_id = data.get("id") or data.get("code") or data.get("file_code")
                if isinstance(file_id, str) and file_id:
                    template = str(self.host_config.get("download_url_template", "https://megaup.net/{id}"))
                    return template.format(id=file_id)

        if isinstance(response_data, str):
            for token in response_data.replace("\n", " ").split(" "):
                link = token.strip()
                if link.startswith("http"):
                    return link

        raise UploadError(f"megaup link not found in response: {response_data!r}")
