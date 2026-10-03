import asyncio

from app.services import fanout_service
from app.services.fanout_service import PUSH_FOLLOWER_LIMIT, FanoutService

AUTHOR = "author-1"
INSERT = {"action": "insert", "post_id": "post-1", "author_id": AUTHOR, "created_at": 1_700_000_000_000}
DELETE = {"action": "delete", "post_id": "post-1", "author_id": AUTHOR}


class FakeGraph:
    def __init__(self, friend_ids=(), error=None, delay=0):
        self.friend_ids = list(friend_ids)
        self.error = error
        self.delay = delay

    async def get_friend_ids(self, user_id):
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return list(self.friend_ids)


class FakeRepo:
    def __init__(self, excluded=(), error=None):
        self.excluded = list(excluded)
        self.error = error

    async def list_excluding_friend_ids(self, author_id):
        if self.error:
            raise self.error
        return list(self.excluded)


class Harness:
    def __init__(self, friend_ids=(), excluded=(), failures=0, graph=None, repo=None):
        self.graph = graph or FakeGraph(friend_ids)
        self.repo = repo or FakeRepo(excluded)
        self.failures = failures
        self.attempts = 0
        self.sent = []
        self.dead = []
        self.service = FanoutService(self.graph, self.repo, self.publish, self.publish_dead)

    async def publish(self, message):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise ConnectionError("broker down")
        self.sent.append(message)

    async def publish_dead(self, message):
        self.dead.append(message)


async def test_insert_sends_filtered_friends():
    h = Harness(["a", "b", "c", "d"], excluded=["b", "d"])
    await h.service.handle(INSERT)
    assert h.sent == [
        {"post_id": "post-1", "friend_ids": ["a", "c"], "action": "insert", "created_at": 1_700_000_000_000}
    ]
    assert h.dead == []


async def test_insert_ignores_excluded_user_who_is_not_a_friend():
    h = Harness(["a"], excluded=["stranger"])
    await h.service.handle(INSERT)
    assert h.sent[0]["friend_ids"] == ["a"]


async def test_insert_skips_send_when_everyone_is_filtered():
    h = Harness(["a"], excluded=["a"])
    await h.service.handle(INSERT)
    assert h.sent == []


async def test_insert_pushes_at_limit():
    h = Harness([str(i) for i in range(PUSH_FOLLOWER_LIMIT)])
    await h.service.handle(INSERT)
    assert len(h.sent) == 1


async def test_insert_skips_push_above_limit():
    h = Harness([str(i) for i in range(PUSH_FOLLOWER_LIMIT + 1)])
    await h.service.handle(INSERT)
    assert h.sent == []
    assert h.dead == []


async def test_publish_retries_then_succeeds():
    h = Harness(["a"], failures=2)
    await h.service.handle(INSERT)
    assert h.attempts == 3
    assert len(h.sent) == 1
    assert h.dead == []


async def test_publish_goes_to_dead_letter_after_three_failures():
    h = Harness(["a"], failures=10)
    await h.service.handle(INSERT)
    assert h.attempts == 3
    assert h.sent == []
    assert h.dead == [
        {"post_id": "post-1", "friend_ids": ["a"], "action": "insert", "created_at": 1_700_000_000_000}
    ]


async def test_dead_letter_failure_does_not_propagate():
    h = Harness(["a"], failures=10)

    async def broken(message):
        raise ConnectionError("broker down")

    h.service.publish_dead_letter = broken
    await h.service.handle(INSERT)
    assert h.attempts == 3


async def test_graph_failure_aborts_without_raising():
    h = Harness(graph=FakeGraph(error=RuntimeError("db down")))
    await h.service.handle(INSERT)
    assert h.attempts == 0
    assert h.dead == []


async def test_graph_timeout_aborts_without_raising(monkeypatch):
    monkeypatch.setattr(fanout_service, "FRIEND_LOOKUP_TIMEOUT", 0.01)
    h = Harness(graph=FakeGraph(["a"], delay=1))
    await h.service.handle(INSERT)
    assert h.attempts == 0


async def test_filter_lookup_failure_aborts_without_raising():
    h = Harness(["a"], repo=FakeRepo(error=RuntimeError("db down")))
    await h.service.handle(INSERT)
    assert h.attempts == 0


async def test_delete_targets_all_friends_without_filter():
    h = Harness(["a", "b"], excluded=["b"])
    await h.service.handle(DELETE)
    assert h.sent == [{"post_id": "post-1", "friend_ids": ["a", "b"], "action": "delete"}]


async def test_delete_skips_pull_authors():
    h = Harness([str(i) for i in range(PUSH_FOLLOWER_LIMIT + 1)])
    await h.service.handle(DELETE)
    assert h.sent == []


async def test_delete_retries_and_dead_letters():
    h = Harness(["a"], failures=10)
    await h.service.handle(DELETE)
    assert h.attempts == 3
    assert h.dead == [{"post_id": "post-1", "friend_ids": ["a"], "action": "delete"}]


async def test_delete_graph_failure_aborts_without_raising():
    h = Harness(graph=FakeGraph(error=RuntimeError("db down")))
    await h.service.handle(DELETE)
    assert h.attempts == 0
