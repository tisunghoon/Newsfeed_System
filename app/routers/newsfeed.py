from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.routers.deps import get_redis
from app.schemas.error import ErrorResponse
from app.schemas.post import FeedResponse
from app.services.newsfeed_repository import NewsfeedRepository
from app.services.newsfeed_service import InvalidCursorError, NewsfeedService, NewsfeedStorageError

router = APIRouter(prefix="/v1/me/feed")


def get_newsfeed_service(
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> NewsfeedService:
    return NewsfeedService(NewsfeedRepository(session), redis)


def _error(status: int, error: str, code: str) -> JSONResponse:
    return JSONResponse(ErrorResponse(error=error, code=code).model_dump(), status_code=status)


@router.get("", response_model=FeedResponse)
async def get_feed(
    request: Request,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=20),
    service: NewsfeedService = Depends(get_newsfeed_service),
):
    try:
        return await service.get_feed(request.state.user_id, cursor, limit)
    except InvalidCursorError:
        return _error(400, "유효하지 않은 cursor입니다", "VALIDATION_ERROR")
    except NewsfeedStorageError:
        return _error(500, "서버 내부 오류가 발생했습니다", "INTERNAL_ERROR")
