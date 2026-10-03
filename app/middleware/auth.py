import jwt
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.schemas.error import ErrorResponse


def _unauthorized(error: str, code: str) -> JSONResponse:
    return JSONResponse(ErrorResponse(error=error, code=code).model_dump(), status_code=401)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        header = request.headers.get("Authorization")
        if header is None:
            return _unauthorized("인증 토큰이 없습니다", "MISSING_TOKEN")

        scheme, _, token = header.partition(" ")
        if scheme != "Bearer" or not token:
            return _unauthorized("유효하지 않은 토큰입니다", "INVALID_TOKEN")

        try:
            payload = jwt.decode(
                token,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
                options={"require": ["exp", "sub"]},
            )
        except jwt.ExpiredSignatureError:
            return _unauthorized("토큰이 만료되었습니다", "EXPIRED_TOKEN")
        except jwt.InvalidTokenError:
            return _unauthorized("유효하지 않은 토큰입니다", "INVALID_TOKEN")

        request.state.user_id = payload["sub"]
        return await call_next(request)
