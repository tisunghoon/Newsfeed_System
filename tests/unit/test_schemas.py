import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas import ErrorResponse, FeedResponse, PostResponse


def test_error_response_dump():
    body = ErrorResponse(error="인증 토큰이 없습니다", code="MISSING_TOKEN").model_dump()
    assert body == {"error": "인증 토큰이 없습니다", "code": "MISSING_TOKEN"}


def test_error_response_requires_code():
    with pytest.raises(ValidationError):
        ErrorResponse(error="오류")


def _post(**overrides):
    data = {
        "post_id": uuid.uuid4(),
        "author_id": uuid.uuid4(),
        "body": "hello",
        "media": [{"type": "image", "url": "https://cdn.example.com/a.jpg"}],
        "created_at": datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc),
    }
    return {**data, **overrides}


def test_post_response_serializes_to_iso():
    dumped = PostResponse(**_post()).model_dump(mode="json")
    assert dumped["created_at"] == "2024-01-15T10:30:00Z"
    assert dumped["media"][0]["type"] == "image"


def test_post_response_rejects_unknown_media_type():
    with pytest.raises(ValidationError):
        PostResponse(**_post(media=[{"type": "audio", "url": "x"}]))


def test_feed_response_defaults():
    feed = FeedResponse(posts=[PostResponse(**_post())], has_more=False)
    assert feed.next_cursor is None
    assert len(feed.posts) == 1
