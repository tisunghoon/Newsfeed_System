import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.services.social_graph_repository import SocialGraphRepository
from app.services.social_graph_service import SocialGraphService

logger = logging.getLogger(__name__)

FRIEND_LOOKUP_TIMEOUT = 2.0
PUSH_FOLLOWER_LIMIT = 10_000
PUBLISH_ATTEMPTS = 3


class FanoutService:
    def __init__(
        self,
        social_graph: SocialGraphService,
        repo: SocialGraphRepository,
        publish_message: Callable[[dict], Awaitable[None]],
        publish_dead_letter: Callable[[dict], Awaitable[None]],
    ):
        self.social_graph = social_graph
        self.repo = repo
        self.publish_message = publish_message
        self.publish_dead_letter = publish_dead_letter

    async def handle(self, event: dict) -> None:
        action = event["action"]
        author_id = event["author_id"]
        try:
            friend_ids = await asyncio.wait_for(
                self.social_graph.get_friend_ids(author_id), FRIEND_LOOKUP_TIMEOUT
            )
            if len(friend_ids) > PUSH_FOLLOWER_LIMIT:
                return
            if action == "insert":
                excluded = set(await self.repo.list_excluding_friend_ids(author_id))
                friend_ids = [friend_id for friend_id in friend_ids if friend_id not in excluded]
        except Exception:
            logger.exception("fanout aborted, friend lookup failed: %s", event)
            return
        if not friend_ids:
            return

        message = {"post_id": event["post_id"], "friend_ids": friend_ids, "action": action}
        if action == "insert":
            message["created_at"] = event["created_at"]
        await self._send(message)

    async def _send(self, message: dict) -> None:
        for attempt in range(1, PUBLISH_ATTEMPTS + 1):
            try:
                await self.publish_message(message)
                return
            except Exception:
                logger.exception("fanout publish failed (%d/%d)", attempt, PUBLISH_ATTEMPTS)
        try:
            await self.publish_dead_letter(message)
        except Exception:
            logger.exception("dead letter publish failed: %s", message["post_id"])
