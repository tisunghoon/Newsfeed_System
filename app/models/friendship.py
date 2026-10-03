import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Friendship(Base):
    __tablename__ = "friendships"
    __table_args__ = (
        Index("idx_friendships_user_id", "user_id"),
        Index("idx_friendships_friend_id", "friend_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    friend_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    blocked: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    muted: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
