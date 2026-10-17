"""Typed error responses and exception handlers for the HTTP API.

Every error is a single JSON object::

    {"error": {"code": "not_found", "message": "...", "details": {}}}

``code`` is a stable machine-readable slug; ``message`` is human-readable;
``details`` carries optional structured information (e.g. the offending field
name). Validation failures from FastAPI/pydantic are normalized to the same
shape so clients only ever parse one error format.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ExceptionHandler

from github_radar.ai.errors import (
    AIConfigurationError,
    AIError,
    AINetworkError,
    AIProviderError,
    AIRateLimitError,
    AIRequestLimitError,
    AIResponseValidationError,
)


class APIError(Exception):
    """A typed API error mapped onto an HTTP status.

    Attributes:
        status_code: the HTTP status to return.
        code: a stable machine-readable error slug.
        message: a human-readable, caller-safe message.
        details: optional structured context (must be JSON-serializable).
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


class NotFoundError(APIError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "not_found",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status.HTTP_404_NOT_FOUND, code, message, details=details)


class BadRequestError(APIError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "bad_request",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status.HTTP_400_BAD_REQUEST, code, message, details=details)


class ConflictError(APIError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "conflict",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status.HTTP_409_CONFLICT, code, message, details=details)


class UnavailableError(APIError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "unavailable",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status.HTTP_503_SERVICE_UNAVAILABLE, code, message, details=details)


def error_payload(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def _api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(exc.code, exc.message, details=exc.details),
    )


def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail: str
    details: dict[str, Any] | None = None
    if isinstance(exc.detail, dict):
        detail = str(exc.detail.get("message", "Request failed."))
        details = {k: v for k, v in exc.detail.items() if k != "message"}
    else:
        detail = str(exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload("http_error", detail, details=details),
    )


def _validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = exc.errors()
    messages = []
    for err in errors[:8]:
        location = ".".join(str(part) for part in err.get("loc", ()) if part != "body")
        messages.append(f"{location}: {err.get('msg', 'invalid')}")
    message = messages[0] if messages else "Request validation failed."
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=error_payload(
            "validation_error",
            message,
            details={"errors": messages},
        ),
    )


def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # ServerErrorMiddleware is outside user middleware, including the private boundary.
    private = request.url.path.startswith("/api/v1/workspace")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        headers={"Cache-Control": "no-store"} if private else None,
        content=error_payload(
            "internal_error",
            "An internal error occurred; the request was not completed.",
        ),
    )


def _ai_error_handler(request: Request, exc: AIError) -> JSONResponse:
    """Map AI-layer failures onto caller-safe HTTP statuses."""
    message = str(exc)
    if isinstance(exc, AIConfigurationError):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=error_payload("ai_configuration_error", message),
        )
    if isinstance(exc, AIRateLimitError):
        headers = {}
        if exc.retry_after_seconds is not None:
            headers["Retry-After"] = str(exc.retry_after_seconds)
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=error_payload(
                "ai_rate_limit",
                "The LLM provider is rate-limiting requests; retry later.",
            ),
            headers=headers,
        )
    if isinstance(exc, AIRequestLimitError):
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=error_payload(
                "ai_request_limit",
                message,
                details={"max_requests_per_run_used": True},
            ),
        )
    if isinstance(exc, (AINetworkError, AIResponseValidationError)):
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=error_payload(
                "ai_provider_unavailable",
                "The LLM provider did not produce a usable summary.",
            ),
        )
    if isinstance(exc, AIProviderError):
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=error_payload(
                "ai_provider_error",
                "The LLM provider returned an error response.",
                details={"provider_status_code": exc.status_code},
            ),
        )
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content=error_payload("ai_error", "The AI synthesis step failed."),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach uniform error handling to ``app``."""
    handlers: tuple[tuple[type[Exception], Any], ...] = (
        (APIError, _api_error_handler),
        (AIError, _ai_error_handler),
        (StarletteHTTPException, _http_exception_handler),
        (RequestValidationError, _validation_error_handler),
        (Exception, _unhandled_error_handler),
    )
    for exc_class, handler in handlers:
        app.add_exception_handler(exc_class, cast(ExceptionHandler, handler))


__all__ = [
    "APIError",
    "BadRequestError",
    "ConflictError",
    "NotFoundError",
    "UnavailableError",
    "error_payload",
    "register_exception_handlers",
]
