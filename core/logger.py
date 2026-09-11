"""
Structured JSON logging with log rotation and correlation ID propagation.
All modules should use `from core.logger import get_logger`.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterator, Optional

# ── Correlation ID Context Management ─────────────────────────────────────────

correlation_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "correlation_id", default=None
)

# Alias for alternate naming conventions
correlation_id_ctx = correlation_id_var


def get_correlation_id() -> Optional[str]:
    """Retrieve the active correlation ID from the task context, or None if unset."""
    return correlation_id_var.get()


def set_correlation_id(correlation_id: Optional[str]) -> contextvars.Token:
    """Set the correlation ID for the active context and return the reset token."""
    return correlation_id_var.set(correlation_id)


def reset_correlation_id(token: contextvars.Token) -> None:
    """Reset the correlation ID back to its previous value using the provided token."""
    correlation_id_var.reset(token)


@contextmanager
def correlation_id_scope(correlation_id: Optional[str] = None) -> Iterator[str]:
    """
    Context manager that sets a correlation ID for the duration of a block,
    restoring the previous context upon exit.

    If correlation_id is None, generates a new UUID4 string.
    """
    cid = correlation_id or str(uuid.uuid4())
    token = set_correlation_id(cid)
    try:
        yield cid
    finally:
        reset_correlation_id(token)


# ── Structured JSON Formatter ─────────────────────────────────────────────────

STANDARD_RECORD_ATTRS: frozenset[str] = frozenset({
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
    "correlation_id",
})


def _safe_str(obj: Any, max_len: int = 4096) -> str:
    """
    Convert any object to a safe string representation without raising exceptions.
    Guarantees no crash even if __str__, __repr__, or recursion fails.
    """
    try:
        s = str(obj)
    except Exception:
        try:
            s = repr(obj)
        except Exception:
            try:
                s = object.__repr__(obj)
            except Exception:
                s = f"<Unrepresentable {type(obj).__name__}>"
    if len(s) > max_len:
        s = s[:max_len] + "... [truncated]"
    return s


def _safe_serialize_extra_value(val: Any) -> Any:
    """Attempt direct JSON serialization; if it fails, fallback to safe string representation."""
    if val is None or isinstance(val, (bool, int, float, str)):
        return val
    try:
        json.dumps(val, default=str, ensure_ascii=False)
        return val
    except Exception:
        return _safe_str(val)


class JSONFormatter(logging.Formatter):
    """
    Structured JSON log formatter emitting single-line ISO-8601 UTC JSON objects.

    Fields:
    - timestamp: ISO-8601 UTC string ending with 'Z' (millisecond precision, e.g. YYYY-MM-DDTHH:MM:SS.mmmZ)
    - level: Uppercase log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    - logger: Logger name (e.g. core.model_router)
    - message: Formatted log message
    - correlation_id: Active correlation ID (or None if not set)
    - module: Originating module name
    - function: Originating function name
    - line: Originating line number
    - exception: Formatted traceback string if exc_info present, else None
    - stack_info: Formatted stack string if stack_info present, else None
    - Extra fields: Any custom attributes provided via extra={...}
    """

    def format(self, record: logging.LogRecord) -> str:
        utc_time = time.gmtime(record.created)
        msecs = int(record.msecs)
        timestamp_str = f"{time.strftime('%Y-%m-%dT%H:%M:%S', utc_time)}.{msecs:03d}Z"

        try:
            message = record.getMessage()
        except Exception:
            try:
                message = str(record.msg)
            except Exception:
                message = object.__repr__(record.msg)

        correlation_id = getattr(record, "correlation_id", None)
        if not correlation_id:
            correlation_id = get_correlation_id()
        if correlation_id is not None:
            correlation_id = _safe_str(correlation_id)

        exception_text = None
        if record.exc_info:
            try:
                exception_text = self.formatException(record.exc_info)
            except Exception:
                exception_text = _safe_str(record.exc_info)
        elif record.exc_text:
            exception_text = _safe_str(record.exc_text)

        base_payload: dict[str, Any] = {
            "timestamp": timestamp_str,
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "correlation_id": correlation_id,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "exception": exception_text,
        }

        if record.stack_info:
            try:
                base_payload["stack_info"] = self.formatStack(record.stack_info)
            except Exception:
                base_payload["stack_info"] = _safe_str(record.stack_info)

        extra_fields: list[tuple[Any, Any]] = []
        for key, val in record.__dict__.items():
            if key not in STANDARD_RECORD_ATTRS and not key.startswith("_") and key not in base_payload:
                extra_fields.append((key, val))

        # ── Stage 1: Fast path (direct serialization) ─────────────────────
        payload = dict(base_payload)
        for key, val in extra_fields:
            payload[key] = val

        try:
            return json.dumps(payload, default=str, ensure_ascii=False)
        except Exception:
            pass

        # ── Stage 2: Fallback path (sanitize extra fields) ────────────────
        try:
            safe_payload = dict(base_payload)
            for key, val in extra_fields:
                safe_key = _safe_str(key)
                safe_payload[safe_key] = _safe_serialize_extra_value(val)

            try:
                return json.dumps(safe_payload, default=str, ensure_ascii=False)
            except UnicodeEncodeError:
                return json.dumps(safe_payload, default=str, ensure_ascii=True)
        except Exception as stage2_exc:
            # ── Stage 3: Ultimate minimal fallback (guaranteed safe) ──────
            minimal_payload = dict(base_payload)
            minimal_payload["extra_error"] = f"Failed to serialize extra fields: {_safe_str(stage2_exc)}"
            return json.dumps(minimal_payload, default=str, ensure_ascii=True)


class _ColorFormatter(logging.Formatter):
    """Retained ANSI colour formatter for optional local console development."""

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

    def format(self, record: logging.LogRecord) -> str:
        color = self.FORMATS.get(record.levelno, self.GREY)
        fmt = (
            f"{color}%(asctime)s{self.RESET} "
            f"%(levelname)-8s "
            f"\x1b[36m%(name)s\x1b[0m "
            f"%(message)s"
        )
        formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")
        return formatter.format(record)


# ── Root Logger Initialisation ────────────────────────────────────────────────

def _setup_root_logger(
    level: str,
    log_file: str,
    max_bytes: int,
    backup_count: int,
    force_reconfigure: bool = False,
) -> None:
    """Configure the root logger with JSONFormatter for console and rotating file."""
    root = logging.getLogger()
    try:
        root.setLevel(level.upper())
    except Exception:
        root.setLevel(logging.INFO)

    if root.handlers and not force_reconfigure:
        return  # Already configured

    if force_reconfigure:
        for h in list(root.handlers):
            root.removeHandler(h)

    formatter = JSONFormatter()

    # Console handler (sys.stdout) emitting single-line JSON
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    # Rotating file handler emitting single-line JSON
    try:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except Exception as exc:
        sys.stderr.write(f"Warning: Failed to initialize file logger at {log_file}: {exc}\n")


def get_logger(name: str) -> logging.Logger:
    """Get a named logger. Call init_logging first."""
    return logging.getLogger(name)


def init_logging(force_reconfigure: bool = False) -> None:
    """Initialise logging from settings. Call once at application startup."""
    try:
        from config import settings
        _setup_root_logger(
            level=settings.log_level,
            log_file=settings.log_file,
            max_bytes=settings.log_max_bytes,
            backup_count=settings.log_backup_count,
            force_reconfigure=force_reconfigure,
        )
    except Exception as exc:
        # Fallback if config is not ready
        _setup_root_logger(
            level="INFO",
            log_file="logs/omniagent.log",
            max_bytes=10 * 1024 * 1024,
            backup_count=5,
            force_reconfigure=force_reconfigure,
        )


__all__ = [
    "JSONFormatter",
    "_ColorFormatter",
    "correlation_id_var",
    "correlation_id_ctx",
    "get_correlation_id",
    "set_correlation_id",
    "reset_correlation_id",
    "correlation_id_scope",
    "get_logger",
    "init_logging",
    "_setup_root_logger",
]
