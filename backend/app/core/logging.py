"""Structured JSON logging with redaction (BE-1.5).

Secrets and sensitive values must never reach logs (Arch §10, WS cross-cutting rules).
Redaction is applied by the formatter to the message, the args and every `extra` field:

* keys that name credentials (authorization, password, secret, token, *key, cookie …)
* bearer tokens and JWT-shaped strings anywhere in a string value
* any value wrapped in `Sensitive(...)`
"""

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.correlation import get_correlation_id

REDACTED = "[REDACTED]"

_SENSITIVE_KEY = re.compile(
    r"(authorization|passw(or)?d|secret|token|credential|cookie|session[_-]?id"
    # "key" as a word anywhere in the name (key, signing_key, private_key_pem,
    # aws_access_key_id) plus common unseparated forms (apikey, privatekey).
    r"|(^|[_.-])key([_.-]|$)|(api|private|access|signing|encryption|secret)key)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")

# Attributes every LogRecord has; anything else came from `extra=`.
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class Sensitive:
    """Wrap a value to force redaction wherever it is logged."""

    __slots__ = ("_value",)

    def __init__(self, value: object) -> None:
        self._value = value

    def __repr__(self) -> str:
        return REDACTED

    __str__ = __repr__

    def reveal(self) -> object:
        return self._value


def redact(value: Any, key: str | None = None) -> Any:
    if isinstance(value, Sensitive):
        return REDACTED
    if key is not None and _SENSITIVE_KEY.search(key):
        return REDACTED
    if isinstance(value, str):
        return _JWT.sub(REDACTED, _BEARER.sub(f"Bearer {REDACTED}", value))
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [redact(v) for v in value]
    if isinstance(value, int | float | bool) or value is None:
        return value
    return redact(str(value))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if isinstance(record.args, dict):
            record.args = redact(record.args)
        elif isinstance(record.args, tuple):
            record.args = tuple(redact(a) for a in record.args)
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
            "correlation_id": get_correlation_id(),
        }
        extras = {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS}
        if extras:
            payload["extra"] = redact(extras)
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
