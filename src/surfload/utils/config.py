from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - depends on runtime environment
    yaml = None

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "surfload" / "config.yaml"

DEFAULT_CONFIG: Dict[str, Any] = {
    "parallelism": 3,
    "chunk_size": 1024 * 1024,
    "timeout": 120,
    "retries": 3,
    "resume_on_retry": True,
    "backoff_base_seconds": 1,
    "backoff_max_seconds": 60,
    "compression": "none",
    "credential_backend": "file",
    "log_level": "INFO",
    "host_defaults": {
        "catbox": {
            "upload_url": "https://catbox.moe/user/api.php",
        },
        "tmpfiles_org": {
            "upload_url": "https://tmpfiles.org/api/v1/upload",
        },
        "dailyuploads": {
            "upload_url": "",
            "server_url": "https://dailyuploads.net/server",
        },
        "fileq": {
            "api_base": "https://fileq.net/api",
            "utype": "prem",
            "folder_id": "",
        },
        "megaup": {
            "bootstrap_url": "https://megaup.net/",
            "file_field": "files[]",
            "token_field": "token",
            "use_bearer_auth": False,
            "max_chunk_size": 100000000,
            "folder_id": "",
        },
        "gofile": {
            "upload_url": "https://upload.gofile.io/uploadfile",
            "server_api_url": "https://api.gofile.io/servers",
            "upload_path": "/uploadFile",
            "folder_id": "",
            "use_bearer_auth": False,
            "enable_resume": False,
            "resume_probe_url_template": "",
        },
        "send_now": {
            "upload_url": "",
            "homepage_url": "https://send.now/",
        },
        "upload_ee": {
            "bootstrap_url": "",
            "upload_url_template": "https://www.upload.ee/cgi-bin/ubr_upload.pl?X-Progress-ID={upload_id}&upload_id={upload_id}",
            "finished_url_template": "https://www.upload.ee/?page=finishedsimple&upload_id={upload_id}",
        },
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Path | None = None) -> Dict[str, Any]:
    config_path = (path or DEFAULT_CONFIG_PATH).expanduser().resolve()
    if not config_path.exists():
        return copy.deepcopy(DEFAULT_CONFIG)

    text = config_path.read_text(encoding="utf-8")
    if yaml is not None:
        raw = yaml.safe_load(text) or {}
    else:
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "Config parsing failed. Install 'PyYAML' for YAML support, "
                "or use JSON syntax in config file."
            ) from exc

    if not isinstance(raw, dict):
        raise ValueError(f"Config must be a mapping: {config_path}")
    return _deep_merge(DEFAULT_CONFIG, raw)


def save_config(config: Dict[str, Any], path: Path | None = None) -> Path:
    config_path = (path or DEFAULT_CONFIG_PATH).expanduser().resolve()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if yaml is not None:
        serialized = yaml.safe_dump(config, sort_keys=False)
    else:
        serialized = json.dumps(config, indent=2, ensure_ascii=False)
    config_path.write_text(serialized, encoding="utf-8")
    return config_path


def set_config_value(config: Dict[str, Any], dotted_key: str, raw_value: str) -> Dict[str, Any]:
    target = config
    parts = [part.strip() for part in dotted_key.split(".") if part.strip()]
    if not parts:
        raise ValueError("Invalid config key")

    for part in parts[:-1]:
        if part not in target or not isinstance(target[part], dict):
            target[part] = {}
        target = target[part]

    value: Any
    low = raw_value.lower()
    if low in {"true", "false"}:
        value = low == "true"
    else:
        try:
            value = int(raw_value)
        except ValueError:
            try:
                value = float(raw_value)
            except ValueError:
                value = raw_value

    target[parts[-1]] = value
    return config
