from __future__ import annotations

import mimetypes
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Iterable, List, Tuple


SIZE_UNITS = {
    "": 1,
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "kib": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "mib": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
    "gib": 1024**3,
}


def _should_auto_compress(path: Path) -> bool:
    if path.is_dir():
        return True
    mime, _ = mimetypes.guess_type(path.name)
    if mime and mime.startswith("video/"):
        return True
    return path.suffix.lower() in {".mp4", ".mkv", ".mov", ".avi"}


def _collect_files(paths: Iterable[Path]) -> List[Path]:
    collected: List[Path] = []
    for path in paths:
        if path.is_dir():
            for child in path.rglob("*"):
                if child.is_file():
                    collected.append(child)
        elif path.is_file():
            collected.append(path)
    return collected


def parse_size_to_bytes(raw_value: str) -> int:
    text = str(raw_value or "").strip().lower().replace(" ", "")
    if not text:
        raise ValueError("Archive part size must not be empty")

    match = re.fullmatch(r"(\d+(?:\.\d+)?)([a-z]*)", text)
    if not match:
        raise ValueError(f"Invalid archive part size: {raw_value}")

    value = float(match.group(1))
    unit = match.group(2)
    if unit not in SIZE_UNITS:
        raise ValueError(f"Unsupported size unit in archive part size: {raw_value}")

    size_bytes = int(value * SIZE_UNITS[unit])
    if size_bytes <= 0:
        raise ValueError(f"Archive part size must be > 0: {raw_value}")
    return size_bytes


def normalize_archive_stem(raw_name: str) -> str:
    stem = Path(str(raw_name or "").strip()).name
    if not stem:
        return "upload_bundle"

    for ext in (".zip", ".7z"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break

    stem = stem.strip().strip(".")
    if not stem:
        raise ValueError("Archive name must contain visible characters")
    return stem


def _collect_archive_outputs(archive_path: Path) -> List[Path]:
    outputs: List[Path] = []
    if archive_path.exists() and archive_path.is_file():
        outputs.append(archive_path)

    split_parts = sorted(path for path in archive_path.parent.glob(f"{archive_path.name}.*") if path.is_file())
    for part in split_parts:
        if part not in outputs:
            outputs.append(part)

    if not outputs:
        raise RuntimeError(f"Archive creation did not produce output files: {archive_path}")
    return outputs


def create_archive_with_7z_cli(
    paths: List[Path],
    output_path: Path,
    archive_type: str,
    password: str = "",
    part_size_bytes: int = 0,
) -> List[Path]:
    seven_zip = shutil.which("7z")
    if not seven_zip:
        raise RuntimeError("7z not available. Install p7zip-full.")

    mode = archive_type.lower().strip()
    if mode not in {"zip", "7z"}:
        raise ValueError(f"Unsupported archive type for 7z CLI: {archive_type}")

    cmd = [seven_zip, "a", "-y", f"-t{mode}", str(output_path)]
    if password:
        cmd.append(f"-p{password}")
        if mode == "zip":
            cmd.append("-mem=AES256")
        else:
            cmd.append("-mhe=on")
    if part_size_bytes > 0:
        cmd.append(f"-v{int(part_size_bytes)}b")

    cmd.extend(str(path) for path in paths)
    completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"7z failed: {completed.stderr.strip() or completed.stdout.strip()}")

    return _collect_archive_outputs(output_path)


def create_zip_archive(paths: List[Path], output_path: Path) -> Path:
    files = _collect_files(paths)
    if not files:
        raise ValueError("No files to archive")

    roots: List[Tuple[Path, str]] = []
    for path in paths:
        if path.is_dir():
            root = path
            prefix = path.name
        else:
            root = path.parent
            prefix = ""
        entry = (root, prefix)
        if entry not in roots:
            roots.append(entry)

    def build_arcname(file_path: Path) -> Path:
        for root, prefix in roots:
            try:
                rel = file_path.relative_to(root)
                if prefix:
                    return Path(prefix) / rel
                return rel
            except ValueError:
                continue
        return Path(file_path.name)

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for file_path in files:
            arcname = build_arcname(file_path)
            archive.write(file_path, arcname=arcname.as_posix())
    return output_path


def create_7z_archive(
    paths: List[Path],
    output_path: Path,
    password: str = "",
    part_size_bytes: int = 0,
) -> List[Path]:
    if password or part_size_bytes > 0:
        return create_archive_with_7z_cli(
            paths,
            output_path,
            archive_type="7z",
            password=password,
            part_size_bytes=part_size_bytes,
        )

    try:
        import py7zr  # type: ignore

        with py7zr.SevenZipFile(output_path, mode="w") as archive:
            for path in paths:
                if path.is_dir():
                    archive.writeall(path, arcname=path.name)
                else:
                    archive.write(path, arcname=path.name)
        return [output_path]
    except Exception:
        pass

    return create_archive_with_7z_cli(paths, output_path, archive_type="7z")


def prepare_upload_paths(
    input_paths: List[Path],
    compress_mode: str,
    archive_name: str = "",
    archive_password: str = "",
    archive_part_size: str = "",
    keep_temp: bool = False,
) -> Tuple[List[Path], Callable[[], None]]:
    normalized = [path.expanduser().resolve() for path in input_paths]
    for path in normalized:
        if not path.exists():
            raise FileNotFoundError(path)

    mode = compress_mode.lower().strip()
    archive_stem = normalize_archive_stem(archive_name)
    password = str(archive_password or "")
    part_size_bytes = parse_size_to_bytes(archive_part_size) if str(archive_part_size or "").strip() else 0

    temp_dir = Path(tempfile.mkdtemp(prefix="surfload_"))

    def cleanup() -> None:
        if keep_temp:
            return
        shutil.rmtree(temp_dir, ignore_errors=True)

    if mode in {"none", "off", "false"}:
        return normalized, cleanup

    if mode == "auto":
        if not normalized:
            return normalized, cleanup
        should_compress = len(normalized) > 1 or any(_should_auto_compress(path) for path in normalized)
        if not should_compress:
            return normalized, cleanup
        mode = "zip"

    if mode == "zip":
        archive_path = temp_dir / f"{archive_stem}.zip"
        if password or part_size_bytes > 0:
            outputs = create_archive_with_7z_cli(
                normalized,
                archive_path,
                archive_type="zip",
                password=password,
                part_size_bytes=part_size_bytes,
            )
            return outputs, cleanup
        create_zip_archive(normalized, archive_path)
        return [archive_path], cleanup

    if mode == "7z":
        archive_path = temp_dir / f"{archive_stem}.7z"
        outputs = create_7z_archive(
            normalized,
            archive_path,
            password=password,
            part_size_bytes=part_size_bytes,
        )
        return outputs, cleanup

    raise ValueError(f"Unsupported compression mode: {compress_mode}")
