from __future__ import annotations

import json
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from tqdm import tqdm

from .plugins import PluginDescriptor, get_plugin_registry
from .plugins.base import UploadError
from .utils.credentials import CredentialStore, CredentialsError


@dataclass
class UploadResult:
    host: str
    file_path: str
    success: bool
    file_size_bytes: int = 0
    download_url: Optional[str] = None
    error: Optional[str] = None
    raw_response: Any = None
    attempts: int = 1
    duration_seconds: float = 0.0
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UploadTask:
    host: str
    file_path: Path
    account_name: Optional[str]


class UploadManager:
    def __init__(
        self,
        config: Dict[str, Any],
        credential_store: CredentialStore,
        logger: Any,
    ) -> None:
        self.config = config
        self.credential_store = credential_store
        self.logger = logger
        self.registry = get_plugin_registry()

    def list_hosts(self) -> List[PluginDescriptor]:
        return sorted(self.registry.values(), key=lambda item: item.key)

    def validate_hosts(self, hosts: Iterable[str]) -> List[str]:
        normalized: List[str] = []
        for host in hosts:
            key = host.strip().lower()
            if key not in self.registry:
                raise ValueError(f"Unknown host: {host}")
            if key not in normalized:
                normalized.append(key)
        if not normalized:
            raise ValueError("No hosts selected")
        return normalized

    def upload(
        self,
        files: List[Path],
        hosts: List[str],
        parallelism: int,
        chunk_size: int,
        retries: int,
        backoff_base_seconds: int,
        backoff_max_seconds: int,
        account_selection: Optional[Dict[str, str]] = None,
        master_password: Optional[str] = None,
        show_progress: bool = True,
        resume_on_retry: bool = True,
    ) -> List[UploadResult]:
        host_keys = self.validate_hosts(hosts)
        file_paths = [path.expanduser().resolve() for path in files]
        if not file_paths:
            raise ValueError("No files provided")

        for path in file_paths:
            if not path.exists() or not path.is_file():
                raise FileNotFoundError(path)

        tasks: List[UploadTask] = []
        account_selection = account_selection or {}
        for host in host_keys:
            for path in file_paths:
                tasks.append(UploadTask(host=host, file_path=path, account_name=account_selection.get(host)))

        aggregate = tqdm(
            total=len(tasks),
            desc="Gesamt",
            unit="job",
            leave=True,
            disable=not show_progress,
        )

        results: List[UploadResult] = []
        max_workers = max(1, int(parallelism))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map: Dict[Future[UploadResult], UploadTask] = {
                executor.submit(
                    self._run_task,
                    task=task,
                    retries=max(1, int(retries)),
                    chunk_size=max(64 * 1024, int(chunk_size)),
                    backoff_base_seconds=max(1, int(backoff_base_seconds)),
                    backoff_max_seconds=max(1, int(backoff_max_seconds)),
                    master_password=master_password,
                    show_progress=show_progress,
                    resume_on_retry=bool(resume_on_retry),
                ): task
                for task in tasks
            }

            for future in as_completed(future_map):
                result = future.result()
                results.append(result)
                aggregate.update(1)

                file_name = Path(result.file_path).name
                if result.success:
                    self.logger.info("OK [%s] %s -> %s", result.host, file_name, result.download_url)
                else:
                    self.logger.error("ERR [%s] %s -> %s", result.host, file_name, result.error)

        aggregate.close()
        return sorted(results, key=lambda item: (item.file_path, item.host))

    def _run_task(
        self,
        task: UploadTask,
        retries: int,
        chunk_size: int,
        backoff_base_seconds: int,
        backoff_max_seconds: int,
        master_password: Optional[str],
        show_progress: bool,
        resume_on_retry: bool,
    ) -> UploadResult:
        descriptor = self.registry[task.host]
        plugin_config = self._build_plugin_config(task.host)
        plugin = descriptor.cls(host_config=plugin_config, logger=self.logger)

        started_dt = datetime.now(timezone.utc)
        started_ts = time.monotonic()
        file_size = task.file_path.stat().st_size

        file_bar = tqdm(
            total=file_size,
            desc=f"{task.host}:{task.file_path.name}",
            unit="B",
            unit_scale=True,
            leave=False,
            disable=not show_progress,
        )

        last_error: Exception | None = None
        attempts_done = 0

        try:
            plugin.init()
            account_data = self._resolve_account_data(
                host=task.host,
                account_name=task.account_name,
                master_password=master_password,
            )
            plugin.auth(account_data)

            for attempt in range(1, retries + 1):
                attempts_done = attempt
                if attempt > 1:
                    file_bar.reset(total=file_size)

                start_offset = 0
                can_resume = resume_on_retry and attempt > 1 and plugin.supports_resume()
                if can_resume:
                    try:
                        raw_offset = int(
                            plugin.get_resume_offset(
                                task.file_path,
                                metadata={
                                    "attempt": attempt,
                                    "file_size": file_size,
                                    "host": task.host,
                                },
                            )
                            or 0
                        )
                        start_offset = max(0, min(raw_offset, file_size))
                        if file_size > 0 and start_offset >= file_size:
                            start_offset = 0
                        if start_offset > 0:
                            self.logger.info(
                                "Resuming %s on %s from byte %s",
                                task.file_path.name,
                                task.host,
                                start_offset,
                            )
                    except Exception as resume_exc:  # noqa: BLE001
                        start_offset = 0
                        self.logger.warning(
                            "Resume offset lookup failed for %s on %s: %s",
                            task.file_path.name,
                            task.host,
                            resume_exc,
                        )

                try:
                    plugin_result = plugin.upload_path(
                        file_path=task.file_path,
                        chunk_size=chunk_size,
                        progress_callback=lambda sent: file_bar.update(sent),
                        start_offset=start_offset,
                    )
                    finished_dt = datetime.now(timezone.utc)
                    return UploadResult(
                        host=task.host,
                        file_path=str(task.file_path),
                        success=True,
                        file_size_bytes=file_size,
                        download_url=plugin_result.download_url,
                        raw_response=plugin_result.raw_response,
                        attempts=attempt,
                        duration_seconds=round(time.monotonic() - started_ts, 3),
                        started_at=started_dt.isoformat(),
                        finished_at=finished_dt.isoformat(),
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    if attempt >= retries:
                        break

                    wait_seconds = min(backoff_base_seconds * (2 ** (attempt - 1)), backoff_max_seconds)
                    self.logger.warning(
                        "Retry %s/%s for %s on %s in %ss: %s",
                        attempt,
                        retries,
                        task.file_path.name,
                        task.host,
                        wait_seconds,
                        exc,
                    )
                    time.sleep(wait_seconds)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            attempts_done = max(attempts_done, 1)
        finally:
            file_bar.close()

        finished_dt = datetime.now(timezone.utc)
        message = str(last_error) if last_error else "Unknown upload error"
        if isinstance(last_error, CredentialsError):
            message = f"Credentials error: {message}"
        elif isinstance(last_error, UploadError):
            message = f"Upload error: {message}"

        return UploadResult(
            host=task.host,
            file_path=str(task.file_path),
            success=False,
            file_size_bytes=file_size,
            error=message,
            attempts=attempts_done,
            duration_seconds=round(time.monotonic() - started_ts, 3),
            started_at=started_dt.isoformat(),
            finished_at=finished_dt.isoformat(),
        )

    def _build_plugin_config(self, host: str) -> Dict[str, Any]:
        merged: Dict[str, Any] = {}
        defaults = self.config.get("host_defaults", {}).get(host, {})
        host_specific = self.config.get("hosts", {}).get(host, {})
        if isinstance(defaults, dict):
            merged.update(defaults)
        if isinstance(host_specific, dict):
            merged.update(host_specific)

        merged.setdefault("timeout", int(self.config.get("timeout", 120)))
        merged.setdefault("retries", int(self.config.get("retries", 3)))
        merged.setdefault("backoff_factor", float(self.config.get("backoff_base_seconds", 1)))
        return merged

    def _resolve_account_data(
        self,
        host: str,
        account_name: Optional[str],
        master_password: Optional[str],
    ) -> Dict[str, Any]:
        try:
            accounts = self.credential_store.list_accounts(host, master_password=master_password)
        except CredentialsError:
            raise

        if not accounts:
            return {}

        if account_name:
            for item in accounts:
                if item.get("name") == account_name:
                    return dict(item.get("data") or {})
            raise CredentialsError(f"Account '{account_name}' not found for host '{host}'")

        return dict(accounts[0].get("data") or {})

    @staticmethod
    def print_results(results: List[UploadResult]) -> int:
        success = [item for item in results if item.success]
        failed = [item for item in results if not item.success]

        print("\n=== Upload fertig ===")
        print(f"Erfolgreich: {len(success)} | Fehlgeschlagen: {len(failed)}")

        print("\n=== Download-Links ===")
        if success:
            for item in success:
                host = item.host
                file_name = Path(item.file_path).name
                print(f"[{host}] {file_name}: {item.download_url}")
        else:
            print("Keine erfolgreichen Uploads.")

        if failed:
            print("\n=== Fehler ===")
            for item in failed:
                file_name = Path(item.file_path).name
                print(f"[{item.host}] {file_name}: {item.error}")

        return 0 if not failed else 2

    @staticmethod
    def export_results_json(results: List[UploadResult], target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = [item.to_dict() for item in results]
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return target

    @staticmethod
    def export_summary_text(results: List[UploadResult], target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        lines: List[str] = []
        lines.append("surfload export")
        lines.append(f"generated: {datetime.now(timezone.utc).isoformat()}")
        lines.append("")

        sorted_results = sorted(
            results,
            key=lambda item: (
                item.host,
                UploadManager._natural_sort_key(Path(item.file_path).name),
                item.file_path,
            ),
        )

        grouped: list[list[UploadResult]] = []
        current_group: list[UploadResult] = []
        current_host = ""
        current_base: str | None = None

        for item in sorted_results:
            file_name = Path(item.file_path).name
            base_name, _part_number, _part_width = UploadManager._split_part_suffix(file_name)
            group_base = base_name if _part_number is not None else file_name

            if not current_group or item.host != current_host or group_base != current_base:
                if current_group:
                    grouped.append(current_group)
                current_group = [item]
                current_host = item.host
                current_base = group_base
            else:
                current_group.append(item)

        if current_group:
            grouped.append(current_group)

        for group in grouped:
            host = group[0].host
            file_names = [Path(item.file_path).name for item in group]
            header_label = UploadManager._format_group_header(file_names)
            lines.append(f"[{host}] {header_label}")

            for item in group:
                size_label = UploadManager._format_size_label(item.file_size_bytes)
                if item.success and item.download_url:
                    lines.append(f"{item.download_url}  - {size_label}")
                else:
                    file_name = Path(item.file_path).name
                    lines.append(f"ERROR {file_name}: {item.error or 'Unknown upload error'}  - {size_label}")
            lines.append("")

        target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return target

    @staticmethod
    def _natural_sort_key(text: str) -> tuple[tuple[int, Any], ...]:
        parts = re.split(r"(\d+)", text.lower())
        key: list[tuple[int, Any]] = []
        for part in parts:
            if not part:
                continue
            if part.isdigit():
                key.append((0, int(part)))
            else:
                key.append((1, part))
        return tuple(key)

    @staticmethod
    def _split_part_suffix(file_name: str) -> tuple[str, int | None, int]:
        match = re.match(r"^(.*)\.(\d+)$", file_name)
        if not match:
            return file_name, None, 0
        part_text = match.group(2)
        return match.group(1), int(part_text), len(part_text)

    @staticmethod
    def _format_group_header(file_names: list[str]) -> str:
        if not file_names:
            return ""

        parsed = [UploadManager._split_part_suffix(name) for name in file_names]
        if all(part is not None for _, part, _ in parsed):
            base_name = parsed[0][0]
            if all(base == base_name for base, _, _ in parsed):
                part_numbers = sorted(int(part or 0) for _, part, _ in parsed)
                width = max((width for _, _, width in parsed), default=1)
                first_part = str(part_numbers[0]).zfill(width)
                last_part = str(part_numbers[-1]).zfill(width)
                if first_part == last_part:
                    return f"{base_name}.{first_part}"
                return f"{base_name}.{first_part}-{last_part}"

        return file_names[0]

    @staticmethod
    def _format_size_label(size_bytes: int) -> str:
        size = max(0, int(size_bytes or 0))
        gib = 1024 * 1024 * 1024
        mib = 1024 * 1024
        kib = 1024

        if size >= gib:
            value = size / gib
            return f"{value:.2f}".replace(".", ",") + " GB"
        if size >= mib:
            value = size / mib
            return f"{value:.2f}".replace(".", ",") + " MB"
        if size >= kib:
            value = size / kib
            return f"{value:.2f}".replace(".", ",") + " KB"
        return f"{size} B"
