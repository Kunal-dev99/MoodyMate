"""Standard error envelope (BE-1.3, D-07).

Every error response has the shape
    {"code", "message", "details", "correlation_id", "retryable"}
and never includes stack traces or raw request input.
"""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.correlation import get_correlation_id


class ErrorEnvelope(BaseModel):
    code: str = Field(description="Stable machine-readable error code, snake_case.")
    message: str = Field(description="Human-readable summary. Never contains secrets.")
    details: list[dict[str, Any]] | dict[str, Any] | None = None
    correlation_id: str | None = None
    retryable: bool = False


class ApiError(Exception):
    """Raise from services/routers for an expected, enveloped error."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: list[dict[str, Any]] | dict[str, Any] | None = None,
        retryable: bool = False,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.retryable = retryable
        self.headers = headers


# Retrying these can succeed without the client changing the request.
_RETRYABLE = {429, 502, 503, 504}


def _envelope(
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | dict[str, Any] | None = None,
    retryable: bool | None = None,
    correlation_id: str | None = None,
) -> JSONResponse:
    body = ErrorEnvelope(
        code=code,
        message=message,
        details=details,
        correlation_id=correlation_id or get_correlation_id(),
        retryable=status_code in _RETRYABLE if retryable is None else retryable,
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


def _code_for(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase.lower().replace(" ", "_").replace("-", "_")
    except ValueError:
        return "http_error"


def internal_error_response(correlation_id: str) -> JSONResponse:
    return _envelope(
        500,
        "internal_error",
        "An unexpected error occurred. Quote the correlation ID when reporting it.",
        correlation_id=correlation_id,
        retryable=False,
    )


async def _api_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    response = _envelope(exc.status_code, exc.code, exc.message, exc.details, exc.retryable)
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def _http_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    message = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
    response = _envelope(exc.status_code, _code_for(exc.status_code), message)
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def _validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Keep location, message and type only. Pydantic's `input`/`ctx` can echo
    # sensitive request values back to the caller and into logs.
    details = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg"), "type": err.get("type")}
        for err in exc.errors()
    ]
    return _envelope(422, "validation_error", "Request validation failed.", details)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    # Unhandled exceptions are enveloped by CorrelationIdMiddleware so that the
    # correlation header survives; see app.core.correlation.


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    "4XX": {"model": ErrorEnvelope, "description": "Client error (standard envelope)"},
    "5XX": {"model": ErrorEnvelope, "description": "Server error (standard envelope)"},
}
