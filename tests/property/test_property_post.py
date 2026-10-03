import asyncio
import uuid
from datetime import datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from tests.unit.test_post_service import BrokenRedis, FakeRepo, Harness, create_post

MEDIA_TYPES = st.sampled_from(["text", "image", "video"])
MEDIA_ITEM = st.fixed_dictionaries({"type": MEDIA_TYPES, "url": st.none() | st.text(max_size=100)})
VALID_PAYLOAD = st.fixed_dictionaries(
    {
        "body": st.text(max_size=2000),
        "media": st.lists(MEDIA_ITEM, min_size=1, max_size=10),
    }
)
BAD_ITEM = st.fixed_dictionaries(
    {"type": st.text(max_size=10).filter(lambda t: t not in {"text", "image", "video"})}
)
INVALID_PAYLOAD = st.one_of(
    st.fixed_dictionaries(
        {"body": st.text(min_size=2001, max_size=2100), "media": st.lists(MEDIA_ITEM, min_size=1, max_size=10)}
    ),
    st.fixed_dictionaries({"body": st.text(max_size=100), "media": st.just([])}),
    st.fixed_dictionaries(
        {"body": st.text(max_size=100), "media": st.lists(MEDIA_ITEM, min_size=11, max_size=15)}
    ),
    st.fixed_dictionaries(
        {
            "body": st.text(max_size=100),
            "media": st.tuples(st.lists(MEDIA_ITEM, max_size=9), BAD_ITEM).map(lambda t: [*t[0], t[1]]),
        }
    ),
)


# Feature: newsfeed-system, Property 4: 유효한 Post 저장은 항상 post_id와 타임스탬프를 포함한 201을 반환한다
@given(payload=VALID_PAYLOAD)
@settings(max_examples=100, deadline=None)
def test_valid_post_returns_201_with_id_and_timestamp(payload):
    res = asyncio.run(create_post(Harness(), payload))
    assert res.status_code == 201
    body = res.json()
    uuid.UUID(body["post_id"])
    datetime.fromisoformat(body["created_at"])


# Feature: newsfeed-system, Property 5: 무효한 Post는 항상 400을 반환한다
@given(payload=INVALID_PAYLOAD)
@settings(max_examples=100, deadline=None)
def test_invalid_post_returns_400(payload):
    h = Harness()
    res = asyncio.run(create_post(h, payload))
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION_ERROR"
    assert h.repo.posts == {}


# Feature: newsfeed-system, Property 6: DB 저장 실패 시 캐시 상태는 변경되지 않는다
@given(payload=VALID_PAYLOAD, existing=st.dictionaries(st.uuids().map(str), st.text(min_size=1, max_size=20), max_size=5))
@settings(max_examples=100, deadline=None)
def test_db_failure_leaves_cache_unchanged(payload, existing):
    async def run():
        h = Harness(repo=FakeRepo(fail_create=True))
        for post_id, body in existing.items():
            await h.redis.hset(f"post:{post_id}", mapping={"body": body})
        before = {key: await h.redis.hgetall(key) for key in await h.redis.keys("*")}
        res = await create_post(h, payload)
        after = {key: await h.redis.hgetall(key) for key in await h.redis.keys("*")}
        return res, before, after, h.events

    res, before, after, events = asyncio.run(run())
    assert res.status_code == 500
    assert after == before
    assert events == []


# Feature: newsfeed-system, Property 7: 캐시 저장 실패 시에도 DB 저장 성공이면 201을 반환한다
@given(payload=VALID_PAYLOAD)
@settings(max_examples=100, deadline=None)
def test_cache_failure_still_returns_201(payload):
    h = Harness(redis=BrokenRedis())
    res = asyncio.run(create_post(h, payload))
    assert res.status_code == 201
    assert uuid.UUID(res.json()["post_id"]) in h.repo.posts
