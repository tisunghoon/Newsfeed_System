import asyncio
import json
import logging

import pytest
from sqlalchemy import text

import worker_main
from app.core.database import async_session
from app.core.redis_client import redis_client
from app.routers import post as post_router
from app.services.message_queue import FANOUT_QUEUE
from app.workers.fanout_worker import FanoutWorker, run_worker
from tests.integration.conftest import make_user
from tests.unit.test_auth_middleware import auth_header

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

FEED = "/v1/me/feed"
POST = {"body": "hello", "media": [{"type": "image", "url": "https://img/1.png"}]}


async def befriend(client, user_id, friend_id):
    res = await client.post(f"/v1/me/friends/{friend_id}", headers=auth_header(user_id))
    assert res.status_code == 201


async def mute(user_id, friend_id):
    async with async_session() as session:
        await session.execute(
            text("UPDATE friendships SET muted = true WHERE user_id = :u AND friend_id = :f"),
            {"u": user_id, "f": friend_id},
        )
        await session.commit()


async def publish_post(client, author_id):
    res = await client.post(FEED, json=POST, headers=auth_header(author_id))
    assert res.status_code == 201
    await post_router.wait_fanout_tasks()
    return res.json()


async def test_post_reaches_friend_feed_through_queue_and_worker(client, channel, caplog):
    caplog.set_level(logging.INFO)
    author = await make_user()
    friend = await make_user(device_token="device-token-friend")
    muted = await make_user(device_token="device-token-muted")
    await befriend(client, author, friend)
    await befriend(client, author, muted)
    await mute(muted, author)

    created = await publish_post(client, author)
    post_id = created["post_id"]

    queue = await channel.get_queue(FANOUT_QUEUE)
    incoming = await queue.get(timeout=5)
    message = json.loads(incoming.body)
    assert message["post_id"] == post_id
    assert message["action"] == "insert"
    assert message["friend_ids"] == [friend]

    await FanoutWorker(redis_client, worker_main.send_notifications).process(incoming)

    assert await redis_client.zscore(f"newsfeed:{friend}", post_id) == message["created_at"]
    assert not await redis_client.exists(f"newsfeed:{muted}")
    assert await queue.get(fail=False) is None
    assert "dev push, not sent" in caplog.text
    assert "token=device-t" in caplog.text

    res = await client.get(FEED, headers=auth_header(friend))
    assert res.status_code == 200
    posts = res.json()["posts"]
    assert [p["post_id"] for p in posts] == [post_id]
    assert posts[0]["media"] == POST["media"]


async def test_run_worker_consumes_published_message(client, channel):
    author = await make_user()
    friend = await make_user()
    await befriend(client, author, friend)

    sent = []

    async def send_notifications(post_id, friend_ids):
        sent.append((post_id, friend_ids))

    worker = asyncio.create_task(run_worker(channel, FanoutWorker(redis_client, send_notifications)))
    try:
        post_id = (await publish_post(client, author))["post_id"]
        async with asyncio.timeout(5):
            while not sent:
                await asyncio.sleep(0.05)
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    assert sent == [(post_id, [friend])]
    assert await redis_client.zrange(f"newsfeed:{friend}", 0, -1) == [post_id]
