import uuid

import httpx
from fakeredis.aioredis import FakeRedis
from fastapi import FastAPI

from app.core.errors import register_error_handlers
from app.middleware.auth import AuthMiddleware
from app.routers import social as social_router
from app.services.social_graph_service import FRIEND_LIMIT, SocialGraphService
from tests.unit.test_auth_middleware import auth_header, client_for
from tests.unit.test_post_service import BrokenRedis

FRIENDS = "/v1/me/friends"
ME = "user-1"


class FakeRepo:
    def __init__(self, users=(), fail_write=False):
        self.users = set(users)
        self.pairs = set()
        self.fail_write = fail_write

    async def user_exists(self, user_id):
        return user_id in self.users

    async def count_friends(self, user_id):
        return sum(1 for a, _ in self.pairs if a == user_id)

    async def are_friends(self, user_id, friend_id):
        return (user_id, friend_id) in self.pairs

    async def list_friend_ids(self, user_id):
        return [b for a, b in self.pairs if a == user_id]

    async def add_friendship(self, user_id, friend_id):
        if self.fail_write:
            raise RuntimeError("db down")
        self.pairs |= {(user_id, friend_id), (friend_id, user_id)}

    async def remove_friendship(self, user_id, friend_id):
        if self.fail_write:
            raise RuntimeError("db down")
        self.pairs -= {(user_id, friend_id), (friend_id, user_id)}


class Harness:
    def __init__(self, repo=None, redis=None):
        self.repo = repo or FakeRepo()
        self.redis = redis if redis is not None else FakeRedis(decode_responses=True)
        self.service = SocialGraphService(self.repo, self.redis)

    def client(self) -> httpx.AsyncClient:
        app = FastAPI()
        register_error_handlers(app)
        app.add_middleware(AuthMiddleware)
        app.include_router(social_router.router)
        app.dependency_overrides[social_router.get_social_graph_service] = lambda: self.service
        return client_for(app)


def new_user(h: Harness) -> str:
    user_id = str(uuid.uuid4())
    h.repo.users.add(user_id)
    return user_id


async def add_friend(h: Harness, target, user_id=ME) -> httpx.Response:
    async with h.client() as client:
        return await client.post(f"{FRIENDS}/{target}", headers=auth_header(user_id))


async def remove_friend(h: Harness, target, user_id=ME) -> httpx.Response:
    async with h.client() as client:
        return await client.delete(f"{FRIENDS}/{target}", headers=auth_header(user_id))


async def test_add_stores_both_directions():
    h = Harness()
    target = new_user(h)
    res = await add_friend(h, target)
    assert res.status_code == 201
    assert h.repo.pairs == {(ME, target), (target, ME)}


async def test_add_unknown_user_returns_404():
    h = Harness()
    res = await add_friend(h, uuid.uuid4())
    assert res.status_code == 404
    assert res.json()["code"] == "NOT_FOUND"
    assert h.repo.pairs == set()


async def test_add_duplicate_returns_409():
    h = Harness()
    target = new_user(h)
    await add_friend(h, target)
    res = await add_friend(h, target)
    assert res.status_code == 409
    assert res.json()["code"] == "ALREADY_FRIENDS"
    assert len(h.repo.pairs) == 2


async def test_add_over_limit_returns_409():
    h = Harness()
    h.repo.pairs = {(ME, f"other-{i}") for i in range(FRIEND_LIMIT)}
    target = new_user(h)
    res = await add_friend(h, target)
    assert res.status_code == 409
    assert res.json()["code"] == "FRIEND_LIMIT_EXCEEDED"
    assert await h.repo.count_friends(ME) == FRIEND_LIMIT


async def test_limit_ignores_stale_count_cache():
    h = Harness()
    h.repo.pairs = {(ME, f"other-{i}") for i in range(FRIEND_LIMIT)}
    await h.redis.set(f"friends:count:{ME}", 10)
    res = await add_friend(h, new_user(h))
    assert res.status_code == 409
    assert res.json()["code"] == "FRIEND_LIMIT_EXCEEDED"


async def test_add_at_one_below_limit_succeeds():
    h = Harness()
    h.repo.pairs = {(ME, f"other-{i}") for i in range(FRIEND_LIMIT - 1)}
    assert (await add_friend(h, new_user(h))).status_code == 201


async def test_add_db_failure_returns_500():
    h = Harness(repo=FakeRepo(fail_write=True))
    res = await add_friend(h, new_user(h))
    assert res.status_code == 500
    assert res.json()["code"] == "INTERNAL_ERROR"


async def test_add_invalidates_both_caches():
    h = Harness()
    target = new_user(h)
    for user_id in (ME, target):
        await h.redis.sadd(f"friends:{user_id}", "x")
        await h.redis.set(f"friends:count:{user_id}", 1)
    await add_friend(h, target)
    assert await h.redis.keys("*") == []


async def test_add_succeeds_when_cache_is_down():
    h = Harness(redis=BrokenRedis())
    res = await add_friend(h, new_user(h))
    assert res.status_code == 201


async def test_remove_deletes_both_directions():
    h = Harness()
    target = new_user(h)
    await add_friend(h, target)
    res = await remove_friend(h, target)
    assert res.status_code == 204
    assert h.repo.pairs == set()


async def test_remove_missing_relation_returns_404():
    h = Harness()
    res = await remove_friend(h, new_user(h))
    assert res.status_code == 404
    assert res.json()["code"] == "NOT_FOUND"


async def test_remove_invalidates_both_caches():
    h = Harness()
    target = new_user(h)
    await add_friend(h, target)
    for user_id in (ME, target):
        await h.redis.sadd(f"friends:{user_id}", "x")
        await h.redis.set(f"friends:count:{user_id}", 1)
    await remove_friend(h, target)
    assert await h.redis.keys("*") == []


async def test_remove_db_failure_returns_500():
    h = Harness(repo=FakeRepo(fail_write=True))
    target = new_user(h)
    h.repo.pairs = {(ME, target), (target, ME)}
    res = await remove_friend(h, target)
    assert res.status_code == 500
    assert len(h.repo.pairs) == 2


async def test_get_friend_ids_miss_reads_db_and_fills_cache():
    h = Harness()
    h.repo.pairs = {(ME, "a"), (ME, "b")}
    assert sorted(await h.service.get_friend_ids(ME)) == ["a", "b"]
    assert await h.redis.smembers(f"friends:{ME}") == {"a", "b"}
    assert await h.redis.get(f"friends:count:{ME}") == "2"
    assert 0 < await h.redis.ttl(f"friends:{ME}") <= 600
    assert 0 < await h.redis.ttl(f"friends:count:{ME}") <= 600


async def test_get_friend_ids_hit_skips_db():
    h = Harness()
    await h.redis.sadd(f"friends:{ME}", "cached")
    h.repo.pairs = {(ME, "db")}
    assert await h.service.get_friend_ids(ME) == ["cached"]


async def test_get_friend_ids_falls_back_to_db_when_cache_is_down():
    h = Harness(redis=BrokenRedis())
    h.repo.pairs = {(ME, "a")}
    assert await h.service.get_friend_ids(ME) == ["a"]


async def test_get_friend_ids_after_add_reflects_new_friend():
    h = Harness()
    target = new_user(h)
    assert await h.service.get_friend_ids(ME) == []
    await add_friend(h, target)
    assert await h.service.get_friend_ids(ME) == [target]
