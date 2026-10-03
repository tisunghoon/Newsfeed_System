from aio_pika.abc import AbstractChannel
from fastapi import Request
from redis.asyncio import Redis

from app.core.redis_client import redis_client


def get_redis() -> Redis:
    return redis_client


def get_mq_channel(request: Request) -> AbstractChannel:
    return request.app.state.mq_channel
