import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, SmallInteger, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PostMedia(Base):
    __tablename__ = "post_media"
    __table_args__ = (
        CheckConstraint("media_type IN ('text', 'image', 'video')", name="ck_post_media_media_type"),
        CheckConstraint("position BETWEEN 0 AND 9", name="chk_media_count"),
        Index("idx_post_media_post_id", "post_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    media_type: Mapped[str] = mapped_column(String(10))
    url: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(SmallInteger)
