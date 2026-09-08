from __future__ import annotations

import logging
import re

_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}"),
    re.compile(r"Bearer\s+[^\s]+", re.IGNORECASE),
]

class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for pattern in _PATTERNS:
            message = pattern.sub("[REDACTED_API_KEY]", message)
        record.msg = message
        record.args = ()
        return True
