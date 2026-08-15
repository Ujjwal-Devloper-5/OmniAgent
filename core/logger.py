"""
Structured JSON logging with log rotation.
All modules should use `from core.logger import get_logger`.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


class _ColorFormatter(logging.Formatter):
    """ANSI colour formatter for console output."""

    GREY = "\x1b[38;20m"
    GREEN = "\x1b[32;20m"
    YELLOW = "\x1b[33;20m"
    RED = "\x1b[31;20m"
    BOLD_RED = "\x1b[31;1m"
    RESET = "\x1b[0m"

    FORMATS: dict[int, str] = {
        logging.DEBUG: GREY,
        logging.INFO: GREEN,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: BOLD_RED,
    }

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        color = self.FORMATS.get(record.levelno, self.GREY)
        fmt = (
            f"{color}%(asctime)s{self.RESET} "
            f"%(levelname)-8s "
            f"\x1b[36m%(name)s\x1b[0m "
            f"%(message)s"
        )
        formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")
        return formatter.format(record)


def _setup_root_logger(level: str, log_file: str, max_bytes: int, backup_count: int) -> None:
    """Configure the root logger once at application startup."""
    root = logging.getLogger()
    root.setLevel(level)

    if root.handlers:
        return  # Already configured

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(_ColorFormatter())
    root.addHandler(console_handler)

    # Rotating file handler
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_fmt)
    root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """Get a named logger. Call _setup_root_logger first."""
    return logging.getLogger(name)


def init_logging() -> None:
    """Initialise logging from settings. Call once at startup."""
    from config import settings

    _setup_root_logger(
        level=settings.log_level,
        log_file=settings.log_file,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
    )
