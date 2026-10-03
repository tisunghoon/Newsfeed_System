from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.schemas.error import ErrorResponse


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0]
    field = ".".join(str(part) for part in first["loc"][1:])
    message = f"{field}: {first['msg']}" if field else first["msg"]
    body = ErrorResponse(error=message, code="VALIDATION_ERROR")
    return JSONResponse(body.model_dump(), status_code=400)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, validation_error_handler)
