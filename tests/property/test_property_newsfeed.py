import asyncio
import base64
import json
import uuid
from datetime import datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.newsfeed_service import InvalidCursorError, decode_cursor, encode_cursor
from tests.unit.test_newsfeed_service import BASE_MS, Harness, get_feed

TIMESTAMPS = st.lists(st.integers(min_value=0, max_value=20), max_size=60)


def build(timestamps):
    h = Harness()
    ids = [h.repo.add(BASE_MS + t) for t in timestamps]
    return h, ids


async def fill_cache(h, ids):
    for post_id in ids:
        await h.feed_add(post_id)


async def collect_pages(h, limit):
    pages, cursor = [], None
    while True:
        params = {"limit": limit, **({"cursor": cursor} if cursor else {})}
        body = (await get_feed(h, **params)).json()
        pages.append(body)
        cursor = body["next_cursor"]
        if cursor is None:
            return pages


def expected_order(h, ids):
    return [
        str(i)
        for i in sorted(ids, key=lambda i: (h.repo.posts[i].created_at, str(i)), reverse=True)
    ]


# Feature: newsfeed-system, Property 12: 피드는 항상 최신순(created_at 내림차순)으로 정렬된다
@given(timestamps=TIMESTAMPS, cached=st.booleans())
@settings(max_examples=100, deadline=None)
def test_feed_is_sorted_newest_first(timestamps, cached):
    async def run():
        h, ids = build(timestamps)
        if cached:
            await fill_cache(h, ids)
        return (await get_feed(h, limit=20)).json()["posts"]

    posts = asyncio.run(run())
    assert len(posts) == min(len(timestamps), 20)
    for a, b in zip(posts, posts[1:]):
        assert datetime.fromisoformat(a["created_at"]) >= datetime.fromisoformat(b["created_at"])


# Feature: newsfeed-system, Property 13: 피드 페이지 크기는 항상 20개 이하이며 cursor는 올바르게 동작한다
@given(timestamps=TIMESTAMPS, limit=st.integers(min_value=1, max_value=20), cached=st.booleans())
@settings(max_examples=100, deadline=None)
def test_pages_are_bounded_and_cursor_covers_all_posts(timestamps, limit, cached):
    async def run():
        h, ids = build(timestamps)
        if cached:
            await fill_cache(h, ids)
        return h, ids, await collect_pages(h, limit)

    h, ids, pages = asyncio.run(run())
    seen = []
    for page in pages:
        assert len(page["posts"]) <= limit
        assert page["has_more"] == (page["next_cursor"] is not None)
        seen += [p["post_id"] for p in page["posts"]]
    assert pages[-1]["next_cursor"] is None
    assert seen == expected_order(h, ids)


def structurally_invalid():
    valid_id = str(uuid.uuid4())
    wrong_shapes = st.sampled_from(
        [
            "not an object",
            [1, 2],
            {},
            {"post_id": valid_id},
            {"created_at": 1},
            {"post_id": valid_id, "created_at": "1"},
            {"post_id": 7, "created_at": 1},
            {"post_id": "nope", "created_at": 1},
            {"post_id": valid_id, "created_at": -5},
        ]
    ).map(lambda data: base64.b64encode(json.dumps(data).encode()).decode())
    truncated = st.integers(min_value=1, max_value=20).map(
        lambda n: encode_cursor(valid_id, BASE_MS)[:-n]
    )
    return st.one_of(st.text(max_size=60), wrong_shapes, truncated)


def is_decodable(cursor):
    try:
        decode_cursor(cursor)
    except InvalidCursorError:
        return False
    return True


# Feature: newsfeed-system, Property 14: 유효하지 않은 cursor는 항상 400을 반환한다
@given(cursor=structurally_invalid().filter(lambda c: not is_decodable(c)))
@settings(max_examples=100, deadline=None)
def test_invalid_cursor_returns_400(cursor):
    res = asyncio.run(get_feed(Harness(), cursor=cursor))
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION_ERROR"


# Feature: newsfeed-system, Property 15: 캐시 미스 시 DB 폴백은 올바른 피드를 반환한다
@given(timestamps=TIMESTAMPS)
@settings(max_examples=100, deadline=None)
def test_cache_miss_falls_back_to_correct_feed(timestamps):
    async def run():
        h, ids = build(timestamps)
        deleted = set(ids[::3])
        h.repo.deleted |= deleted
        live = [i for i in ids if i not in deleted]
        return h, live, (await get_feed(h, limit=20)).json()

    h, live, body = asyncio.run(run())
    assert [p["post_id"] for p in body["posts"]] == expected_order(h, live)[:20]
    assert body["has_more"] == (len(live) > 20)
