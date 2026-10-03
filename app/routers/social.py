import uuid

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.routers.deps import get_redis
from app.schemas.error import ErrorResponse
from app.services.social_graph_repository import SocialGraphRepository
from app.services.social_graph_service import (
    AlreadyFriendsError,
    FriendLimitExceededError,
    FriendshipNotFoundError,
    SocialGraphService,
    SocialGraphStorageError,
    UserNotFoundError,
)

router = APIRouter(prefix="/v1/me/friends")


def get_social_graph_service(
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> SocialGraphService:
    return SocialGraphService(SocialGraphRepository(session), redis)


def _error(status: int, error: str, code: str) -> JSONResponse:
    return JSONResponse(ErrorResponse(error=error, code=code).model_dump(), status_code=status)


@router.post("/{target_user_id}", status_code=201)
async def add_friend(
    target_user_id: uuid.UUID,
    request: Request,
    service: SocialGraphService = Depends(get_social_graph_service),
):
    try:
        await service.add_friend(request.state.user_id, str(target_user_id))
    except UserNotFoundError:
        return _error(404, "사용자를 찾을 수 없습니다", "NOT_FOUND")
    except FriendLimitExceededError:
        return _error(409, "친구 수 한도(5,000명)를 초과했습니다", "FRIEND_LIMIT_EXCEEDED")
    except AlreadyFriendsError:
        return _error(409, "이미 친구 관계입니다", "ALREADY_FRIENDS")
    except SocialGraphStorageError:
        return _error(500, "서버 내부 오류가 발생했습니다", "INTERNAL_ERROR")
    return Response(status_code=201)


@router.delete("/{target_user_id}", status_code=204)
async def remove_friend(
    target_user_id: uuid.UUID,
    request: Request,
    service: SocialGraphService = Depends(get_social_graph_service),
):
    try:
        await service.remove_friend(request.state.user_id, str(target_user_id))
    except FriendshipNotFoundError:
        return _error(404, "친구 관계를 찾을 수 없습니다", "NOT_FOUND")
    except SocialGraphStorageError:
        return _error(500, "서버 내부 오류가 발생했습니다", "INTERNAL_ERROR")
    return Response(status_code=204)
