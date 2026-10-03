import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class MediaItem(BaseModel):
    type: Literal["text", "image", "video"]
    url: str | None = None


class PostResponse(BaseModel):
    post_id: uuid.UUID
    author_id: uuid.UUID
    body: str | None = None
    media: list[MediaItem] = []
    created_at: datetime


class FeedResponse(BaseModel):
    posts: list[PostResponse]
    next_cursor: str | None = None
    has_more: bool
