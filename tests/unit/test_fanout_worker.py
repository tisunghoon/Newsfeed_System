import json

import fakeredis.aioredis
import pytest

from app.workers.fanout_worker import NEWSFEED_MAX_SIZE, NEWSFEED_TTL_SECONDS, FanoutWorker

INSERT = {"post_id": "post-1", "friend_ids": ["a", "b"], "action": "insert", "created_at": 1_700_000_000_000}
DELETE = {"post_id": "post-1", "friend_ids": ["a", "b"], "action": "delete"}


class FlakyRedis:
    def __init__(self, redis, failures=0):
        self.redis = redis
        self.failures = failures
        self.calls = 0

    async def zadd(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("redis down")
        return await self.redis.zadd(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.redis, name)


class FakeIncoming:
    def __init__(self, body):
        self.body = body
        self.acked = False
        self.nacked = None

    async def ack(self):
        self.acked = True

    async def nack(self, requeue=True):
        self.nacked = requeue


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def notified():
    return []


@pytest.fixture
def worker(redis, notified):
    async def send_notifications(post_id, friend_ids):
        notified.append((post_id, friend_ids))

    return FanoutWorker(redis, send_notifications)


async def test_insert_adds_post_to_each_friend_feed(worker, redis):
    await worker.handle(INSERT)
    for friend_id in INSERT["friend_ids"]:
        assert await redis.zrange(f"newsfeed:{friend_id}", 0, -1, withscores=True) == [
            ("post-1", 1_700_000_000_000)
        ]


async def test_insert_sets_ttl(worker, redis):
    await worker.handle(INSERT)
    assert 0 < await redis.ttl("newsfeed:a") <= NEWSFEED_TTL_SECONDS


async def test_insert_beyond_limit_removes_oldest(worker, redis):
    for i in range(NEWSFEED_MAX_SIZE + 1):
        await worker.handle({**INSERT, "friend_ids": ["a"], "post_id": f"p{i}", "created_at": i})
    members = await redis.zrange("newsfeed:a", 0, -1)
    assert len(members) == NEWSFEED_MAX_SIZE
    assert members[0] == "p1"


async def test_delete_removes_post_from_feeds(worker, redis):
    await worker.handle(INSERT)
    await worker.handle(DELETE)
    assert await redis.zcard("newsfeed:a") == 0
    assert await redis.zcard("newsfeed:b") == 0


async def test_retry_succeeds_within_three_attempts(redis, notified):
    flaky = FlakyRedis(redis, failures=2)
    worker = FanoutWorker(flaky, lambda *args: _record(notified, *args))
    await worker.handle(INSERT)
    assert await redis.zcard("newsfeed:a") == 1
    assert await redis.zcard("newsfeed:b") == 1


async def test_raises_after_three_failed_attempts(redis, notified):
    flaky = FlakyRedis(redis, failures=3)
    worker = FanoutWorker(flaky, lambda *args: _record(notified, *args))
    with pytest.raises(ConnectionError):
        await worker.handle(INSERT)
    assert flaky.calls == 3
    assert notified == []


async def _record(notified, post_id, friend_ids):
    notified.append((post_id, friend_ids))


async def test_insert_triggers_notifications(worker, notified):
    await worker.handle(INSERT)
    assert notified == [("post-1", ["a", "b"])]


async def test_delete_does_not_trigger_notifications(worker, notified):
    await worker.handle(DELETE)
    assert notified == []


async def test_notification_failure_is_ignored(redis):
    async def failing(post_id, friend_ids):
        raise RuntimeError("notification down")

    worker = FanoutWorker(redis, failing)
    await worker.handle(INSERT)
    assert await redis.zcard("newsfeed:a") == 1


async def test_process_acks_on_success(worker):
    incoming = FakeIncoming(json.dumps(INSERT).encode())
    await worker.process(incoming)
    assert incoming.acked
    assert incoming.nacked is None


async def test_process_requeues_on_failure(redis, notified):
    worker = FanoutWorker(FlakyRedis(redis, failures=3), lambda *args: _record(notified, *args))
    incoming = FakeIncoming(json.dumps(INSERT).encode())
    await worker.process(incoming)
    assert not incoming.acked
    assert incoming.nacked is True
