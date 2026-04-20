from __future__ import annotations

import argparse
import getpass
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import unquote

from .core import UploadManager
from .plugins import CAPABILITY_LABELS
from .utils.compression import prepare_upload_paths
from .utils.config import DEFAULT_CONFIG_PATH, load_config, save_config, set_config_value
from .utils.credentials import CredentialStore, CredentialsError
from .utils.logger import build_logger


HOST_ALIASES = {
    "transfer.sh": "transfer_sh",
    "transfer_sh": "transfer_sh",
    "file.io": "fileio",
    "fileio": "fileio",
    "catbox.moe": "catbox",
    "catbox": "catbox",
    "tmpfiles.org": "tmpfiles_org",
    "tmpfiles_org": "tmpfiles_org",
    "buzzheavier.com": "buzzheavier",
    "buzzheavier": "buzzheavier",
    "1fichier.com": "onefichier",
    "1fichier": "onefichier",
    "onefichier": "onefichier",
    "gofile.io": "gofile",
    "gofile": "gofile",
    "send.now": "send_now",
    "send_now": "send_now",
    "upload.ee": "upload_ee",
    "upload_ee": "upload_ee",
    "vikingfile.com": "vikingfile",
    "vikingfile": "vikingfile",
    "dummy-local": "dummy_local",
    "dummy_local": "dummy_local",
}


def _normalize_host_key(raw: str) -> str:
    key = raw.strip().lower()
    return HOST_ALIASES.get(key, key)


def _parse_host_args(values: Optional[List[str]]) -> List[str]:
    if not values:
        return []
    hosts: List[str] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if not part:
                continue
            hosts.append(_normalize_host_key(part))
    deduped: List[str] = []
    for host in hosts:
        if host not in deduped:
            deduped.append(host)
    return deduped


def _interactive_host_selection(manager: UploadManager) -> List[str]:
    descriptors = manager.list_hosts()
    print("Verfuegbare Hoster:")
    for index, descriptor in enumerate(descriptors, start=1):
        tags = ", ".join(descriptor.capability_tags) if descriptor.capability_tags else "-"
        print(f"  {index}) {descriptor.key:<12} [{tags}]")

    raw = input("Hoster auswaehlen (z.B. 1,3 oder fileio,transfer_sh): ").strip()
    if not raw:
        raise SystemExit("Keine Hoster ausgewaehlt.")

    selected: List[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if part.isdigit() and 1 <= int(part) <= len(descriptors):
            selected.append(descriptors[int(part) - 1].key)
        else:
            selected.append(_normalize_host_key(part))

    return manager.validate_hosts(selected)


def _gather_files(paths: List[str], recursive: bool) -> List[Path]:
    discovered: List[Path] = []
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            print(f"WARN: Pfad fehlt und wird uebersprungen: {path}")
            continue

        if path.is_file():
            discovered.append(path)
        elif path.is_dir():
            iterator = path.rglob("*") if recursive else path.glob("*")
            for child in iterator:
                if child.is_file():
                    discovered.append(child.resolve())

    unique = sorted(set(discovered))
    if not unique:
        raise SystemExit("Keine gueltigen Dateien gefunden")
    return unique


def _parse_account_map(values: Optional[List[str]]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for value in values or []:
        if ":" not in value:
            raise ValueError(f"Invalid --account value: {value}. Use host:account_name")
        host, account = value.split(":", 1)
        mapping[_normalize_host_key(host)] = account.strip()
    return mapping


def _resolve_master_password(args, store: CredentialStore, action_name: str) -> Optional[str]:
    if args.master_password:
        return args.master_password

    if store.backend == "keyring":
        return None

    if not store.exists() and action_name in {"upload", "list", "account-list"}:
        return None

    prompt = "Master-Passwort"
    if action_name == "account-add" and not store.exists():
        prompt = "Neues Master-Passwort (wird fuer credentials.enc genutzt)"

    return getpass.getpass(f"{prompt}: ")


def _manager_from_args(args) -> tuple[UploadManager, CredentialStore, Dict]:
    config = load_config(Path(args.config) if args.config else DEFAULT_CONFIG_PATH)
    logger = build_logger(log_level=str(config.get("log_level", "INFO")))

    backend = args.credential_backend or str(config.get("credential_backend", "file"))
    store = CredentialStore(backend=backend)

    manager = UploadManager(config=config, credential_store=store, logger=logger)
    return manager, store, config


def cmd_upload(args) -> int:
    manager, store, config = _manager_from_args(args)

    hosts = _parse_host_args(args.host)
    if hosts:
        hosts = manager.validate_hosts(hosts)
    else:
        hosts = _interactive_host_selection(manager)

    files = _gather_files(args.paths, recursive=args.recursive)
    account_map = _parse_account_map(args.account)

    master_password = _resolve_master_password(args, store, action_name="upload")

    compress_mode = args.compress or str(config.get("compression", "none"))
    archive_password = args.archive_password or ""
    if args.archive_password_prompt:
        archive_password = getpass.getpass("Archiv-Passwort (ZIP/7z): ")

    upload_paths, cleanup = prepare_upload_paths(
        files,
        compress_mode=compress_mode,
        archive_name=args.archive_name or "",
        archive_password=archive_password,
        archive_part_size=args.archive_part_size or "",
    )

    try:
        resume_on_retry = args.resume_on_retry
        if resume_on_retry is None:
            resume_on_retry = bool(config.get("resume_on_retry", True))

        results = manager.upload(
            files=upload_paths,
            hosts=hosts,
            parallelism=args.parallel or int(config.get("parallelism", 3)),
            chunk_size=args.chunk_size or int(config.get("chunk_size", 1024 * 1024)),
            retries=args.retries or int(config.get("retries", 3)),
            backoff_base_seconds=int(config.get("backoff_base_seconds", 1)),
            backoff_max_seconds=int(config.get("backoff_max_seconds", 60)),
            account_selection=account_map,
            master_password=master_password,
            show_progress=not args.no_progress,
            resume_on_retry=bool(resume_on_retry),
        )
    finally:
        cleanup()

    exit_code = manager.print_results(results)

    if args.json:
        print(json.dumps([item.to_dict() for item in results], indent=2, ensure_ascii=False))

    if args.json_file:
        target = manager.export_results_json(results, Path(args.json_file).expanduser().resolve())
        print(f"JSON export geschrieben: {target}")

    if args.export:
        target = manager.export_summary_text(results, Path(args.export).expanduser().resolve())
        print(f"Text export geschrieben: {target}")

    return exit_code


def cmd_list(args) -> int:
    manager, store, _ = _manager_from_args(args)
    descriptors = manager.list_hosts()
    provided_master_password = args.master_password if args.master_password else None

    print("Unterstuetzte Hoster:")
    for descriptor in descriptors:
        tags = descriptor.capability_tags
        tag_str = f" ({', '.join(tags)})" if tags else ""

        account_count = 0
        try:
            account_count = len(store.list_accounts(descriptor.key, master_password=provided_master_password))
        except CredentialsError:
            account_count = -1

        account_text = "accounts: ?" if account_count < 0 else f"accounts: {account_count}"
        print(f"- {descriptor.key:<12} {tag_str}  [{account_text}]")

    print("\nTag-Legende:")
    inverse = {value: key for key, value in CAPABILITY_LABELS.items()}
    for short, key in sorted(inverse.items()):
        print(f"  {short:<6} -> {key}")

    return 0


def cmd_account_add(args) -> int:
    manager, store, _ = _manager_from_args(args)
    host = _normalize_host_key(args.host)
    manager.validate_hosts([host])

    descriptor = manager.registry[host]
    account_name = args.name.strip() if args.name else "default"

    values: Dict[str, str] = {}
    for raw in args.field or []:
        if "=" not in raw:
            raise SystemExit(f"Invalid --field: {raw}. Use key=value")
        key, value = raw.split("=", 1)
        values[key.strip()] = value.strip()

    if args.interactive or not values:
        print(f"Accountdaten fuer {host} erfassen:")
        fields = descriptor.cls.account_fields or ["token"]
        for field in fields:
            if field in values:
                continue
            if any(secret in field.lower() for secret in ("password", "token", "key", "secret")):
                values[field] = getpass.getpass(f"  {field}: ")
            else:
                values[field] = input(f"  {field}: ").strip()

    master_password = _resolve_master_password(args, store, action_name="account-add")
    store.add_account(host=host, account_data=values, name=account_name, master_password=master_password)
    print(f"Account gespeichert: host={host}, name={account_name}")
    return 0


def cmd_account_remove(args) -> int:
    manager, store, _ = _manager_from_args(args)
    host = _normalize_host_key(args.host)
    manager.validate_hosts([host])

    master_password = _resolve_master_password(args, store, action_name="account-remove")
    removed = store.remove_account(host=host, name=args.name, master_password=master_password)
    if removed:
        print(f"Account entfernt: host={host}, name={args.name}")
        return 0

    print(f"Account nicht gefunden: host={host}, name={args.name}")
    return 1


def cmd_account_list(args) -> int:
    manager, store, _ = _manager_from_args(args)
    host_filter = _normalize_host_key(args.host) if args.host else None

    master_password = _resolve_master_password(args, store, action_name="account-list")

    hosts = [host_filter] if host_filter else [descriptor.key for descriptor in manager.list_hosts()]

    for host in hosts:
        if host not in manager.registry:
            continue
        print(f"[{host}]")
        accounts = store.list_accounts(host, master_password=master_password)
        if not accounts:
            print("  (keine Accounts)")
            continue
        for account in accounts:
            print(f"  - {account.get('name', 'unnamed')}")
    return 0


def cmd_config_show(args) -> int:
    config_path = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
    config = load_config(config_path)
    print(json.dumps(config, indent=2, ensure_ascii=False))
    return 0


def cmd_config_set(args) -> int:
    config_path = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
    config = load_config(config_path)

    key_aliases = {
        "parallel": "parallelism",
    }
    key = key_aliases.get(args.key, args.key)
    updated = set_config_value(config, key, args.value)
    target = save_config(updated, config_path)
    print(f"Config aktualisiert: {key}={args.value} ({target})")
    return 0


def _start_demo_server(port: int):
    root = Path(tempfile.mkdtemp(prefix="surfload_demo_server_"))

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send_json(self, status: int, payload: Dict):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_PUT(self):  # noqa: N802
            if not self.path.startswith("/upload/"):
                self._send_json(404, {"error": "not found"})
                return

            filename = unquote(self.path.split("/upload/", 1)[1]).strip("/")
            if not filename:
                self._send_json(400, {"error": "filename missing"})
                return

            length = int(self.headers.get("Content-Length", "0") or 0)
            target = root / filename

            content_range = self.headers.get("Content-Range", "")
            if content_range.startswith("bytes "):
                try:
                    range_value = content_range.split(" ", 1)[1]
                    bounds, _total = range_value.split("/", 1)
                    start_text, end_text = bounds.split("-", 1)
                    start = int(start_text)
                    end = int(end_text)
                except Exception:  # noqa: BLE001
                    self._send_json(400, {"error": "invalid Content-Range"})
                    return

                expected_len = max(0, end - start + 1)
                if expected_len != length:
                    self._send_json(400, {"error": "content length mismatch"})
                    return

                if not target.exists() and start != 0:
                    self._send_json(409, {"error": "resume offset mismatch"})
                    return

                if target.exists() and target.stat().st_size != start:
                    self._send_json(409, {"error": "resume offset mismatch"})
                    return

                mode = "r+b" if target.exists() else "wb"
                with target.open(mode) as handle:
                    handle.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        handle.write(chunk)
                        remaining -= len(chunk)
            else:
                with target.open("wb") as handle:
                    remaining = length
                    while remaining > 0:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        handle.write(chunk)
                        remaining -= len(chunk)

            url = f"http://127.0.0.1:{port}/files/{filename}"
            self._send_json(200, {"url": url})

        def do_HEAD(self):  # noqa: N802
            if not self.path.startswith("/upload/"):
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            filename = unquote(self.path.split("/upload/", 1)[1]).strip("/")
            target = root / filename
            if not target.exists():
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            uploaded = target.stat().st_size
            self.send_response(200)
            self.send_header("X-Uploaded-Bytes", str(uploaded))
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):  # noqa: N802
            if not self.path.startswith("/files/"):
                self._send_json(404, {"error": "not found"})
                return

            filename = unquote(self.path.split("/files/", 1)[1]).strip("/")
            target = root / filename
            if not target.exists():
                self._send_json(404, {"error": "missing"})
                return

            payload = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, root


def cmd_demo(args) -> int:
    manager, store, config = _manager_from_args(args)
    _ = store  # explicit to show this command still uses configured store backend.

    port = args.port or 8765
    server, root = _start_demo_server(port)

    sample_file = root / "demo-upload.txt"
    sample_file.write_text("Demo upload from surfload\n" * 2000, encoding="utf-8")

    override = dict(config)
    host_defaults = dict(config.get("host_defaults", {}))
    host_defaults["dummy_local"] = {
        "upload_url": f"http://127.0.0.1:{port}/upload",
        "timeout": 30,
    }
    override["host_defaults"] = host_defaults

    demo_manager = UploadManager(config=override, credential_store=store, logger=manager.logger)

    try:
        print(f"Demo server laeuft auf http://127.0.0.1:{port}")
        results = demo_manager.upload(
            files=[sample_file],
            hosts=["dummy_local"],
            parallelism=1,
            chunk_size=int(config.get("chunk_size", 1024 * 1024)),
            retries=2,
            backoff_base_seconds=1,
            backoff_max_seconds=5,
            account_selection={},
            master_password=None,
            show_progress=not args.no_progress,
        )
        return demo_manager.print_results(results)
    finally:
        server.shutdown()
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="surfload",
        description="Anfaengerfreundliches Multi-Hoster Upload-CLI fuer Ubuntu/Debian.",
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Pfad zur config.yaml")
    parser.add_argument(
        "--credential-backend",
        choices=["file", "keyring"],
        default=None,
        help="Credential backend (default aus config)",
    )
    parser.add_argument("--master-password", default="", help="Master-Passwort fuer credentials.enc")

    sub = parser.add_subparsers(dest="command", required=True)

    upload = sub.add_parser("upload", help="Dateien/Ordner hochladen")
    upload.add_argument("paths", nargs="+", help="Datei- oder Ordnerpfade")
    upload.add_argument("--host", action="append", help="Hostliste, z.B. fileio,transfer.sh")
    upload.add_argument("--account", action="append", help="Host-Account-Mapping host:name")
    upload.add_argument("--parallel", type=int, default=0, help="Parallele Upload-Worker")
    upload.add_argument("--chunk-size", type=int, default=0, help="Chunk-Groesse in Bytes")
    upload.add_argument("--retries", type=int, default=0, help="Anzahl Upload-Versuche")
    upload.add_argument("--compress", choices=["none", "auto", "zip", "7z"], default=None)
    upload.add_argument(
        "--archive-name",
        default="",
        help="Basisname fuer erzeugtes Archiv (ohne/mit .zip oder .7z)",
    )
    upload.add_argument(
        "--archive-password",
        default="",
        help="Passwort fuer ZIP/7z Archiv (nutzt 7z, wenn gesetzt)",
    )
    upload.add_argument(
        "--archive-password-prompt",
        action="store_true",
        help="Archiv-Passwort interaktiv eingeben (sicherer als Klartext-Arg)",
    )
    upload.add_argument(
        "--archive-part-size",
        default="",
        help="Archiv-Splitting pro Part, z.B. 500MB, 1GB, 1536MiB",
    )
    upload.add_argument("--recursive", action="store_true", help="Ordner rekursiv aufloesen")
    upload.add_argument("--json", action="store_true", help="JSON Ausgabe auf stdout")
    upload.add_argument("--json-file", default="", help="JSON Ausgabe in Datei schreiben")
    upload.add_argument("--export", default="", help="Textzusammenfassung in Datei")
    upload.add_argument("--no-progress", action="store_true", help="Progressbars deaktivieren")
    upload.add_argument(
        "--resume-on-retry",
        dest="resume_on_retry",
        action="store_true",
        default=None,
        help="Resume bei Retry aktivieren (wenn Plugin es unterstuetzt)",
    )
    upload.add_argument(
        "--no-resume-on-retry",
        dest="resume_on_retry",
        action="store_false",
        help="Resume bei Retry deaktivieren",
    )
    upload.set_defaults(func=cmd_upload)

    list_cmd = sub.add_parser("list", help="Hoster und Tags anzeigen")
    list_cmd.set_defaults(func=cmd_list)

    account = sub.add_parser("account", help="Accounts verwalten")
    account_sub = account.add_subparsers(dest="account_cmd", required=True)

    account_add = account_sub.add_parser("add", help="Account hinzufuegen")
    account_add.add_argument("host", help="Host-Key")
    account_add.add_argument("--name", default="default", help="Accountname")
    account_add.add_argument("--field", action="append", help="Wert als key=value")
    account_add.add_argument("--interactive", action="store_true", help="Felder interaktiv eingeben")
    account_add.set_defaults(func=cmd_account_add)

    account_remove = account_sub.add_parser("remove", help="Account loeschen")
    account_remove.add_argument("host", help="Host-Key")
    account_remove.add_argument("name", help="Accountname")
    account_remove.set_defaults(func=cmd_account_remove)

    account_list = account_sub.add_parser("list", help="Accounts anzeigen")
    account_list.add_argument("--host", default="", help="Optional nur ein Host")
    account_list.set_defaults(func=cmd_account_list)

    config_cmd = sub.add_parser("config", help="Konfiguration verwalten")
    config_sub = config_cmd.add_subparsers(dest="config_cmd", required=True)

    config_show = config_sub.add_parser("show", help="Config anzeigen")
    config_show.set_defaults(func=cmd_config_show)

    config_set = config_sub.add_parser("set", help="Config-Wert setzen")
    config_set.add_argument("key")
    config_set.add_argument("value")
    config_set.set_defaults(func=cmd_config_set)

    demo = sub.add_parser("demo", help="Demo mit lokalem Dummy-Hoster")
    demo.add_argument("--port", type=int, default=8765)
    demo.add_argument("--no-progress", action="store_true")
    demo.set_defaults(func=cmd_demo)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.master_password == "":
        args.master_password = None

    try:
        return int(args.func(args))
    except CredentialsError as exc:
        print(f"Credential-Fehler: {exc}")
        return 1
    except KeyboardInterrupt:
        print("Abbruch durch Nutzer.")
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"Fehler: {exc}")
        return 1
