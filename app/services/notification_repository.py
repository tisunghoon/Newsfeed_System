import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class NotificationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_push_targets(self, user_ids: list[str]) -> list[tuple[str, str]]:
        query = select(User.id, User.device_token).where(
            User.id.in_([uuid.UUID(user_id) for user_id in user_ids]),
            User.push_enabled.is_(True),
            User.device_token.is_not(None),
        )
        return [(str(user_id), token) for user_id, token in await self.session.execute(query)]
