import asyncio

from hypothesis import given, settings
from hypothesis import strategies as st

from tests.unit.test_notification_service import Harness

USER_IDS = st.uuids().map(str)


# Feature: newsfeed-system, Property 16: 알림은 push_enabled 설정에 따라 정확히 발송된다
@given(
    friends=st.lists(USER_IDS, max_size=30, unique=True),
    flags=st.lists(st.booleans(), max_size=30),
)
@settings(max_examples=100, deadline=None)
def test_push_is_sent_only_to_enabled_users(friends, flags):
    enabled = [friend for friend, flag in zip(friends, flags) if flag]
    h = Harness([(friend, f"token-{friend}") for friend in enabled])
    asyncio.run(h.service.send_notifications("post-1", friends))
    assert sorted(token for token, _ in h.sent) == sorted(f"token-{friend}" for friend in enabled)
    assert {post_id for _, post_id in h.sent} <= {"post-1"}
