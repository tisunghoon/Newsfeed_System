import json
import logging
from collections.abc import Awaitable, Callable

from aio_pika.abc import AbstractChannel, AbstractIncomingMessage
from redis.asyncio import Redis

from app.services.message_queue import FANOUT_QUEUE

logger = logging.getLogger(__name__)

NEWSFEED_MAX_SIZE = 500
NEWSFEED_TTL_SECONDS = 172_800
CACHE_ATTEMPTS = 3


def newsfeed_key(user_id: str) -> str:
    return f"newsfeed:{user_id}"


class FanoutWorker:
    def __init__(
        self,
        redis: Redis,
        send_notifications: Callable[[str, list[str]], Awaitable[None]],
    ):
        self.redis = redis
        self.send_notifications = send_notifications

    async def handle(self, message: dict) -> None:
        for attempt in range(1, CACHE_ATTEMPTS + 1):
            try:
                await self._update_caches(message)
                break
            except Exception:
                logger.exception("newsfeed cache update failed (%d/%d)", attempt, CACHE_ATTEMPTS)
                if attempt == CACHE_ATTEMPTS:
                    raise
        if message["action"] == "insert":
            await self._notify(message["post_id"], message["friend_ids"])

    async def _update_caches(self, message: dict) -> None:
        post_id = message["post_id"]
        for friend_id in message["friend_ids"]:
            key = newsfeed_key(friend_id)
            if message["action"] == "insert":
                await self.redis.zadd(key, {post_id: message["created_at"]})
                if await self.redis.zcard(key) > NEWSFEED_MAX_SIZE:
                    await self.redis.zremrangebyrank(key, 0, 0)
                await self.redis.expire(key, NEWSFEED_TTL_SECONDS)
            else:
                await self.redis.zrem(key, post_id)

    async def _notify(self, post_id: str, friend_ids: list[str]) -> None:
        try:
            await self.send_notifications(post_id, friend_ids)
        except Exception:
            logger.exception("notification trigger failed: %s", post_id)

    async def process(self, incoming: AbstractIncomingMessage) -> None:
        try:
            await self.handle(json.loads(incoming.body))
        except Exception:
            logger.exception("fanout message returned to queue")
            await incoming.nack(requeue=True)
            return
        await incoming.ack()


async def run_worker(channel: AbstractChannel, worker: FanoutWorker) -> None:
    queue = await channel.get_queue(FANOUT_QUEUE)
    async with queue.iterator() as messages:
        async for incoming in messages:
            await worker.process(incoming)
