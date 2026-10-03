import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from functools import partial

from aio_pika.abc import AbstractChannel
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session, get_session
from app.routers.deps import get_mq_channel, get_redis
from app.schemas.error import ErrorResponse
from app.schemas.post import PostCreateRequest, PostCreateResponse
from app.services import message_queue
from app.services.fanout_service import FanoutService
from app.services.post_repository import PostRepository
from app.services.post_service import PostNotFoundError, PostService, PostStorageError
from app.services.social_graph_repository import SocialGraphRepository
from app.services.social_graph_service import SocialGraphService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/me/feed")

FANOUT_SHUTDOWN_TIMEOUT_SECONDS = 10

_fanout_tasks: set[asyncio.Task] = set()


async def _run_fanout(redis: Redis, channel: AbstractChannel, event: dict) -> None:
    async with async_session() as session:
        repo = SocialGraphRepository(session)
        await FanoutService(
            SocialGraphService(repo, redis),
            repo,
            partial(message_queue.publish_message, channel),
            partial(message_queue.publish_dead_letter, channel),
        ).handle(event)


def _fanout_done(task: asyncio.Task) -> None:
    _fanout_tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.error("fanout task failed", exc_info=task.exception())


async def wait_fanout_tasks() -> None:
    if not _fanout_tasks:
        return
    _, pending = await asyncio.wait(set(_fanout_tasks), timeout=FANOUT_SHUTDOWN_TIMEOUT_SECONDS)
    if pending:
        logger.warning("fanout tasks cancelled on shutdown: %d", len(pending))
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


def get_publish_fanout(
    redis: Redis = Depends(get_redis),
    channel: AbstractChannel = Depends(get_mq_channel),
) -> Callable[[dict], Awaitable[None]]:
    async def publish(event: dict) -> None:
        task = asyncio.create_task(_run_fanout(redis, channel, event))
        _fanout_tasks.add(task)
        task.add_done_callback(_fanout_done)

    return publish


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
