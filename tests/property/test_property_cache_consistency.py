import asyncio
import uuid

from hypothesis import given, settings
from hypothesis import strategies as st

from tests.property.test_property_post import VALID_PAYLOAD
from tests.unit.test_post_service import BrokenRedis, Harness, create_post, delete_post

FAILURE = st.sampled_from(["cache", "db"])


async def stored_post(h, payload):
    post_id = (await create_post(h, payload)).json()["post_id"]
    h.events.clear()
    return post_id


# Feature: newsfeed-system, Property 21: Post 삭제 후 캐시와 DB에서 모두 제거된다
@given(payload=VALID_PAYLOAD)
@settings(max_examples=100, deadline=None)
def test_deleted_post_is_gone_from_cache_and_db(payload):
    async def run():
        h = Harness()
        post_id = await stored_post(h, payload)
        assert (await delete_post(h, post_id)).status_code == 204
        assert await h.redis.exists(f"post:{post_id}") == 0
        assert not await h.repo.exists_active(uuid.UUID(post_id), "user-1")

    asyncio.run(run())


# Feature: newsfeed-system, Property 22: 부분 삭제 실패 시 캐시와 DB 상태는 삭제 이전으로 유지된다
@given(payload=VALID_PAYLOAD, failure=FAILURE)
@settings(max_examples=100, deadline=None)
def test_partial_delete_failure_keeps_cache_and_db_unchanged(payload, failure):
    async def run():
        h = Harness()
        post_id = await stored_post(h, payload)
        key = f"post:{post_id}"
        before = await h.redis.hgetall(key)
        if failure == "db":
            h.repo.fail_delete = True
        else:
            h.service.redis = BrokenRedis()
        res = await delete_post(h, post_id)
        h.service.redis = h.redis
        assert res.status_code == 500
        assert res.json()["code"] == "INTERNAL_ERROR"
        assert await h.redis.hgetall(key) == before
        assert await h.redis.ttl(key) > 0
        assert await h.repo.exists_active(uuid.UUID(post_id), "user-1")
        assert h.events == []

    asyncio.run(run())
