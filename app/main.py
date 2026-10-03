from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from app.core import rabbitmq_client
from app.core.database import engine
from app.core.errors import register_error_handlers
from app.core.logging_config import configure_logging
from app.core.redis_client import redis_client
from app.middleware.auth import AuthMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.routers import newsfeed, post, social
from app.services import message_queue


@asynccontextmanager
async def lifespan(app: FastAPI):
    connection = await rabbitmq_client.connect()
    channel = await rabbitmq_client.open_channel(connection)
    await message_queue.declare_queues(channel)
    app.state.mq_channel = channel
    yield
    await post.wait_fanout_tasks()
    await connection.close()
    await redis_client.aclose()
    await engine.dispose()


def create_app(redis: Redis = redis_client) -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.add_middleware(RateLimitMiddleware, redis=redis)
    app.add_middleware(AuthMiddleware)
    app.include_router(post.router)
    app.include_router(newsfeed.router)
    app.include_router(social.router)
    register_error_handlers(app)
    return app


configure_logging()
app = create_app()
