import json

import aio_pika
from aio_pika.abc import AbstractChannel

FANOUT_QUEUE = "newsfeed.fanout"
FANOUT_DLQ = "newsfeed.fanout.dlq"


async def declare_queues(channel: AbstractChannel) -> None:
    await channel.declare_queue(FANOUT_DLQ, durable=True)
    await channel.declare_queue(
        FANOUT_QUEUE,
        durable=True,
        arguments={"x-dead-letter-exchange": "", "x-dead-letter-routing-key": FANOUT_DLQ},
    )


async def _publish(channel: AbstractChannel, queue: str, message: dict) -> None:
    body = json.dumps(message).encode()
    await channel.default_exchange.publish(
        aio_pika.Message(body, delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
        routing_key=queue,
    )


async def publish_message(channel: AbstractChannel, message: dict) -> None:
    await _publish(channel, FANOUT_QUEUE, message)


async def publish_dead_letter(channel: AbstractChannel, message: dict) -> None:
    await _publish(channel, FANOUT_DLQ, message)
