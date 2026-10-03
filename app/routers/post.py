import uuid
from collections.abc import Awaitable, Callable
from functools import partial

from aio_pika.abc import AbstractChannel
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.routers.deps import get_redis
from app.schemas.error import ErrorResponse
from app.schemas.post import PostCreateRequest, PostCreateResponse
from app.services import message_queue
from app.services.fanout_service import FanoutService
from app.services.post_repository import PostRepository
from app.services.post_service import PostNotFoundError, PostService, PostStorageError
from app.services.social_graph_repository import SocialGraphRepository
from app.services.social_graph_service import SocialGraphService

router = APIRouter(prefix="/v1/me/feed")


def get_mq_channel() -> AbstractChannel:
    raise NotImplementedError("RabbitMQ 채널은 앱 수명주기 연결 이슈에서 제공한다")


def get_publish_fanout(
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    channel: AbstractChannel = Depends(get_mq_channel),
) -> Callable[[dict], Awaitable[None]]:
    repo = SocialGraphRepository(session)
    return FanoutService(
        SocialGraphService(repo, redis),
        repo,
        partial(message_queue.publish_message, channel),
        partial(message_queue.publish_dead_letter, channel),
    ).handle


def get_post_service(
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    publish_fanout: Callable[[dict], Awaitable[None]] = Depends(get_publish_fanout),
) -> PostService:
    return PostService(PostRepository(session), redis, publish_fanout)


def _error(status: int, error: str, code: str) -> JSONResponse:
    return JSONResponse(ErrorResponse(error=error, code=code).model_dump(), status_code=status)


@router.post("", status_code=201, response_model=PostCreateResponse)
async def create_post(
    payload: PostCreateRequest,
    request: Request,
    service: PostService = Depends(get_post_service),
):
    try:
        return await service.create(request.state.user_id, payload)
    except PostStorageError:
        return _error(500, "서버 내부 오류가 발생했습니다", "INTERNAL_ERROR")


@router.delete("/{post_id}", status_code=204)
async def delete_post(
    post_id: uuid.UUID,
    request: Request,
    service: PostService = Depends(get_post_service),
):
    try:
        await service.delete(request.state.user_id, post_id)
    except PostNotFoundError:
        return _error(404, "게시물을 찾을 수 없습니다", "NOT_FOUND")
    except PostStorageError:
        return _error(500, "서버 내부 오류가 발생했습니다", "INTERNAL_ERROR")
    return Response(status_code=204)
