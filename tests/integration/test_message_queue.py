import asyncio
import json
import logging
import os
import signal
from functools import partial

import pytest

import worker_main
from app.core.database import async_session
from app.core.redis_client import redis_client
from app.services import message_queue
from app.services.fanout_service import FanoutService
from app.services.social_graph_repository import SocialGraphRepository
from app.services.social_graph_service import SocialGraphService
from app.workers.fanout_worker import FanoutWorker
from tests.integration.conftest import make_user
from tests.integration.test_post_publish_flow import befriend, publish_post

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

MESSAGE = {"post_id": "p-1", "friend_ids": ["f-1"], "action": "insert", "created_at": 1}


async def get_one(channel, queue_name):
    return await (await channel.get_queue(queue_name)).get(timeout=5)


async def test_fanout_falls_back_to_dlq_after_publish_failures(client, channel):
    author = await make_user()
    friend = await make_user()
    await befriend(client, author, friend)

    async def broken_publish(message):
        raise ConnectionError("broker down")

    async with async_session() as session:
        repo = SocialGraphRepository(session)
        fanout = FanoutService(
            SocialGraphService(repo, redis_client),
            repo,
            broken_publish,
            partial(message_queue.publish_dead_letter, channel),
        )
        await fanout.handle({"action": "delete", "post_id": "p-9", "author_id": author})

    incoming = await get_one(channel, message_queue.FANOUT_DLQ)
    await incoming.ack()
    assert json.loads(incoming.body) == {"post_id": "p-9", "friend_ids": [friend], "action": "delete"}


async def test_rejected_message_is_dead_lettered(channel):
    await message_queue.publish_message(channel, MESSAGE)
    incoming = await get_one(channel, message_queue.FANOUT_QUEUE)
    await incoming.reject(requeue=False)

    dead = await get_one(channel, message_queue.FANOUT_DLQ)
    await dead.ack()
    assert json.loads(dead.body) == MESSAGE


async def test_worker_failure_returns_message_to_queue(channel):
    class BrokenRedis:
        async def zadd(self, *args, **kwargs):
            raise ConnectionError("redis down")

    async def send_notifications(post_id, friend_ids):
        pass

    await message_queue.publish_message(channel, MESSAGE)
    incoming = await get_one(channel, message_queue.FANOUT_QUEUE)
    await FanoutWorker(BrokenRedis(), send_notifications).process(incoming)

    again = await get_one(channel, message_queue.FANOUT_QUEUE)
    await again.ack()
    assert again.redelivered
    assert json.loads(again.body) == MESSAGE


async def test_worker_main_consumes_until_sigterm(client, caplog):
    caplog.set_level(logging.INFO)
    author = await make_user()
    friend = await make_user(device_token="device-token-friend")
    await befriend(client, author, friend)

    loop = asyncio.get_running_loop()
    main = asyncio.create_task(worker_main.main())
    try:
        post_id = (await publish_post(client, author))["post_id"]
        async with asyncio.timeout(5):
            while "dev push, not sent: post=" + post_id not in caplog.text:
                await asyncio.sleep(0.02)
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(main, 5)
    finally:
        main.cancel()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
    assert await redis_client.zscore(f"newsfeed:{friend}", post_id) is not None
    assert "fanout worker stopped" in caplog.text
