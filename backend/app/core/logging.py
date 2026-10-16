"""Structured JSON logging with redaction (BE-1.5).

Secrets and sensitive values must never reach logs (Arch §10, WS cross-cutting rules).
Redaction is applied by the formatter to the message, the args and every `extra` field:

* keys that name credentials (authorization, password, secret, token, *key, cookie …)
* bearer tokens and JWT-shaped strings anywhere in a string value
* any value wrapped in `Sensitive(...)`
* a `Classified(value, code)` whose code is not in `LOG_CLEAR` (BE-3.5)
"""

import json
import logging
import re
import sys
import traceback
from datetime import UTC, datetime
from typing import Any

from app.core.classification import LOG_CLEAR, Classified
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
    if isinstance(value, Classified):
        # Same classification codes as API masking (BE-3.5); unknown codes are redacted.
        return redact(value.value, key) if value.code in LOG_CLEAR else REDACTED
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


def _is_database_error(exc: BaseException) -> bool:
    from sqlalchemy.exc import StatementError

    module = type(exc).__module__
    return isinstance(exc, StatementError) or module.startswith(("asyncpg", "sqlalchemy.dialects"))


def _exception_message(exc: BaseException) -> str:
    """Database errors carry bound values (and row values, e.g. 'Key (email)=(...)') in
    their message, so only the statement and the error identity are kept (BE-3.5)."""
    if not _is_database_error(exc):
        return str(exc)
    statement = getattr(exc, "statement", None)
    sqlstate = getattr(exc, "sqlstate", None)
    constraint = getattr(exc, "constraint_name", None)
    parts = [f"[SQL: {statement}]" if statement else "", f"sqlstate={sqlstate}" if sqlstate else ""]
    parts.append(f"constraint={constraint}" if constraint else "")
    return " ".join(p for p in parts + ["(driver message withheld)"] if p)


def format_exception(exc: BaseException) -> str:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and all(current is not seen for seen in chain):
        chain.append(current)
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    out = []
    for item in reversed(chain):
        frames = "".join(traceback.format_tb(item.__traceback__))
        name = f"{type(item).__module__}.{type(item).__qualname__}"
        message = _exception_message(item)
        out.append(f"Traceback (most recent call last):\n{frames}{name}: {message}")
    text = "\n\nDuring handling, another exception occurred:\n\n".join(out)
    if isinstance(exc, BaseExceptionGroup):
        # Keep each grouped exception's diagnostics (same withholding rules apply).
        for i, sub in enumerate(exc.exceptions, 1):
            text += f"\n\n[group member {i}]\n" + format_exception(sub)
    return text


class JsonFormatter(logging.Formatter):
    def formatException(self, ei: Any) -> str:  # noqa: N802 - logging API name
        exc = ei[1]
        return format_exception(exc) if exc is not None else super().formatException(ei)

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
    # python-multipart's warnings quote positions and byte values of the request body
    # (BE-8.6: no upload content in logs); its errors carry none.
    logging.getLogger("python_multipart").setLevel(logging.ERROR)
