import uuid
from collections import defaultdict

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.friendship import Friendship
from app.models.post import Post
from app.models.post_media import PostMedia
from app.schemas.post import MediaItem, PostResponse


class NewsfeedRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_friend_posts(
        self, user_id: str, cursor: tuple[int, str] | None, limit: int
    ) -> list[PostResponse]:
        friend_ids = select(Friendship.friend_id).where(
            Friendship.user_id == uuid.UUID(user_id),
            Friendship.blocked.is_(False),
            Friendship.muted.is_(False),
        )
        created_ms = func.floor(func.extract("epoch", Post.created_at) * 1000)
        query = select(Post).where(Post.author_id.in_(friend_ids), Post.deleted_at.is_(None))
        if cursor is not None:
            ms, post_id = cursor
            query = query.where(
                or_(created_ms < ms, and_(created_ms == ms, Post.id < uuid.UUID(post_id)))
            )
        query = query.order_by(created_ms.desc(), Post.id.desc()).limit(limit)
        return await self._with_media(list(await self.session.scalars(query)))

    async def get_posts(self, post_ids: list[str]) -> list[PostResponse]:
        query = select(Post).where(
            Post.id.in_([uuid.UUID(post_id) for post_id in post_ids]),
            Post.deleted_at.is_(None),
        )
        return await self._with_media(list(await self.session.scalars(query)))

    async def _with_media(self, posts: list[Post]) -> list[PostResponse]:
        media: dict[uuid.UUID, list[MediaItem]] = defaultdict(list)
        if posts:
            query = (
                select(PostMedia)
                .where(PostMedia.post_id.in_([post.id for post in posts]))
                .order_by(PostMedia.position)
            )
            for row in await self.session.scalars(query):
                media[row.post_id].append(MediaItem(type=row.media_type, url=row.url))
        return [
            PostResponse(
                post_id=post.id,
                author_id=post.author_id,
                body=post.body,
                media=media[post.id],
                created_at=post.created_at,
            )
            for post in posts
        ]
