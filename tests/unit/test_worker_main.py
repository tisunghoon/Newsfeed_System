import asyncio
import contextlib
import logging
import os
import signal
import uuid
from types import SimpleNamespace

import worker_main
from app.core import rabbitmq_client
from app.services import message_queue


class FakeSession:
    async def execute(self, query):
        return [(uuid.uuid4(), "token-abcdefgh")]


async def test_send_notifications_opens_new_session_per_call(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    opened = []

    @contextlib.asynccontextmanager
    async def session_factory():
        session = FakeSession()
        opened.append(session)
        yield session

    monkeypatch.setattr(worker_main, "async_session", session_factory)
    friend_ids = [str(uuid.uuid4())]
    await worker_main.send_notifications("post-1", friend_ids)
    await worker_main.send_notifications("post-2", friend_ids)
    assert len(opened) == 2
    assert opened[0] is not opened[1]
    assert caplog.text.count("dev push, not sent") == 2


async def test_main_closes_mq_redis_db_when_signal_cancels_worker(monkeypatch):
    calls = []
    started = asyncio.Event()

    class FakeChannel:
        async def set_qos(self, prefetch_count):
            calls.append("qos")

    class FakeConnection:
        async def close(self):
            calls.append("mq close")

    async def connect():
        return FakeConnection()

    async def open_channel(connection):
        return FakeChannel()

    async def declare_queues(channel):
        calls.append("declare queues")

    async def gated_run_worker(channel, worker):
        started.set()
        await asyncio.Event().wait()

    async def redis_close():
        calls.append("redis close")

    async def engine_dispose():
        calls.append("db dispose")

    monkeypatch.setattr(rabbitmq_client, "connect", connect)
    monkeypatch.setattr(rabbitmq_client, "open_channel", open_channel)
    monkeypatch.setattr(message_queue, "declare_queues", declare_queues)
    monkeypatch.setattr(worker_main, "run_worker", gated_run_worker)
    monkeypatch.setattr(worker_main, "redis_client", SimpleNamespace(aclose=redis_close))
    monkeypatch.setattr(worker_main, "engine", SimpleNamespace(dispose=engine_dispose))

    loop = asyncio.get_running_loop()
    main = asyncio.create_task(worker_main.main())
    try:
        await asyncio.wait_for(started.wait(), 1)
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(main, 1)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
    assert calls == ["qos", "declare queues", "mq close", "redis close", "db dispose"]
