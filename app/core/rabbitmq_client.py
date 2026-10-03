import aio_pika
from aio_pika.abc import AbstractChannel, AbstractRobustConnection

from app.core.config import settings


async def connect() -> AbstractRobustConnection:
    return await aio_pika.connect_robust(settings.rabbitmq_url)


async def open_channel(connection: AbstractRobustConnection) -> AbstractChannel:
    return await connection.channel()
