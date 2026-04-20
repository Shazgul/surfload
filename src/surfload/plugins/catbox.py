from __future__ import annotations

from typing import Any, Dict
from uuid import uuid4

from ..utils.streaming import MultipartStream
from .base import BaseHostPlugin, UploadError


class CatboxPlugin(BaseHostPlugin):
    host_key = "catbox"
    display_name = "catbox.moe"
    domain = "catbox.moe"
    account_fields = ["userhash"]

    def upload_file(self, stream: Any, size: int, metadata: Dict[str, Any]) -> Any:
        boundary = f"----SurfloadBoundary{uuid4().hex}"
        upload_url = str(self.host_config.get("upload_url", "https://catbox.moe/user/api.php"))

        form_data: Dict[str, str] = {"reqtype": "fileupload"}
        userhash = str(self.account.get("userhash") or "").strip()
        if userhash:
            form_data["userhash"] = userhash

        multipart = MultipartStream(
            file_path=metadata["path"],
            field_name="fileToUpload",
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
            raise UploadError(f"catbox upload failed ({response.status_code}): {response.text[:300]}")
        return response.text.strip()

    def finalize(self, response_data: Any, metadata: Dict[str, Any]) -> str:
        if isinstance(response_data, str):
            link = response_data.strip().splitlines()[0].strip()
            if link.startswith("http"):
                return link
        raise UploadError(f"catbox link not found in response: {response_data!r}")
