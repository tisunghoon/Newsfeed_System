import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.services.notification_repository import NotificationRepository

logger = logging.getLogger(__name__)

RETRY_INTERVAL_SECONDS = 30
MAX_RETRIES = 3


class NotificationService:
    def __init__(
        self,
        repo: NotificationRepository,
        send_push: Callable[[str, str], Awaitable[None]],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.repo = repo
        self.send_push = send_push
        self.sleep = sleep

    async def send_notifications(self, post_id: str, friend_ids: list[str]) -> None:
        if not friend_ids:
            return
        try:
            targets = await self.repo.list_push_targets(friend_ids)
        except Exception:
            logger.exception("notification target lookup failed: %s", post_id)
            return
        await asyncio.gather(
            *(self._deliver(post_id, user_id, token) for user_id, token in targets)
        )

    async def _deliver(self, post_id: str, user_id: str, device_token: str) -> None:
        for retry in range(MAX_RETRIES + 1):
            try:
                await self.send_push(device_token, post_id)
                return
            except Exception:
                logger.exception(
                    "push failed: post=%s user=%s (retry %d/%d)", post_id, user_id, retry, MAX_RETRIES
                )
                if retry < MAX_RETRIES:
                    await self.sleep(RETRY_INTERVAL_SECONDS)
        logger.error("push gave up: post=%s user=%s", post_id, user_id)
