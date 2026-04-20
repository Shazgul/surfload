from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def get_default_log_file() -> Path:
    return Path.home() / ".local" / "state" / "surfload" / "surfload.log"


def build_logger(log_level: str = "INFO", log_file: Path | None = None) -> logging.Logger:
    logger = logging.getLogger("surfload")
    if logger.handlers:
        return logger

    level = getattr(logging, str(log_level).upper(), logging.INFO)
    logger.setLevel(level)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    destination = log_file or get_default_log_file()
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        destination,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger
