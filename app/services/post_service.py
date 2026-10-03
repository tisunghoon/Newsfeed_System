import json
import logging
import uuid
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.schemas.post import PostCreateRequest, PostCreateResponse
from app.services.post_repository import PostRepository

logger = logging.getLogger(__name__)

POST_CACHE_TTL = 86_400


class PostNotFoundError(Exception):
    pass


class PostStorageError(Exception):
    pass


def _cache_key(post_id: uuid.UUID) -> str:
    return f"post:{post_id}"


class PostService:
    def __init__(
        self,
        repo: PostRepository,
        redis: Redis,
        publish_fanout: Callable[[dict], Awaitable[None]],
    ):
        self.repo = repo
        self.redis = redis
        self.publish_fanout = publish_fanout

    async def create(self, author_id: str, request: PostCreateRequest) -> PostCreateResponse:
        try:
            post_id, created_at = await self.repo.create(author_id, request.body, request.media)
        except Exception:
            logger.exception("post insert failed")
            raise PostStorageError from None

        fields = {
            "author_id": author_id,
            "body": request.body or "",
            "media_json": json.dumps([item.model_dump() for item in request.media]),
            "created_at": str(int(created_at.timestamp() * 1000)),
        }
        try:
            async with self.redis.pipeline() as pipe:
                pipe.hset(_cache_key(post_id), mapping=fields)
                pipe.expire(_cache_key(post_id), POST_CACHE_TTL)
                await pipe.execute()
        except RedisError:
            logger.exception("post cache write failed: %s", post_id)

        await self._publish({"action": "insert", "post_id": str(post_id), "author_id": author_id})
        return PostCreateResponse(post_id=post_id, created_at=created_at)

    async def _publish(self, event: dict) -> None:
        try:
            await self.publish_fanout(event)
        except Exception:
            logger.exception("fanout publish failed: %s", event)

    async def delete(self, author_id: str, post_id: uuid.UUID) -> None:
        try:
            if not await self.repo.exists_active(post_id, author_id):
                raise PostNotFoundError
        except PostNotFoundError:
            raise
        except Exception:
            logger.exception("post lookup failed: %s", post_id)
            raise PostStorageError from None

        key = _cache_key(post_id)
        try:
            snapshot = await self.redis.hgetall(key)
            ttl = await self.redis.ttl(key)
            await self.redis.delete(key)
        except RedisError:
            logger.exception("post cache delete failed: %s", post_id)
            raise PostStorageError from None

        try:
            await self.repo.soft_delete(post_id)
        except Exception:
            logger.exception("post soft delete failed: %s", post_id)
            await self._restore_cache(key, snapshot, ttl)
            raise PostStorageError from None

        await self._publish({"action": "delete", "post_id": str(post_id), "author_id": author_id})

    async def _restore_cache(self, key: str, snapshot: dict, ttl: int) -> None:
        if not snapshot:
            return
        try:
            async with self.redis.pipeline() as pipe:
                pipe.hset(key, mapping=snapshot)
                pipe.expire(key, ttl if ttl > 0 else POST_CACHE_TTL)
                await pipe.execute()
        except RedisError:
            logger.exception("post cache restore failed: %s", key)
