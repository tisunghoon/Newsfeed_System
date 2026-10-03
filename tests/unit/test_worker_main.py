import contextlib
import logging
import uuid

import worker_main


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
