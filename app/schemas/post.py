import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class MediaItem(BaseModel):
    type: Literal["text", "image", "video"]
    url: str | None = None


class PostCreateRequest(BaseModel):
    body: str | None = Field(default=None, max_length=2000)
    media: list[MediaItem] = Field(min_length=1, max_length=10)


class PostCreateResponse(BaseModel):
    post_id: uuid.UUID
    created_at: datetime


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
