import uuid
from datetime import datetime, timezone

import httpx
from fakeredis.aioredis import FakeRedis
from fastapi import FastAPI
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.errors import register_error_handlers
from app.middleware.auth import AuthMiddleware
from app.routers import post as post_router
from app.services.post_service import PostService
from tests.unit.test_auth_middleware import auth_header, client_for

FEED = "/v1/me/feed"
VALID = {"body": "hello", "media": [{"type": "image", "url": "https://cdn.example.com/a.jpg"}]}


class FakeRepo:
    def __init__(self, fail_create=False, fail_delete=False):
        self.posts = {}
        self.fail_create = fail_create
        self.fail_delete = fail_delete

    async def create(self, author_id, body, media):
        if self.fail_create:
            raise RuntimeError("db down")
        post_id = uuid.uuid4()
        self.posts[post_id] = {"author_id": author_id, "deleted": False}
        return post_id, datetime.now(timezone.utc)

    async def exists_active(self, post_id, author_id):
        post = self.posts.get(post_id)
        return post is not None and post["author_id"] == author_id and not post["deleted"]

    async def soft_delete(self, post_id):
        if self.fail_delete:
            raise RuntimeError("db down")
        self.posts[post_id]["deleted"] = True


class BrokenRedis:
    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise RedisConnectionError("redis down")

        return fail


class Harness:
    def __init__(self, repo=None, redis=None):
        self.repo = repo or FakeRepo()
        self.redis = redis if redis is not None else FakeRedis(decode_responses=True)
        self.events = []
        self.service = PostService(self.repo, self.redis, self.publish)

    async def publish(self, event):
        self.events.append(event)

    def client(self) -> httpx.AsyncClient:
        app = FastAPI()
        register_error_handlers(app)
        app.add_middleware(AuthMiddleware)
        app.include_router(post_router.router)
        app.dependency_overrides[post_router.get_post_service] = lambda: self.service
        return client_for(app)


async def create_post(h: Harness, payload=VALID, user_id="user-1") -> httpx.Response:
    async with h.client() as client:
        return await client.post(FEED, json=payload, headers=auth_header(user_id))


async def delete_post(h: Harness, post_id, user_id="user-1") -> httpx.Response:
    async with h.client() as client:
        return await client.delete(f"{FEED}/{post_id}", headers=auth_header(user_id))


async def test_create_stores_post_and_cache():
    h = Harness()
    res = await create_post(h)
    assert res.status_code == 201
    post_id = res.json()["post_id"]
    assert uuid.UUID(post_id) in h.repo.posts
    cached = await h.redis.hgetall(f"post:{post_id}")
    assert cached["author_id"] == "user-1"
    assert cached["body"] == "hello"
    assert 0 < await h.redis.ttl(f"post:{post_id}") <= 86_400
    assert h.events == [
        {
            "action": "insert",
            "post_id": post_id,
            "author_id": "user-1",
            "created_at": int(cached["created_at"]),
        }
    ]


async def test_create_db_failure_skips_cache_and_returns_500():
    h = Harness(repo=FakeRepo(fail_create=True))
    res = await create_post(h)
    assert res.status_code == 500
    assert res.json()["code"] == "INTERNAL_ERROR"
    assert await h.redis.keys("*") == []
    assert h.events == []


async def test_create_cache_failure_still_returns_201():
    h = Harness(redis=BrokenRedis())
    res = await create_post(h)
    assert res.status_code == 201
    assert len(h.repo.posts) == 1
    assert len(h.events) == 1


async def test_create_rejects_invalid_input_with_400():
    invalid = [
        {**VALID, "body": "a" * 2001},
        {**VALID, "media": []},
        {**VALID, "media": [{"type": "image"}] * 11},
        {**VALID, "media": [{"type": "audio"}]},
    ]
    for payload in invalid:
        h = Harness()
        res = await create_post(h, payload)
        assert res.status_code == 400
        assert res.json()["code"] == "VALIDATION_ERROR"
        assert set(res.json()) == {"error", "code"}
        assert h.repo.posts == {}


async def test_create_accepts_boundary_sizes():
    for payload in ({**VALID, "body": "a" * 2000}, {**VALID, "media": [{"type": "text"}] * 10}):
        assert (await create_post(Harness(), payload)).status_code == 201


async def test_delete_removes_cache_and_soft_deletes():
    h = Harness()
    post_id = (await create_post(h)).json()["post_id"]
    h.events.clear()
    res = await delete_post(h, post_id)
    assert res.status_code == 204
    assert await h.redis.exists(f"post:{post_id}") == 0
    assert h.repo.posts[uuid.UUID(post_id)]["deleted"] is True
    assert h.events == [{"action": "delete", "post_id": post_id, "author_id": "user-1"}]


async def test_delete_db_failure_restores_cache():
    h = Harness()
    post_id = (await create_post(h)).json()["post_id"]
    before = await h.redis.hgetall(f"post:{post_id}")
    h.repo.fail_delete = True
    h.events.clear()
    res = await delete_post(h, post_id)
    assert res.status_code == 500
    assert res.json()["code"] == "INTERNAL_ERROR"
    assert await h.redis.hgetall(f"post:{post_id}") == before
    assert 0 < await h.redis.ttl(f"post:{post_id}") <= 86_400
    assert h.repo.posts[uuid.UUID(post_id)]["deleted"] is False
    assert h.events == []


async def test_delete_cache_failure_keeps_db_untouched():
    h = Harness()
    post_id = (await create_post(h)).json()["post_id"]
    h.service.redis = BrokenRedis()
    res = await delete_post(h, post_id)
    assert res.status_code == 500
    assert h.repo.posts[uuid.UUID(post_id)]["deleted"] is False


async def test_delete_unknown_post_returns_404():
    res = await delete_post(Harness(), uuid.uuid4())
    assert res.status_code == 404
    assert res.json() == {"error": "게시물을 찾을 수 없습니다", "code": "NOT_FOUND"}


async def test_delete_twice_returns_404():
    h = Harness()
    post_id = (await create_post(h)).json()["post_id"]
    assert (await delete_post(h, post_id)).status_code == 204
    assert (await delete_post(h, post_id)).status_code == 404


async def test_delete_other_users_post_returns_404():
    h = Harness()
    post_id = (await create_post(h, user_id="user-1")).json()["post_id"]
    res = await delete_post(h, post_id, user_id="user-2")
    assert res.status_code == 404
    assert h.repo.posts[uuid.UUID(post_id)]["deleted"] is False
