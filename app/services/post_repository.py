import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.models.post import Post
from app.models.post_media import PostMedia
from app.schemas.post import MediaItem


class PostRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, author_id: str, body: str | None, media: list[MediaItem]
    ) -> tuple[uuid.UUID, datetime]:
        try:
            post = Post(author_id=uuid.UUID(author_id), body=body)
            self.session.add(post)
            await self.session.flush()
            self.session.add_all(
                PostMedia(post_id=post.id, media_type=item.type, url=item.url, position=i)
                for i, item in enumerate(media)
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return post.id, post.created_at

    async def exists_active(self, post_id: uuid.UUID, author_id: str) -> bool:
        query = select(Post.id).where(
            Post.id == post_id,
            Post.author_id == uuid.UUID(author_id),
            Post.deleted_at.is_(None),
        )
        return await self.session.scalar(query) is not None

    async def soft_delete(self, post_id: uuid.UUID) -> None:
        try:
            await self.session.execute(
                update(Post).where(Post.id == post_id).values(deleted_at=func.now())
            )
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
