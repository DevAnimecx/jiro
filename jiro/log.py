"""Structured JSON logging.

No telemetry: Jiro never sends logs anywhere. Logs go to stderr (and
optionally a file), one JSON object per line, with a request_id when inside
a request context.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any, Dict, Optional

_RESERVED = {"name", "msg", "args", "levelname", "levelno", "pathname", "filename",
             "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
             "created", "msecs", "relativeCreated", "thread", "threadName",
             "processName", "process", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
            "logger": record.name,
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "info", file: str = "") -> None:
    handlers = [logging.StreamHandler(sys.stderr)]
    if file:
        handlers.append(logging.FileHandler(file))
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        handlers=handlers)
    for handler in handlers:
        handler.setFormatter(JsonFormatter())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_extra(event: str, **fields: Any) -> Dict[str, Any]:
    return {"event": event, **fields}
