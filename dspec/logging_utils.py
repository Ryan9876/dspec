from __future__ import annotations

import logging
from typing import Iterable

from .security import sanitize


class RedactingFormatter(logging.Formatter):
    """Delegate to the handler's native formatter, then redact secrets."""

    def __init__(self, delegate: logging.Formatter | None) -> None:
        super().__init__()
        self.delegate = delegate

    def format(self, record: logging.LogRecord) -> str:
        if self.delegate is None:
            rendered = record.getMessage()
        else:
            rendered = self.delegate.format(record)
        return sanitize(rendered)


def configure_logging(logger_names: Iterable[str] = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "fastapi", "dspec")) -> None:
    """Wrap existing handlers so custom Uvicorn formatters remain intact."""
    seen: set[int] = set()
    for name in logger_names:
        logger = logging.getLogger(name)
        for handler in logger.handlers:
            if id(handler) in seen:
                continue
            seen.add(id(handler))
            if isinstance(handler.formatter, RedactingFormatter):
                continue
            handler.setFormatter(RedactingFormatter(handler.formatter))
