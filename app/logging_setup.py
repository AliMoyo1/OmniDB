"""Structured JSON logging with a redaction backstop.

Application code must not log raw personal data (plan 9.8). This filter is a safety net
that scrubs long digit runs (phone-number shaped) from log text.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone

_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")


def _redact(text: str) -> str:
    return _PHONE_RE.sub("[redacted-number]", text)


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact(record.msg)
        if record.args:
            record.args = tuple(
                _redact(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
