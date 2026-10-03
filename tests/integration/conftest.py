import os
import subprocess
import sys
import uuid

PG_URL = "postgresql://newsfeed:newsfeed@localhost:5432"
TEST_DB = "newsfeed_test"
MQ_ADMIN_URL = "http://localhost:15672/api"
MQ_VHOST = "newsfeed_test"

os.environ["DATABASE_URL"] = f"postgresql+asyncpg://newsfeed:newsfeed@localhost:5432/{TEST_DB}"
os.environ["REDIS_URL"] = "redis://localhost:6379/15"
os.environ["RABBITMQ_URL"] = f"amqp://newsfeed:newsfeed@localhost:5672/{MQ_VHOST}"

import asyncpg
import httpx
import pytest
import pytest_asyncio
from redis.exceptions import RedisError
from sqlalchemy import text

from app.core import rabbitmq_client
from app.core.config import settings
from app.core.database import async_session, engine
from app.core.redis_client import redis_client
from app.main import create_app
from app.models import User
from app.services import message_queue

if not settings.database_url.endswith(f"/{TEST_DB}"):
    raise RuntimeError(f"app settings were loaded before the integration conftest: {settings.database_url}")


def _fail(service: str, error: Exception):
    pytest.fail(f"{service}에 접속할 수 없다. `docker compose up -d`로 컨테이너를 먼저 띄워라: {error!r}")


async def _admin_execute(sql: str) -> None:
    try:
        conn = await asyncpg.connect(f"{PG_URL}/newsfeed")
    except (OSError, asyncpg.PostgresError) as error:
        _fail("PostgreSQL", error)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def database():
    await _admin_execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    await _admin_execute(f"CREATE DATABASE {TEST_DB}")
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, env=os.environ)
    yield
    await engine.dispose()
    await _admin_execute(f"DROP DATABASE {TEST_DB} WITH (FORCE)")


@pytest.fixture(scope="session")
def mq_vhost():
    auth = ("newsfeed", "newsfeed")
    try:
        httpx.put(f"{MQ_ADMIN_URL}/vhosts/{MQ_VHOST}", auth=auth).raise_for_status()
        httpx.put(
            f"{MQ_ADMIN_URL}/permissions/{MQ_VHOST}/newsfeed",
            auth=auth,
            json={"configure": ".*", "write": ".*", "read": ".*"},
        ).raise_for_status()
    except httpx.HTTPError as error:
        _fail("RabbitMQ", error)
    yield
    httpx.delete(f"{MQ_ADMIN_URL}/vhosts/{MQ_VHOST}", auth=auth)


@pytest_asyncio.fixture(loop_scope="session")
async def channel(mq_vhost):
    try:
        connection = await rabbitmq_client.connect()
    except OSError as error:
        _fail("RabbitMQ", error)
    channel = await rabbitmq_client.open_channel(connection)
    await message_queue.declare_queues(channel)
    for name in (message_queue.FANOUT_QUEUE, message_queue.FANOUT_DLQ):
        await (await channel.get_queue(name)).purge()
    yield channel
    await connection.close()


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def clean_state(database, channel):
    try:
        await redis_client.flushdb()
    except RedisError as error:
        _fail("Redis", error)
    async with async_session() as session:
        await session.execute(text("TRUNCATE users, posts, post_media, friendships CASCADE"))
        await session.commit()


@pytest_asyncio.fixture(loop_scope="session")
async def app():
    app = create_app()
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture(loop_scope="session")
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def make_user(push_enabled=True, device_token=None) -> str:
    name = uuid.uuid4().hex[:12]
    async with async_session() as session:
        user = User(
            username=name,
            email=f"{name}@example.com",
            push_enabled=push_enabled,
            device_token=device_token,
        )
        session.add(user)
        await session.commit()
        return str(user.id)
