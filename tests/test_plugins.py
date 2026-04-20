from __future__ import annotations

from pathlib import Path

import pytest

from surfload.plugins import get_plugin_registry
from surfload.plugins.base import BaseHostPlugin, UploadError
from surfload.plugins.buzzheavier import BuzzheavierPlugin
from surfload.plugins.catbox import CatboxPlugin
from surfload.plugins.dummy_local import DummyLocalPlugin
from surfload.plugins.gofile import GofilePlugin
from surfload.plugins.onefichier import OneFichierPlugin
from surfload.plugins.send_now import SendNowPlugin
from surfload.plugins.transfer_sh import TransferShPlugin
from surfload.plugins.tmpfiles_org import TmpfilesOrgPlugin
from surfload.plugins.upload_ee import UploadEePlugin
from surfload.plugins.vikingfile import VikingfilePlugin


def test_plugin_registry_contains_extended_plugins() -> None:
    registry = get_plugin_registry()
    assert "catbox" in registry
    assert "tmpfiles_org" in registry
    assert "onefichier" in registry
    assert "gofile" in registry
    assert "send_now" in registry
    assert "upload_ee" in registry
    assert "vikingfile" in registry


def test_catbox_finalize_parses_text_link() -> None:
    plugin = CatboxPlugin()
    result = plugin.finalize("https://files.catbox.moe/demo.txt\n", metadata={})
    assert result == "https://files.catbox.moe/demo.txt"


def test_catbox_finalize_raises_on_invalid_response() -> None:
    plugin = CatboxPlugin()
    with pytest.raises(UploadError):
        plugin.finalize("error: upload failed", metadata={})


def test_tmpfiles_finalize_parses_nested_json_link() -> None:
    plugin = TmpfilesOrgPlugin()
    response = {"status": "success", "data": {"url": "https://tmpfiles.org/dl/123/demo.bin"}}
    result = plugin.finalize(response, metadata={})
    assert result == "https://tmpfiles.org/dl/123/demo.bin"


def test_tmpfiles_finalize_raises_when_link_missing() -> None:
    plugin = TmpfilesOrgPlugin()
    with pytest.raises(UploadError):
        plugin.finalize({"status": "success", "data": {}}, metadata={})


def test_onefichier_finalize_accepts_plain_text_link() -> None:
    plugin = OneFichierPlugin()
    result = plugin.finalize("https://1fichier.com/?abc123\n", metadata={})
    assert result == "https://1fichier.com/?abc123"


def test_onefichier_finalize_builds_link_from_id() -> None:
    plugin = OneFichierPlugin()
    result = plugin.finalize({"data": {"id": "xyz987"}}, metadata={})
    assert result == "https://1fichier.com/?xyz987"


def test_gofile_finalize_parses_download_page() -> None:
    plugin = GofilePlugin()
    response = {"status": "ok", "data": {"downloadPage": "https://gofile.io/d/abcdEF"}}
    result = plugin.finalize(response, metadata={})
    assert result == "https://gofile.io/d/abcdEF"


def test_gofile_resume_is_opt_in() -> None:
    disabled = GofilePlugin(host_config={})
    enabled = GofilePlugin(host_config={"enable_resume": True})

    assert disabled.supports_resume() is False
    assert enabled.supports_resume() is True


def test_gofile_get_resume_offset_prefers_content_length() -> None:
    plugin = GofilePlugin(host_config={"resume_probe_url_template": "https://example.invalid/{filename}"})

    class Response:
        status_code = 200
        headers = {"Content-Length": "2048"}
        text = ""

    plugin._request = lambda *_args, **_kwargs: Response()  # type: ignore[method-assign]

    offset = plugin.get_resume_offset(Path("demo.bin"))
    assert offset == 2048


def test_gofile_upload_sets_content_range_for_resume(tmp_path: Path) -> None:
    source = tmp_path / "gofile-resume.bin"
    source.write_bytes(b"0123456789")

    plugin = GofilePlugin(host_config={"upload_url": "https://store1.gofile.io/uploadFile"})

    captured: dict[str, object] = {}

    class Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"status": "ok", "data": {"downloadPage": "https://gofile.io/d/demo"}}

    def fake_upload_request(method: str, url: str, **kwargs):
        _ = method
        _ = url
        captured["headers"] = kwargs.get("headers") or {}
        body = kwargs.get("data")
        captured["body_len"] = len(b"".join(iter(body))) if body is not None else 0
        return Response()

    plugin._upload_request = fake_upload_request  # type: ignore[method-assign]

    class Stream:
        progress_callback = None
        chunk_size = 1024

    plugin.upload_file(
        stream=Stream(),
        size=6,
        metadata={
            "path": source,
            "filename": source.name,
            "mime_type": "application/octet-stream",
            "start_offset": 4,
            "remaining_size": 6,
            "file_size": 10,
        },
    )

    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["Content-Range"] == "bytes 4-9/10"
    assert int(headers["Content-Length"]) == int(captured["body_len"])


def test_send_now_finalize_parses_nested_link() -> None:
    plugin = SendNowPlugin()
    response = {"ok": True, "data": {"url": "https://send.now/abcde"}}
    result = plugin.finalize(response, metadata={})
    assert result == "https://send.now/abcde"


def test_upload_ee_finalize_builds_default_link_from_code() -> None:
    plugin = UploadEePlugin()
    response = {"code": "qwerty12"}
    result = plugin.finalize(response, metadata={})
    assert result == "https://upload.ee/files/qwerty12.html"


def test_vikingfile_finalize_builds_default_link_from_id() -> None:
    plugin = VikingfilePlugin()
    response = {"data": {"id": "abcd1234"}}
    result = plugin.finalize(response, metadata={})
    assert result == "https://vikingfile.com/f/abcd1234"


def test_vikingfile_resolves_upload_url_from_server_api() -> None:
    plugin = VikingfilePlugin(host_config={"upload_url": "", "server_api_url": "https://vikingfile.com/api/get-server"})

    class Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"server": "https://s1.vikingfile.com/upload"}

    plugin._request = lambda *_args, **_kwargs: Response()  # type: ignore[method-assign]

    assert plugin._resolve_upload_url() == "https://s1.vikingfile.com/upload"


def test_vikingfile_upload_raises_clear_error_on_html_response(tmp_path: Path) -> None:
    source = tmp_path / "viking-upload.bin"
    source.write_bytes(b"abc")

    plugin = VikingfilePlugin(host_config={"upload_url": "https://example.invalid/upload"})

    class Response:
        status_code = 200
        text = "<html><body>File not found</body></html>"

        @staticmethod
        def json() -> dict[str, str]:
            raise ValueError("not json")

    plugin._upload_request = lambda *_args, **_kwargs: Response()  # type: ignore[method-assign]

    class Stream:
        progress_callback = None
        chunk_size = 1024

    with pytest.raises(UploadError, match="returned HTML instead of API JSON"):
        plugin.upload_file(
            stream=Stream(),
            size=3,
            metadata={
                "path": source,
                "filename": source.name,
                "mime_type": "application/octet-stream",
                "start_offset": 0,
                "remaining_size": 3,
                "file_size": 3,
            },
        )


def test_vikingfile_upload_uses_api_key_as_user_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "viking-user.bin"
    source.write_bytes(b"abc")

    captured: dict[str, object] = {}

    class FakeMultipart:
        def __init__(self, **kwargs):
            captured["form_data"] = kwargs.get("form_data") or {}

        def __len__(self) -> int:
            return 4

        def __iter__(self):
            yield b"body"

    monkeypatch.setattr("surfload.plugins.vikingfile.MultipartStream", FakeMultipart)

    plugin = VikingfilePlugin(host_config={"upload_url": "https://example.invalid/upload"})
    plugin.auth({"api_key": "my-api-key"})

    class Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"data": {"id": "demo"}}

    plugin._upload_request = lambda *_args, **_kwargs: Response()  # type: ignore[method-assign]

    class Stream:
        progress_callback = None
        chunk_size = 1024

    plugin.upload_file(
        stream=Stream(),
        size=3,
        metadata={
            "path": source,
            "filename": source.name,
            "mime_type": "application/octet-stream",
            "start_offset": 0,
            "remaining_size": 3,
            "file_size": 3,
        },
    )

    form_data = captured["form_data"]
    assert isinstance(form_data, dict)
    assert form_data["user"] == "my-api-key"


def test_base_upload_path_respects_start_offset(tmp_path: Path) -> None:
    source = tmp_path / "offset.bin"
    payload = b"0123456789"
    source.write_bytes(payload)

    class CapturePlugin(BaseHostPlugin):
        def upload_file(self, stream, size: int, metadata):
            uploaded = b""
            while True:
                chunk = stream.read(-1)
                if not chunk:
                    break
                uploaded += chunk
            return {
                "uploaded": uploaded,
                "size": size,
                "metadata": metadata,
            }

        def finalize(self, response_data, metadata):
            _ = metadata
            return "https://example.invalid/download"

    plugin = CapturePlugin()
    progressed = 0

    def callback(sent: int) -> None:
        nonlocal progressed
        progressed += sent

    result = plugin.upload_path(
        file_path=source,
        chunk_size=4,
        progress_callback=callback,
        start_offset=3,
    )

    raw = result.raw_response
    assert raw["uploaded"] == payload[3:]
    assert raw["size"] == len(payload) - 3
    assert raw["metadata"]["start_offset"] == 3
    assert raw["metadata"]["remaining_size"] == len(payload) - 3
    assert progressed == len(payload)


def test_dummy_local_upload_sets_content_range_for_resume() -> None:
    plugin = DummyLocalPlugin(host_config={"upload_url": "http://127.0.0.1:8765/upload"})

    captured: dict[str, dict[str, str]] = {}

    class Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"url": "http://127.0.0.1:8765/files/demo.bin"}

    def fake_upload_request(method: str, url: str, **kwargs):
        _ = method
        _ = url
        captured["headers"] = kwargs.get("headers") or {}
        return Response()

    plugin._upload_request = fake_upload_request  # type: ignore[method-assign]

    plugin.upload_file(
        stream=b"abcdef",
        size=6,
        metadata={"filename": "demo.bin", "start_offset": 4, "file_size": 10},
    )

    headers = captured["headers"]
    assert headers["Content-Length"] == "6"
    assert headers["Content-Range"] == "bytes 4-9/10"


def test_buzzheavier_resume_is_opt_in() -> None:
    disabled = BuzzheavierPlugin(host_config={})
    enabled = BuzzheavierPlugin(host_config={"enable_resume": True})

    assert disabled.supports_resume() is False
    assert enabled.supports_resume() is True


def test_buzzheavier_get_resume_offset_prefers_content_length() -> None:
    plugin = BuzzheavierPlugin(host_config={"upload_base_url": "https://w.buzzheavier.com"})

    class Response:
        status_code = 200
        headers = {"Content-Length": "777"}
        text = ""

    plugin._request = lambda *_args, **_kwargs: Response()  # type: ignore[method-assign]

    offset = plugin.get_resume_offset(Path("demo.bin"))
    assert offset == 777


def test_buzzheavier_upload_sets_content_range_for_resume() -> None:
    plugin = BuzzheavierPlugin(host_config={"upload_base_url": "https://w.buzzheavier.com"})

    captured: dict[str, dict[str, str]] = {}

    class Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"url": "https://buzzheavier.com/demo"}

    def fake_upload_request(method: str, url: str, **kwargs):
        _ = method
        _ = url
        captured["headers"] = kwargs.get("headers") or {}
        return Response()

    plugin._upload_request = fake_upload_request  # type: ignore[method-assign]

    plugin.upload_file(
        stream=b"abcdef",
        size=6,
        metadata={"filename": "demo.bin", "start_offset": 4, "file_size": 10},
    )

    headers = captured["headers"]
    assert headers["Content-Length"] == "6"
    assert headers["Content-Range"] == "bytes 4-9/10"


def test_transfer_sh_resume_is_opt_in() -> None:
    disabled = TransferShPlugin(host_config={})
    enabled = TransferShPlugin(host_config={"enable_resume": True})

    assert disabled.supports_resume() is False
    assert enabled.supports_resume() is True


def test_transfer_sh_get_resume_offset_prefers_content_length() -> None:
    plugin = TransferShPlugin(host_config={"upload_url": "https://transfer.sh"})

    class Response:
        status_code = 200
        headers = {"Content-Length": "12345"}
        text = ""

    plugin._request = lambda *_args, **_kwargs: Response()  # type: ignore[method-assign]

    offset = plugin.get_resume_offset(Path("demo.bin"))
    assert offset == 12345


def test_transfer_sh_upload_sets_content_range_for_resume() -> None:
    plugin = TransferShPlugin(host_config={"upload_url": "https://transfer.sh"})

    captured: dict[str, dict[str, str]] = {}

    class Response:
        status_code = 200
        text = "https://transfer.sh/demo.bin"

    def fake_upload_request(method: str, url: str, **kwargs):
        _ = method
        _ = url
        captured["headers"] = kwargs.get("headers") or {}
        return Response()

    plugin._upload_request = fake_upload_request  # type: ignore[method-assign]

    plugin.upload_file(
        stream=b"abcdef",
        size=6,
        metadata={"filename": "demo.bin", "start_offset": 4, "file_size": 10},
    )

    headers = captured["headers"]
    assert headers["Content-Length"] == "6"
    assert headers["Content-Range"] == "bytes 4-9/10"
