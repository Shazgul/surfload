from __future__ import annotations

import re
from typing import Any, Dict

from .base import BaseHostPlugin, UploadError


class FileQPlugin(BaseHostPlugin):
    host_key = "fileq"
    display_name = "fileq.net"
    domain = "fileq.net"
    account_fields = ["api_key"]

    def _api_key(self) -> str:
        value = str(self.account.get("api_key") or self.host_config.get("api_key") or "").strip()
        if not value:
            raise UploadError("fileq api_key missing (set account field 'api_key')")
        return value

    def _resolve_upload_session(self) -> tuple[str, str]:
        api_base = str(self.host_config.get("api_base", "https://fileq.net/api")).strip().rstrip("/")
        response = self._request("GET", f"{api_base}/upload/server", params={"key": self._api_key()})
        if response.status_code >= 400:
            raise UploadError(f"fileq upload server discovery failed ({response.status_code}): {response.text[:300]}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise UploadError(f"fileq upload server discovery did not return JSON: {response.text[:300]}") from exc

        if not isinstance(payload, dict) or int(payload.get("status") or 0) != 200:
            raise UploadError(f"fileq upload server discovery failed: {payload}")

        upload_url = str(payload.get("result") or "").strip()
        sess_id = str(payload.get("sess_id") or "").strip()
        if not upload_url or not sess_id:
            raise UploadError(f"fileq upload server response missing result/sess_id: {payload}")

        return upload_url, sess_id

    def upload_file(self, stream: Any, size: int, metadata: Dict[str, Any]) -> Any:
        _ = size
        upload_url, sess_id = self._resolve_upload_session()

        form_data: Dict[str, str] = {
            "sess_id": sess_id,
            "utype": str(self.host_config.get("utype", "prem")).strip() or "prem",
        }
        folder_id = str(self.host_config.get("folder_id", "")).strip()
        if folder_id:
            form_data["fld_id"] = folder_id

        files = {
            "file_0": (metadata["filename"], stream, metadata["mime_type"]),
        }

        response = self._upload_request("POST", upload_url, data=form_data, files=files)
        if response.status_code >= 400:
            raise UploadError(f"fileq upload failed ({response.status_code}): {response.text[:300]}")

        try:
            return response.json()
        except ValueError:
            return response.text.strip()

    def finalize(self, response_data: Any, metadata: Dict[str, Any]) -> str:
        _ = metadata

        if isinstance(response_data, list) and response_data:
            first = response_data[0]
            if isinstance(first, dict):
                file_code = first.get("file_code") or first.get("filecode")
                if isinstance(file_code, str) and file_code:
                    return f"https://fileq.net/{file_code}"

        if isinstance(response_data, dict):
            result = response_data.get("result")
            if isinstance(result, dict):
                for key in ("url", "link"):
                    value = result.get(key)
                    if isinstance(value, str) and value.startswith("http"):
                        return value
                file_code = result.get("file_code") or result.get("filecode")
                if isinstance(file_code, str) and file_code:
                    return f"https://fileq.net/{file_code}"

            file_code = response_data.get("file_code") or response_data.get("filecode")
            if isinstance(file_code, str) and file_code:
                return f"https://fileq.net/{file_code}"

        if isinstance(response_data, str):
            for token in response_data.replace("\n", " ").split(" "):
                token = token.strip()
                if token.startswith("http"):
                    return token

            match = re.search(r'"file_code"\s*:\s*"([A-Za-z0-9]+)"', response_data)
            if match:
                return f"https://fileq.net/{match.group(1)}"

        raise UploadError(f"fileq link not found in response: {response_data!r}")
