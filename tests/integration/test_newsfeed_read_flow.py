import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.database import async_session
from app.core.redis_client import redis_client
from tests.integration.conftest import make_user
from tests.integration.test_post_publish_flow import befriend
from tests.unit.test_auth_middleware import auth_header

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

FEED = "/v1/me/feed"
BASE = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)


async def insert_post(author_id, created_at, deleted=False, media=(("image", "https://img/a.png"),)):
    async with async_session() as session:
        post_id = await session.scalar(
            text(
                "INSERT INTO posts (author_id, body, created_at, deleted_at) "
                "VALUES (:a, :b, :c, CASE WHEN :d THEN now() END) RETURNING id"
            ),
            {"a": author_id, "b": f"post at {created_at.isoformat()}", "c": created_at, "d": deleted},
        )
        for position, (media_type, url) in enumerate(media):
            await session.execute(
                text(
                    "INSERT INTO post_media (post_id, media_type, url, position) VALUES (:p, :t, :u, :i)"
                ),
                {"p": post_id, "t": media_type, "u": url, "i": position},
            )
        await session.commit()
        return str(post_id)


async def sql_ms(post_ids):
    async with async_session() as session:
        rows = await session.execute(
            text(
                "SELECT id, floor(extract(epoch FROM created_at) * 1000)::bigint "
                "FROM posts WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": post_ids},
        )
        return {str(post_id): ms for post_id, ms in rows}


def cursor_ms(cursor):
    return json.loads(base64.b64decode(cursor, altchars=b"-_"))["created_at"]


async def read_all(client, user_id, limit):
    pages, cursor = [], None
    while True:
        params = {"limit": limit} | ({"cursor": cursor} if cursor else {})
        res = await client.get(FEED, params=params, headers=auth_header(user_id))
        assert res.status_code == 200
        body = res.json()
        pages.append(body)
        if not body["has_more"]:
            return pages
        cursor = body["next_cursor"]


async def test_cache_miss_falls_back_to_db(client):
    reader = await make_user()
    friend = await make_user()
    blocked = await make_user()
    stranger = await make_user()
    await befriend(client, reader, friend)
    await befriend(client, reader, blocked)
    async with async_session() as session:
        await session.execute(
            text("UPDATE friendships SET blocked = true WHERE user_id = :u AND friend_id = :f"),
            {"u": reader, "f": blocked},
        )
        await session.commit()

    older = await insert_post(friend, BASE, media=(("image", "https://img/1.png"), ("video", "https://v/1.mp4")))
    newer = await insert_post(friend, BASE + timedelta(seconds=1))
    await insert_post(friend, BASE + timedelta(seconds=2), deleted=True)
    await insert_post(blocked, BASE + timedelta(seconds=3))
    await insert_post(stranger, BASE + timedelta(seconds=4))

    res = await client.get(FEED, headers=auth_header(reader))
    assert res.status_code == 200
    body = res.json()
    assert [p["post_id"] for p in body["posts"]] == [newer, older]
    assert body["posts"][1]["media"] == [
        {"type": "image", "url": "https://img/1.png"},
        {"type": "video", "url": "https://v/1.mp4"},
    ]
    assert body["posts"][1]["author_id"] == friend
    assert body["has_more"] is False
    assert not await redis_client.exists(f"newsfeed:{reader}")


async def test_db_cursor_pagination_matches_sql_millisecond_boundaries(client):
    reader = await make_user()
    friend = await make_user()
    await befriend(client, reader, friend)
    offsets_us = [0, 999, 500, 1_000, 1_999, 2_000, 122_999, 123_000, 999_999, 1_000_000]
    post_ids = [await insert_post(friend, BASE + timedelta(microseconds=us)) for us in offsets_us]
    expected_ms = await sql_ms(post_ids)
    expected = sorted(post_ids, key=lambda p: (expected_ms[p], p), reverse=True)

    for limit in (1, 2, 3):
        pages = await read_all(client, reader, limit)
        seen = [p["post_id"] for page in pages for p in page["posts"]]
        assert seen == expected
        for page in pages[:-1]:
            last = page["posts"][-1]["post_id"]
            assert cursor_ms(page["next_cursor"]) == expected_ms[last]


async def test_cache_hit_loads_details_from_db_and_skips_deleted(client):
    reader = await make_user()
    friend = await make_user()
    first = await insert_post(friend, BASE)
    second = await insert_post(friend, BASE + timedelta(seconds=1))
    deleted = await insert_post(friend, BASE + timedelta(seconds=2), deleted=True)
    ms = await sql_ms([first, second, deleted])
    await redis_client.zadd(f"newsfeed:{reader}", ms)

    res = await client.get(FEED, params={"limit": 1}, headers=auth_header(reader))
    body = res.json()
    assert res.status_code == 200
    assert body["posts"] == []
    assert body["has_more"] is True

    pages = await read_all(client, reader, 2)
    assert [p["post_id"] for page in pages for p in page["posts"]] == [second, first]
    cached = await redis_client.hgetall(f"post:{second}")
    assert cached["author_id"] == friend
    assert cached["created_at"] == str(ms[second])
    assert not await redis_client.exists(f"post:{deleted}")
