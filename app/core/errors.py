import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.schemas.error import ErrorResponse

logger = logging.getLogger("web_server")


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0]
    field = ".".join(str(part) for part in first["loc"][1:])
    message = f"{field}: {first['msg']}" if field else first["msg"]
    body = ErrorResponse(error=message, code="VALIDATION_ERROR")
    return JSONResponse(body.model_dump(), status_code=400)


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled error: %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
        extra={"user_id": getattr(request.state, "user_id", None), "error_code": "INTERNAL_ERROR"},
    )
    body = ErrorResponse(error="서버 내부 오류가 발생했습니다", code="INTERNAL_ERROR")
    return JSONResponse(body.model_dump(), status_code=500)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
