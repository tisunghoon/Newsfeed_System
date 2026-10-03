import asyncio
import json
import time
from functools import partial

import pytest
from sqlalchemy import text

from app.core.database import async_session
from app.core.redis_client import redis_client
from app.routers import post as post_router
from app.services import message_queue
from app.services.fanout_service import FanoutService
from app.services.social_graph_repository import SocialGraphRepository
from app.services.social_graph_service import FRIEND_LIMIT, SocialGraphService
from app.workers.fanout_worker import FanoutWorker, run_worker
from tests.integration.conftest import make_user
from tests.integration.test_post_publish_flow import befriend, publish_post
from tests.unit.test_auth_middleware import auth_header

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def add_bulk_friends(author_id, count):
    async with async_session() as session:
        await session.execute(
            text(
                "INSERT INTO users (username, email) "
                "SELECT 'bulk' || g, 'bulk' || g || '@example.com' FROM generate_series(1, :n) g"
            ),
            {"n": count},
        )
        await session.execute(
            text(
                "INSERT INTO friendships (user_id, friend_id) "
                "SELECT :a, id FROM users WHERE username LIKE 'bulk%' "
                "UNION ALL SELECT id, :a FROM users WHERE username LIKE 'bulk%'"
            ),
            {"a": author_id},
        )
        await session.commit()


async def test_friend_lookup_and_fanout_finish_within_2000ms(channel):
    author = await make_user()
    await add_bulk_friends(author, FRIEND_LIMIT)

    async with async_session() as session:
        repo = SocialGraphRepository(session)
        started = time.perf_counter()
        friend_ids = await SocialGraphService(repo, redis_client).get_friend_ids(author)
        lookup = time.perf_counter() - started
    assert len(friend_ids) == FRIEND_LIMIT
    assert lookup < 2.0

    async with async_session() as session:
        repo = SocialGraphRepository(session)
        fanout = FanoutService(
            SocialGraphService(repo, redis_client),
            repo,
            partial(message_queue.publish_message, channel),
            partial(message_queue.publish_dead_letter, channel),
        )
        await redis_client.delete(f"friends:{author}")
        started = time.perf_counter()
        await fanout.handle({"action": "insert", "post_id": "p-1", "author_id": author, "created_at": 1})
        elapsed = time.perf_counter() - started
    assert elapsed < 2.0

    incoming = await (await channel.get_queue(message_queue.FANOUT_QUEUE)).get(timeout=5)
    await incoming.ack()
    assert len(json.loads(incoming.body)["friend_ids"]) == FRIEND_LIMIT


async def test_deleted_post_leaves_friend_feed_within_30s(client, channel):
    author = await make_user()
    friend = await make_user()
    await befriend(client, author, friend)

    async def send_notifications(post_id, friend_ids):
        pass

    worker = asyncio.create_task(run_worker(channel, FanoutWorker(redis_client, send_notifications)))
    key = f"newsfeed:{friend}"
    try:
        post_id = (await publish_post(client, author))["post_id"]
        async with asyncio.timeout(5):
            while await redis_client.zscore(key, post_id) is None:
                await asyncio.sleep(0.02)

        started = time.perf_counter()
        res = await client.delete(f"/v1/me/feed/{post_id}", headers=auth_header(author))
        assert res.status_code == 204
        await post_router.wait_fanout_tasks()
        async with asyncio.timeout(30):
            while await redis_client.zscore(key, post_id) is not None:
                await asyncio.sleep(0.02)
        elapsed = time.perf_counter() - started
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    assert elapsed < 30
    assert not await redis_client.exists(f"post:{post_id}")
    async with async_session() as session:
        deleted_at = await session.scalar(text("SELECT deleted_at FROM posts WHERE id = :p"), {"p": post_id})
    assert deleted_at is not None

    res = await client.delete(f"/v1/me/feed/{post_id}", headers=auth_header(author))
    assert res.status_code == 404
    res = await client.get("/v1/me/feed", headers=auth_header(friend))
    assert res.json()["posts"] == []
