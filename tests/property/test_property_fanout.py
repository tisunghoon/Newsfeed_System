import asyncio

from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.fanout_service import PUSH_FOLLOWER_LIMIT
from tests.unit.test_fanout_service import INSERT, Harness

USER_IDS = st.uuids().map(str)


# Feature: newsfeed-system, Property 8: 팬아웃 필터는 차단/알림 비활성화 친구를 항상 제외한다
@given(
    friends=st.lists(USER_IDS, max_size=30, unique=True),
    flags=st.lists(st.booleans(), max_size=30),
)
@settings(max_examples=100, deadline=None)
def test_filter_excludes_blocked_and_muted_friends(friends, flags):
    excluded = [friend for friend, flag in zip(friends, flags) if flag]
    h = Harness(friends, excluded=excluded)
    asyncio.run(h.service.handle(INSERT))
    sent = [friend for message in h.sent for friend in message["friend_ids"]]
    assert not set(sent) & set(excluded)
    assert set(sent) == set(friends) - set(excluded)


# Feature: newsfeed-system, Property 9: 셀러브리티 Post는 Newsfeed_Cache에 즉시 반영되지 않는다
@given(extra=st.integers(min_value=1, max_value=50), action=st.sampled_from(["insert", "delete"]))
@settings(max_examples=100, deadline=None)
def test_celebrity_post_is_never_pushed(extra, action):
    h = Harness([str(i) for i in range(PUSH_FOLLOWER_LIMIT + extra)])
    asyncio.run(h.service.handle({**INSERT, "action": action}))
    assert h.attempts == 0
    assert h.sent == []
    assert h.dead == []


# Feature: newsfeed-system, Property 11: 재시도 로직은 최대 3회를 정확히 준수한다
@given(failures=st.integers(min_value=0, max_value=10))
@settings(max_examples=100, deadline=None)
def test_publish_retries_at_most_three_times(failures):
    h = Harness(["friend"], failures=failures)
    asyncio.run(h.service.handle(INSERT))
    if failures < 3:
        assert h.attempts == failures + 1
        assert len(h.sent) == 1
        assert h.dead == []
    else:
        assert h.attempts == 3
        assert h.sent == []
        assert len(h.dead) == 1
