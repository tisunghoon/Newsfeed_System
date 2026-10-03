import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.services.social_graph_repository import SocialGraphRepository

logger = logging.getLogger(__name__)

FRIEND_LIMIT = 5000
FRIENDS_CACHE_TTL = 600


class UserNotFoundError(Exception):
    pass


class FriendLimitExceededError(Exception):
    pass


class AlreadyFriendsError(Exception):
    pass


class FriendshipNotFoundError(Exception):
    pass


class SocialGraphStorageError(Exception):
    pass


def _friends_key(user_id: str) -> str:
    return f"friends:{user_id}"


def _count_key(user_id: str) -> str:
    return f"friends:count:{user_id}"


class SocialGraphService:
    def __init__(self, repo: SocialGraphRepository, redis: Redis):
        self.repo = repo
        self.redis = redis

    async def add_friend(self, user_id: str, target_id: str) -> None:
        try:
            if not await self.repo.user_exists(target_id):
                raise UserNotFoundError
            if await self.repo.count_friends(user_id) >= FRIEND_LIMIT:
                raise FriendLimitExceededError
            if await self.repo.are_friends(user_id, target_id):
                raise AlreadyFriendsError
            await self.repo.add_friendship(user_id, target_id)
        except (UserNotFoundError, FriendLimitExceededError, AlreadyFriendsError):
            raise
        except Exception:
            logger.exception("friend add failed: %s -> %s", user_id, target_id)
            raise SocialGraphStorageError from None
        await self._invalidate(user_id, target_id)

    async def remove_friend(self, user_id: str, target_id: str) -> None:
        try:
            if not await self.repo.are_friends(user_id, target_id):
                raise FriendshipNotFoundError
            await self.repo.remove_friendship(user_id, target_id)
        except FriendshipNotFoundError:
            raise
        except Exception:
            logger.exception("friend remove failed: %s -> %s", user_id, target_id)
            raise SocialGraphStorageError from None
        await self._invalidate(user_id, target_id)

    async def get_friend_ids(self, user_id: str) -> list[str]:
        try:
            cached = await self.redis.smembers(_friends_key(user_id))
        except RedisError:
            logger.exception("friends cache read failed: %s", user_id)
            cached = None
        if cached:
            return list(cached)

        try:
            friend_ids = await self.repo.list_friend_ids(user_id)
        except Exception:
            logger.exception("friend list lookup failed: %s", user_id)
            raise SocialGraphStorageError from None
        await self._fill_cache(user_id, friend_ids)
        return friend_ids

    async def _fill_cache(self, user_id: str, friend_ids: list[str]) -> None:
        try:
            async with self.redis.pipeline() as pipe:
                if friend_ids:
                    pipe.sadd(_friends_key(user_id), *friend_ids)
                    pipe.expire(_friends_key(user_id), FRIENDS_CACHE_TTL)
                pipe.set(_count_key(user_id), len(friend_ids), ex=FRIENDS_CACHE_TTL)
                await pipe.execute()
        except RedisError:
            logger.exception("friends cache fill failed: %s", user_id)

    async def _invalidate(self, *user_ids: str) -> None:
        keys = [key for user_id in user_ids for key in (_friends_key(user_id), _count_key(user_id))]
        try:
            await self.redis.delete(*keys)
        except RedisError:
            logger.exception("friends cache invalidate failed: %s", user_ids)
