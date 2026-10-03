from app.services.notification_service import (
    MAX_RETRIES,
    RETRY_INTERVAL_SECONDS,
    NotificationService,
)


class FakeRepo:
    def __init__(self, targets=(), error=None):
        self.targets = list(targets)
        self.error = error
        self.queries = []

    async def list_push_targets(self, user_ids):
        self.queries.append(list(user_ids))
        if self.error:
            raise self.error
        return [(user_id, token) for user_id, token in self.targets if user_id in user_ids]


class Harness:
    def __init__(self, targets=(), failures=None, repo=None):
        self.repo = repo or FakeRepo(targets)
        self.failures = failures or {}
        self.calls = []
        self.sent = []
        self.sleeps = []
        self.service = NotificationService(self.repo, self.push, self.sleep)

    async def push(self, device_token, post_id):
        self.calls.append(device_token)
        if self.failures.get(device_token, 0) >= self.calls.count(device_token):
            raise ConnectionError("push gateway down")
        self.sent.append((device_token, post_id))

    async def sleep(self, seconds):
        self.sleeps.append(seconds)


async def test_sends_to_each_target():
    h = Harness([("a", "token-a"), ("b", "token-b")])
    await h.service.send_notifications("post-1", ["a", "b"])
    assert sorted(h.sent) == [("token-a", "post-1"), ("token-b", "post-1")]
    assert h.sleeps == []


async def test_queries_targets_once_with_all_friends():
    h = Harness([("a", "token-a")])
    await h.service.send_notifications("post-1", ["a", "b", "c"])
    assert h.repo.queries == [["a", "b", "c"]]


async def test_users_missing_from_targets_receive_nothing():
    h = Harness([("a", "token-a")])
    await h.service.send_notifications("post-1", ["a", "disabled", "no-token"])
    assert h.sent == [("token-a", "post-1")]


async def test_empty_friend_list_skips_lookup():
    h = Harness()
    await h.service.send_notifications("post-1", [])
    assert h.repo.queries == []
    assert h.calls == []


async def test_retries_with_interval_then_succeeds():
    h = Harness([("a", "token-a")], failures={"token-a": 2})
    await h.service.send_notifications("post-1", ["a"])
    assert h.calls == ["token-a"] * 3
    assert h.sleeps == [RETRY_INTERVAL_SECONDS] * 2
    assert h.sent == [("token-a", "post-1")]


async def test_gives_up_after_three_retries():
    h = Harness([("a", "token-a")], failures={"token-a": 100})
    await h.service.send_notifications("post-1", ["a"])
    assert len(h.calls) == MAX_RETRIES + 1
    assert h.sleeps == [30, 30, 30]
    assert h.sent == []


async def test_succeeds_on_last_retry():
    h = Harness([("a", "token-a")], failures={"token-a": MAX_RETRIES})
    await h.service.send_notifications("post-1", ["a"])
    assert h.sent == [("token-a", "post-1")]


async def test_one_recipient_failing_does_not_affect_others():
    h = Harness([("a", "token-a"), ("b", "token-b")], failures={"token-a": 100})
    await h.service.send_notifications("post-1", ["a", "b"])
    assert h.sent == [("token-b", "post-1")]


async def test_lookup_failure_aborts_without_raising():
    h = Harness(repo=FakeRepo(error=RuntimeError("db down")))
    await h.service.send_notifications("post-1", ["a"])
    assert h.calls == []
