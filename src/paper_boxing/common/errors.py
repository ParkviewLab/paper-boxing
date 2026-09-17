# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Server-side error convention (docs/api.md): `ApiError` and the FastAPI
handlers that turn every failure into the contract's error body.

Used by the backend and by the fake backend, so both answer identically. The
client side is `paper_boxing.common.client.BackendError`.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from paper_boxing.common.schema import ErrorBody, ErrorCode, ErrorDetail


class ApiError(Exception):
    """An error answered with `status` and the body `{"error": {"code", "message"}}`."""

    def __init__(self, status: int, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def error_response(status: int, code: ErrorCode, message: str) -> JSONResponse:
    body = ErrorBody(error=ErrorDetail(code=code, message=message))
    return JSONResponse(body.model_dump(mode="json"), status_code=status)


_STATUS_CODES: dict[int, ErrorCode] = {
    400: ErrorCode.BAD_REQUEST,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
    422: ErrorCode.VALIDATION_ERROR,
    507: ErrorCode.INSUFFICIENT_STORAGE,
}


def install_error_handlers(app: FastAPI) -> None:
    """Register the handlers that keep every error body in the contract's shape."""

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(
            exc.status_code, ErrorCode.BAD_REQUEST if exc.status_code < 500 else ErrorCode.INTERNAL_ERROR
        )
        message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        response = error_response(exc.status_code, code, message)
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = "; ".join(
            f"{'.'.join(str(loc) for loc in err.get('loc', ()))}: {err.get('msg', '')}"
            for err in exc.errors()
        )
        return error_response(422, ErrorCode.VALIDATION_ERROR, problems or "invalid request")
