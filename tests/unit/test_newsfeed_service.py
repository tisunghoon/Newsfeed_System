import base64
import json
import uuid
from datetime import datetime, timezone

import httpx
from fakeredis.aioredis import FakeRedis
from fastapi import FastAPI

from app.core.errors import register_error_handlers
from app.middleware.auth import AuthMiddleware
from app.routers import newsfeed as newsfeed_router
from app.schemas.post import MediaItem, PostResponse
from app.services.newsfeed_service import NewsfeedService, decode_cursor, encode_cursor
from app.workers.fanout_worker import newsfeed_key
from tests.unit.test_auth_middleware import auth_header, client_for
from tests.unit.test_post_service import BrokenRedis

FEED = "/v1/me/feed"
BASE_MS = 1_700_000_000_000


class FakeRepo:
    def __init__(self, fail=False):
        self.posts: dict[uuid.UUID, PostResponse] = {}
        self.deleted: set[uuid.UUID] = set()
        self.fail = fail
        self.detail_reads: list[list[str]] = []

    def add(self, created_ms, post_id=None, deleted=False):
        post_id = post_id or uuid.uuid4()
        self.posts[post_id] = PostResponse(
            post_id=post_id,
            author_id=uuid.uuid4(),
            body="hello",
            media=[MediaItem(type="image", url="https://cdn.example.com/a.jpg")],
            created_at=datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc),
        )
        if deleted:
            self.deleted.add(post_id)
        return post_id

    def _active(self):
        return [p for p in self.posts.values() if p.post_id not in self.deleted]

    async def list_friend_posts(self, user_id, cursor, limit):
        if self.fail:
            raise RuntimeError("db down")
        keyed = sorted(
            ((int(p.created_at.timestamp() * 1000), str(p.post_id), p) for p in self._active()),
            key=lambda t: t[:2],
            reverse=True,
        )
        if cursor is not None:
            keyed = [t for t in keyed if t[:2] < (cursor[0], cursor[1])]
        return [t[2] for t in keyed[:limit]]

    async def get_posts(self, post_ids):
        if self.fail:
            raise RuntimeError("db down")
        self.detail_reads.append(post_ids)
        return [p for p in self._active() if str(p.post_id) in post_ids]


class Harness:
    def __init__(self, repo=None, redis=None):
        self.repo = repo or FakeRepo()
        self.redis = redis if redis is not None else FakeRedis(decode_responses=True)
        self.service = NewsfeedService(self.repo, self.redis)

    async def cache_post(self, post_id):
        post = self.repo.posts[post_id]
        await self.redis.hset(
            f"post:{post_id}",
            mapping={
                "author_id": str(post.author_id),
                "body": post.body or "",
                "media_json": json.dumps([m.model_dump() for m in post.media]),
                "created_at": str(int(post.created_at.timestamp() * 1000)),
            },
        )

    async def feed_add(self, post_id, cache_detail=True):
        score = int(self.repo.posts[post_id].created_at.timestamp() * 1000)
        await self.redis.zadd(newsfeed_key("user-1"), {str(post_id): score})
        if cache_detail:
            await self.cache_post(post_id)

    def client(self) -> httpx.AsyncClient:
        app = FastAPI()
        register_error_handlers(app)
        app.add_middleware(AuthMiddleware)
        app.include_router(newsfeed_router.router)
        app.dependency_overrides[newsfeed_router.get_newsfeed_service] = lambda: self.service
        return client_for(app)


async def get_feed(h: Harness, **params) -> httpx.Response:
    async with h.client() as client:
        return await client.get(FEED, params=params, headers=auth_header("user-1"))


async def test_cache_hit_returns_posts_sorted_newest_first():
    h = Harness()
    ids = [h.repo.add(BASE_MS + offset) for offset in (5, 30, 10)]
    for post_id in ids:
        await h.feed_add(post_id)
    res = await get_feed(h)
    assert res.status_code == 200
    body = res.json()
    assert [p["post_id"] for p in body["posts"]] == [str(ids[1]), str(ids[2]), str(ids[0])]
    assert body["has_more"] is False
    assert body["next_cursor"] is None
    assert body["posts"][0]["media"] == [{"type": "image", "url": "https://cdn.example.com/a.jpg"}]
    assert h.repo.detail_reads == []


async def test_cache_miss_falls_back_to_db():
    h = Harness()
    ids = [h.repo.add(BASE_MS + i) for i in range(3)]
    res = await get_feed(h)
    assert res.status_code == 200
    assert [p["post_id"] for p in res.json()["posts"]] == [str(i) for i in reversed(ids)]


async def test_empty_feed_returns_empty_list():
    res = await get_feed(Harness())
    assert res.json() == {"posts": [], "next_cursor": None, "has_more": False}


async def test_pagination_walks_all_posts_without_duplicates():
    h = Harness()
    ids = [h.repo.add(BASE_MS + i // 3) for i in range(7)]
    for post_id in ids:
        await h.feed_add(post_id)
    seen, cursor = [], None
    while True:
        params = {"limit": 3, **({"cursor": cursor} if cursor else {})}
        body = (await get_feed(h, **params)).json()
        assert len(body["posts"]) <= 3
        seen += [p["post_id"] for p in body["posts"]]
        cursor = body["next_cursor"]
        assert body["has_more"] == (cursor is not None)
        if cursor is None:
            break
    expected = sorted(ids, key=lambda i: (h.repo.posts[i].created_at, str(i)), reverse=True)
    assert seen == [str(i) for i in expected]


async def test_fallback_pagination_matches_cache_pagination():
    h = Harness()
    for i in range(5):
        h.repo.add(BASE_MS + i // 2)
    first = (await get_feed(h, limit=2)).json()
    second = (await get_feed(h, limit=2, cursor=first["next_cursor"])).json()
    third = (await get_feed(h, limit=2, cursor=second["next_cursor"])).json()
    ids = [p["post_id"] for page in (first, second, third) for p in page["posts"]]
    assert len(ids) == len(set(ids)) == 5
    assert third["next_cursor"] is None


async def test_default_limit_is_20_and_max_is_20():
    h = Harness()
    for i in range(25):
        await h.feed_add(h.repo.add(BASE_MS + i))
    body = (await get_feed(h)).json()
    assert len(body["posts"]) == 20
    assert body["has_more"] is True
    res = await get_feed(h, limit=21)
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION_ERROR"
    assert (await get_feed(h, limit=0)).status_code == 400


def _raw_cursor(data) -> str:
    return base64.b64encode(json.dumps(data).encode()).decode()


async def test_invalid_cursors_return_400():
    h = Harness()
    valid_id = str(uuid.uuid4())
    bad = [
        "garbage!!",
        "abc",
        "",
        _raw_cursor("not an object"),
        _raw_cursor([1]),
        _raw_cursor({}),
        _raw_cursor({"post_id": valid_id, "created_at": "1"}),
        _raw_cursor({"post_id": valid_id, "created_at": True}),
        _raw_cursor({"post_id": 1, "created_at": 1}),
        encode_cursor("not-a-uuid", 1),
        encode_cursor(valid_id, -1),
        encode_cursor(valid_id, 1)[:-6],
    ]
    for cursor in bad:
        res = await get_feed(h, cursor=cursor)
        assert res.status_code == 400, cursor
        assert res.json()["code"] == "VALIDATION_ERROR"


async def test_cursor_roundtrip():
    post_id = str(uuid.uuid4())
    assert decode_cursor(encode_cursor(post_id, BASE_MS)) == (BASE_MS, post_id)


async def test_db_failure_on_fallback_returns_500():
    h = Harness(repo=FakeRepo(fail=True))
    res = await get_feed(h)
    assert res.status_code == 500
    assert res.json()["code"] == "INTERNAL_ERROR"


async def test_db_failure_on_detail_read_returns_500():
    h = Harness()
    post_id = h.repo.add(BASE_MS)
    await h.feed_add(post_id, cache_detail=False)
    h.repo.fail = True
    assert (await get_feed(h)).status_code == 500


async def test_missing_post_cache_is_read_from_db_and_backfilled():
    h = Harness()
    cached = h.repo.add(BASE_MS + 1)
    missing = h.repo.add(BASE_MS)
    await h.feed_add(cached)
    await h.feed_add(missing, cache_detail=False)
    body = (await get_feed(h)).json()
    assert [p["post_id"] for p in body["posts"]] == [str(cached), str(missing)]
    assert h.repo.detail_reads == [[str(missing)]]
    fields = await h.redis.hgetall(f"post:{missing}")
    assert fields["created_at"] == str(BASE_MS)
    assert fields["body"] == "hello"
    assert 0 < await h.redis.ttl(f"post:{missing}") <= 86_400


async def test_deleted_or_unknown_posts_are_excluded():
    h = Harness()
    alive = h.repo.add(BASE_MS + 2)
    deleted = h.repo.add(BASE_MS + 1, deleted=True)
    await h.feed_add(alive)
    await h.feed_add(deleted, cache_detail=False)
    await h.redis.zadd(newsfeed_key("user-1"), {str(uuid.uuid4()): BASE_MS})
    body = (await get_feed(h)).json()
    assert [p["post_id"] for p in body["posts"]] == [str(alive)]


async def test_empty_body_in_cache_is_returned_as_null():
    h = Harness()
    post_id = h.repo.add(BASE_MS)
    h.repo.posts[post_id].body = None
    await h.feed_add(post_id)
    assert (await get_feed(h)).json()["posts"][0]["body"] is None


async def test_redis_failure_falls_back_to_db():
    h = Harness(redis=BrokenRedis())
    post_id = h.repo.add(BASE_MS)
    res = await get_feed(h)
    assert res.status_code == 200
    assert [p["post_id"] for p in res.json()["posts"]] == [str(post_id)]


async def test_requires_auth():
    async with Harness().client() as client:
        assert (await client.get(FEED)).status_code == 401
