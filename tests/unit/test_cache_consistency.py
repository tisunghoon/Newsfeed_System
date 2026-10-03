import uuid

from fakeredis.aioredis import FakeRedis

from app.schemas.post import PostCreateRequest
from app.services.fanout_service import FanoutService
from app.services.post_service import POST_CACHE_TTL, PostService
from app.workers.fanout_worker import NEWSFEED_TTL_SECONDS, FanoutWorker
from tests.unit.test_fanout_service import FakeGraph
from tests.unit.test_fanout_service import FakeRepo as FakeSocialRepo
from tests.unit.test_post_service import FakeRepo, create_post
from tests.unit.test_post_service import Harness as PostHarness

AUTHOR = "author-1"
FRIENDS = ["a", "b", "c"]
REQUEST = PostCreateRequest(body="hello", media=[{"type": "text"}])


class Pipeline:
    def __init__(self, friend_ids=FRIENDS, queue_failures=0):
        self.redis = FakeRedis(decode_responses=True)
        self.repo = FakeRepo()
        self.queue_failures = queue_failures
        self.queued = []
        self.dead = []
        self.notified = []
        self.worker = FanoutWorker(self.redis, self.notify)
        self.fanout = FanoutService(
            FakeGraph(friend_ids), FakeSocialRepo(), self.publish_message, self.publish_dead
        )
        self.posts = PostService(self.repo, self.redis, self.fanout.handle)

    async def notify(self, post_id, friend_ids):
        self.notified.append((post_id, friend_ids))

    async def publish_message(self, message):
        self.queued.append(message)
        if len(self.queued) <= self.queue_failures:
            raise ConnectionError("broker down")
        await self.worker.handle(message)

    async def publish_dead(self, message):
        self.dead.append(message)

    async def feed(self, friend_id):
        return await self.redis.zrange(f"newsfeed:{friend_id}", 0, -1)


async def test_delete_event_removes_post_from_every_friend_feed():
    p = Pipeline()
    kept = (await p.posts.create(AUTHOR, REQUEST)).post_id
    removed = (await p.posts.create(AUTHOR, REQUEST)).post_id
    for friend in FRIENDS:
        assert set(await p.feed(friend)) == {str(kept), str(removed)}

    await p.posts.delete(AUTHOR, removed)

    for friend in FRIENDS:
        assert await p.feed(friend) == [str(kept)]
    assert p.queued[-1] == {"post_id": str(removed), "friend_ids": FRIENDS, "action": "delete"}
    assert await p.redis.exists(f"post:{removed}") == 0
    assert p.repo.posts[removed]["deleted"] is True


async def test_delete_leaves_other_users_feeds_untouched():
    p = Pipeline()
    post_id = (await p.posts.create(AUTHOR, REQUEST)).post_id
    await p.redis.zadd("newsfeed:stranger", {str(post_id): 1})

    await p.posts.delete(AUTHOR, post_id)

    assert await p.feed("stranger") == [str(post_id)]


async def test_delete_message_goes_to_dead_letter_after_three_failed_publishes():
    p = Pipeline()
    post_id = (await p.posts.create(AUTHOR, REQUEST)).post_id
    p.queued.clear()
    p.queue_failures = 3

    await p.posts.delete(AUTHOR, post_id)

    assert len(p.queued) == 3
    assert p.dead == [{"post_id": str(post_id), "friend_ids": FRIENDS, "action": "delete"}]
    assert await p.feed("a") == [str(post_id)]


async def test_failed_delete_publishes_nothing_to_feeds():
    p = Pipeline()
    post_id = (await p.posts.create(AUTHOR, REQUEST)).post_id
    p.queued.clear()
    p.repo.fail_delete = True

    try:
        await p.posts.delete(AUTHOR, post_id)
    except Exception:
        pass

    assert p.queued == []
    assert await p.feed("a") == [str(post_id)]


async def test_post_cache_ttl_on_create():
    p = Pipeline()
    post_id = (await p.posts.create(AUTHOR, REQUEST)).post_id
    assert POST_CACHE_TTL - 5 <= await p.redis.ttl(f"post:{post_id}") <= POST_CACHE_TTL


async def test_post_cache_ttl_on_restore_keeps_remaining_ttl():
    h = PostHarness()
    post_id = (await create_post(h)).json()["post_id"]
    await h.redis.expire(f"post:{post_id}", 1_000)
    h.repo.fail_delete = True
    try:
        await h.service.delete("user-1", uuid.UUID(post_id))
    except Exception:
        pass
    assert 990 <= await h.redis.ttl(f"post:{post_id}") <= 1_000


async def test_post_cache_ttl_on_restore_of_key_without_ttl():
    h = PostHarness()
    post_id = (await create_post(h)).json()["post_id"]
    await h.redis.persist(f"post:{post_id}")
    h.repo.fail_delete = True
    try:
        await h.service.delete("user-1", uuid.UUID(post_id))
    except Exception:
        pass
    assert POST_CACHE_TTL - 5 <= await h.redis.ttl(f"post:{post_id}") <= POST_CACHE_TTL


async def test_newsfeed_cache_ttl_after_create_flow():
    p = Pipeline()
    await p.posts.create(AUTHOR, REQUEST)
    for friend in FRIENDS:
        ttl = await p.redis.ttl(f"newsfeed:{friend}")
        assert NEWSFEED_TTL_SECONDS - 5 <= ttl <= NEWSFEED_TTL_SECONDS


async def test_newsfeed_cache_ttl_refreshed_on_every_insert():
    p = Pipeline()
    await p.posts.create(AUTHOR, REQUEST)
    await p.redis.expire("newsfeed:a", 100)
    await p.posts.create(AUTHOR, REQUEST)
    assert NEWSFEED_TTL_SECONDS - 5 <= await p.redis.ttl("newsfeed:a") <= NEWSFEED_TTL_SECONDS
