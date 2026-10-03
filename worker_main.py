import asyncio
import logging
import signal

from app.core import rabbitmq_client
from app.core.database import async_session, engine
from app.core.logging_config import configure_logging
from app.core.redis_client import redis_client
from app.services import message_queue
from app.services.notification_repository import NotificationRepository
from app.services.notification_service import NotificationService
from app.workers.fanout_worker import FanoutWorker, run_worker

logger = logging.getLogger("fanout_worker")

PREFETCH_COUNT = 10


async def log_push(device_token: str, post_id: str) -> None:
    logger.info("dev push, not sent: post=%s token=%s...", post_id, device_token[:8])


async def send_notifications(post_id: str, friend_ids: list[str]) -> None:
    async with async_session() as session:
        service = NotificationService(NotificationRepository(session), log_push)
        await service.send_notifications(post_id, friend_ids)


async def main() -> None:
    connection = await rabbitmq_client.connect()
    channel = await rabbitmq_client.open_channel(connection)
    await channel.set_qos(prefetch_count=PREFETCH_COUNT)
    await message_queue.declare_queues(channel)

    task = asyncio.create_task(run_worker(channel, FanoutWorker(redis_client, send_notifications)))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        logger.info("fanout worker stopped")
    finally:
        await connection.close()
        await redis_client.aclose()
        await engine.dispose()


if __name__ == "__main__":
    configure_logging()
    asyncio.run(main())
