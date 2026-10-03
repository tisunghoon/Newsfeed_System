import base64
import json
import logging
import uuid
from datetime import datetime, timezone

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.schemas.post import FeedResponse, MediaItem, PostResponse
from app.services.newsfeed_repository import NewsfeedRepository
from app.services.post_service import POST_CACHE_TTL
from app.workers.fanout_worker import newsfeed_key

logger = logging.getLogger(__name__)


class InvalidCursorError(Exception):
    pass


class NewsfeedStorageError(Exception):
    pass


def _post_key(post_id: str) -> str:
    return f"post:{post_id}"


def _ms(post: PostResponse) -> int:
    return int(post.created_at.timestamp() * 1000)


def _sort_key(post: PostResponse) -> tuple[int, str]:
    return _ms(post), str(post.post_id)


def encode_cursor(post_id: str, created_at_ms: int) -> str:
    raw = json.dumps({"post_id": post_id, "created_at": created_at_ms})
    return base64.b64encode(raw.encode(), altchars=b"-_").decode()


def decode_cursor(cursor: str) -> tuple[int, str]:
    try:
        data = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        created_at, post_id = data["created_at"], data["post_id"]
        if type(created_at) is not int or created_at < 0 or not isinstance(post_id, str):
            raise ValueError
        return created_at, str(uuid.UUID(post_id))
    except (ValueError, KeyError, TypeError):
        raise InvalidCursorError from None


def _from_cache(post_id: str, fields: dict) -> PostResponse:
    return PostResponse(
        post_id=post_id,
        author_id=fields["author_id"],
        body=fields["body"] or None,
        media=[MediaItem(**item) for item in json.loads(fields["media_json"])],
        created_at=datetime.fromtimestamp(int(fields["created_at"]) / 1000, tz=timezone.utc),
    )


class NewsfeedService:
    def __init__(self, repo: NewsfeedRepository, redis: Redis):
        self.repo = repo
        self.redis = redis

    async def get_feed(self, user_id: str, cursor: str | None, limit: int) -> FeedResponse:
        position = decode_cursor(cursor) if cursor is not None else None
        entries = await self._read_cached_ids(user_id, position, limit + 1)
        if entries is None:
            fetched = await self._db(self.repo.list_friend_posts(user_id, position, limit + 1))
            has_more = len(fetched) > limit
            posts = fetched[:limit]
            last = (str(posts[-1].post_id), _ms(posts[-1])) if posts else None
        else:
            has_more = len(entries) > limit
            page = entries[:limit]
            posts = await self._load_details([post_id for post_id, _ in page])
            last = page[-1] if page else None
        posts.sort(key=_sort_key, reverse=True)
        next_cursor = encode_cursor(*last) if has_more and last else None
        return FeedResponse(posts=posts, next_cursor=next_cursor, has_more=has_more)

    async def _db(self, call):
        try:
            return await call
        except Exception:
            logger.exception("newsfeed db read failed")
            raise NewsfeedStorageError from None

    async def _read_cached_ids(
        self, user_id: str, position: tuple[int, str] | None, count: int
    ) -> list[tuple[str, int]] | None:
        key = newsfeed_key(user_id)
        try:
            if position is None:
                found = await self.redis.zrevrangebyscore(
                    key, "+inf", "-inf", start=0, num=count, withscores=True
                )
            else:
                ms, post_id = position
                tied = await self.redis.zrevrangebyscore(key, ms, ms, withscores=True)
                found = [(member, score) for member, score in tied if member < post_id]
                if len(found) < count:
                    found += await self.redis.zrevrangebyscore(
                        key, f"({ms}", "-inf", start=0, num=count - len(found), withscores=True
                    )
            if not found and not await self.redis.exists(key):
                return None
        except RedisError:
            logger.exception("newsfeed cache read failed: %s", user_id)
            return None
        return [(member, int(score)) for member, score in found[:count]]

    async def _load_details(self, post_ids: list[str]) -> list[PostResponse]:
        try:
            async with self.redis.pipeline() as pipe:
                for post_id in post_ids:
                    pipe.hgetall(_post_key(post_id))
                cached = await pipe.execute()
        except RedisError:
            logger.exception("post cache read failed")
            cached = [{} for _ in post_ids]

        found = {
            post_id: _from_cache(post_id, fields)
            for post_id, fields in zip(post_ids, cached)
            if fields
        }
        missing = [post_id for post_id in post_ids if post_id not in found]
        if missing:
            loaded = await self._db(self.repo.get_posts(missing))
            await self._backfill(loaded)
            found.update({str(post.post_id): post for post in loaded})
        return [found[post_id] for post_id in post_ids if post_id in found]

    async def _backfill(self, posts: list[PostResponse]) -> None:
        try:
            async with self.redis.pipeline() as pipe:
                for post in posts:
                    key = _post_key(str(post.post_id))
                    pipe.hset(
                        key,
                        mapping={
                            "author_id": str(post.author_id),
                            "body": post.body or "",
                            "media_json": json.dumps([item.model_dump() for item in post.media]),
                            "created_at": str(_ms(post)),
                        },
                    )
                    pipe.expire(key, POST_CACHE_TTL)
                await pipe.execute()
        except RedisError:
            logger.exception("post cache back-fill failed")
