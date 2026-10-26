"""Configure process-wide logging once; modules use ordinary named loggers."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import RLock

from ._optional import require
from .errors import _cleanup

_lock = RLock()


def get_logger(name: str = "industrial_connect") -> logging.Logger:
    """Get a module logger, including before setup_logging() configures its outputs."""
    return logging.getLogger(name)


def setup_logging(
    app_name: str,
    *,
    level: int = logging.INFO,
    console: bool = True,
    file: str | Path | None = None,
    max_bytes: int = 10_000_000,
    backup_count: int = 3,
    graylog_host: str | None = None,
    graylog_port: int = 12201,
) -> logging.Logger:
    """Configure root outputs for all modules. Preserve handlers owned by other code."""
    if not app_name or app_name == "root":
        raise ValueError("An application logger name is required")
    if (
        any(type(value) is not int for value in (max_bytes, backup_count, graylog_port))
        or max_bytes <= 0
        or backup_count < 0
        or not 0 < graylog_port < 65536
    ):
        raise ValueError("Invalid rotation or Graylog settings")
    checked_level = logging.Logger(app_name, level=level).level
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handlers = []
    try:
        if console:
            handlers.append(logging.StreamHandler())
        if file is not None:
            handlers.append(
                RotatingFileHandler(
                    file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
                )
            )
        for handler in handlers:
            handler.setFormatter(formatter)
        if graylog_host:
            driver = require("graypy", "graylog")
            handlers.append(driver.GELFUDPHandler(graylog_host, graylog_port, facility=app_name))
    except BaseException:
        for handler in handlers:
            _cleanup(handler.close, "Logging handler initialization cleanup also failed")
        raise
    with _lock:
        root = logging.getLogger()
        old_handlers = []
        for handler in tuple(root.handlers):
            if getattr(handler, "_industrial_connect", False):
                root.removeHandler(handler)
                old_handlers.append(handler)
        for handler in handlers:
            handler._industrial_connect = True
            handler.setLevel(checked_level)
            root.addHandler(handler)
        root.setLevel(checked_level)
        logger = get_logger(app_name)
        logger.setLevel(logging.NOTSET)
        logger.propagate = True
        for handler in old_handlers:
            handler.close()
        return logger


def shutdown_logging() -> None:
    """After application workers stop, remove and close only this helper's outputs."""
    errors = []
    with _lock:
        root = logging.getLogger()
        for handler in tuple(root.handlers):
            if getattr(handler, "_industrial_connect", False):
                root.removeHandler(handler)
                try:
                    handler.close()
                except Exception as exc:
                    errors.append(exc)
    if errors:
        raise ExceptionGroup("Could not close logging outputs", errors)
