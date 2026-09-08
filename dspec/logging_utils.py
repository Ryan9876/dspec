from __future__ import annotations

import logging
from typing import Iterable

from .security import sanitize


class SanitizingFormatter(logging.Formatter):
    """Render the final log line, then redact credential-shaped values."""

    def format(self, record: logging.LogRecord) -> str:
        return sanitize(super().format(record))


def configure_logging(logger_names: Iterable[str] = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "fastapi", "dspec")) -> None:
    """Apply redaction to existing console/file handlers without changing levels or destinations."""
    seen: set[int] = set()
    for name in logger_names:
        logger = logging.getLogger(name)
        for handler in logger.handlers:
            if id(handler) in seen:
                continue
            seen.add(id(handler))
            current = handler.formatter
            fmt = current._fmt if current else "%(asctime)s %(levelname)s %(name)s %(message)s"
            datefmt = current.datefmt if current else None
            handler.setFormatter(SanitizingFormatter(fmt=fmt, datefmt=datefmt))
