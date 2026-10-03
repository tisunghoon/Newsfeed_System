import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.friendship import Friendship
from app.models.user import User


class SocialGraphRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def user_exists(self, user_id: str) -> bool:
        query = select(User.id).where(User.id == uuid.UUID(user_id))
        return await self.session.scalar(query) is not None

    async def count_friends(self, user_id: str) -> int:
        query = select(func.count()).select_from(Friendship).where(
            Friendship.user_id == uuid.UUID(user_id)
        )
        return await self.session.scalar(query)

    async def are_friends(self, user_id: str, friend_id: str) -> bool:
        query = select(Friendship.user_id).where(
            Friendship.user_id == uuid.UUID(user_id),
            Friendship.friend_id == uuid.UUID(friend_id),
        )
        return await self.session.scalar(query) is not None

    async def list_friend_ids(self, user_id: str) -> list[str]:
        query = select(Friendship.friend_id).where(Friendship.user_id == uuid.UUID(user_id))
        return [str(friend_id) for friend_id in await self.session.scalars(query)]

    async def list_excluding_friend_ids(self, author_id: str) -> list[str]:
        query = select(Friendship.user_id).where(
            Friendship.friend_id == uuid.UUID(author_id),
            (Friendship.blocked.is_(True)) | (Friendship.muted.is_(True)),
        )
        return [str(user_id) for user_id in await self.session.scalars(query)]

    async def add_friendship(self, user_id: str, friend_id: str) -> None:
        a, b = uuid.UUID(user_id), uuid.UUID(friend_id)
        try:
            self.session.add_all(
                [Friendship(user_id=a, friend_id=b), Friendship(user_id=b, friend_id=a)]
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

    async def remove_friendship(self, user_id: str, friend_id: str) -> None:
        a, b = uuid.UUID(user_id), uuid.UUID(friend_id)
        try:
            await self.session.execute(
                delete(Friendship).where(
                    ((Friendship.user_id == a) & (Friendship.friend_id == b))
                    | ((Friendship.user_id == b) & (Friendship.friend_id == a))
                )
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
