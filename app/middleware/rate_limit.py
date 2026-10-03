import logging
import math
import time
import uuid

from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware

from app.schemas.error import ErrorResponse

logger = logging.getLogger(__name__)

WINDOW_MS = 60_000
LIMIT = 100
ENDPOINT = "POST:/v1/me/feed"


def _now_ms() -> int:
    return int(time.time() * 1000)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, redis: Redis, now_ms=_now_ms):
        super().__init__(app)
        self.redis = redis
        self.now_ms = now_ms

    async def dispatch(self, request, call_next):
        if f"{request.method}:{request.url.path}" != ENDPOINT:
            return await call_next(request)

        key = f"rate_limit:{request.state.user_id}:{ENDPOINT}"
        now = self.now_ms()
        try:
            await self.redis.zremrangebyscore(key, 0, now - WINDOW_MS)
            if await self.redis.zcard(key) >= LIMIT:
                _, oldest = (await self.redis.zrange(key, 0, 0, withscores=True))[0]
                retry_after = max(1, math.ceil((oldest + WINDOW_MS - now) / 1000))
                return JSONResponse(
                    {"retry_after": retry_after, "code": "RATE_LIMIT_EXCEEDED"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
            await self.redis.zadd(key, {str(uuid.uuid4()): now})
            await self.redis.expire(key, 60)
        except RedisError:
            logger.exception("rate limit redis error")
            body = ErrorResponse(error="서비스를 일시적으로 사용할 수 없습니다", code="SERVICE_UNAVAILABLE")
            return JSONResponse(body.model_dump(), status_code=503)

        return await call_next(request)
