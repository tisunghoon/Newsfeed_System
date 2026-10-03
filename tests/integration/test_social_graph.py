import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.database import async_session
from app.core.redis_client import redis_client
from app.services.notification_repository import NotificationRepository
from app.services.social_graph_service import FRIEND_LIMIT
from tests.integration.conftest import make_user
from tests.integration.test_fanout_timing import add_bulk_friends
from tests.integration.test_newsfeed_read_flow import BASE, insert_post
from tests.unit.test_auth_middleware import auth_header

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def friendship_rows(a, b):
    async with async_session() as session:
        return await session.scalar(
            text(
                "SELECT count(*) FROM friendships "
                "WHERE (user_id = :a AND friend_id = :b) OR (user_id = :b AND friend_id = :a)"
            ),
            {"a": a, "b": b},
        )


async def test_add_and_remove_friend_writes_both_directions(client):
    user = await make_user()
    friend = await make_user()
    path = f"/v1/me/friends/{friend}"

    assert (await client.post(path, headers=auth_header(user))).status_code == 201
    assert await friendship_rows(user, friend) == 2

    res = await client.post(path, headers=auth_header(user))
    assert res.status_code == 409
    assert res.json()["code"] == "ALREADY_FRIENDS"
    res = await client.post(f"/v1/me/friends/{user}", headers=auth_header(friend))
    assert res.json()["code"] == "ALREADY_FRIENDS"

    assert (await client.delete(path, headers=auth_header(user))).status_code == 204
    assert await friendship_rows(user, friend) == 0
    res = await client.delete(path, headers=auth_header(user))
    assert res.status_code == 404


async def test_add_unknown_user_returns_404(client):
    user = await make_user()
    res = await client.post(f"/v1/me/friends/{uuid.uuid4()}", headers=auth_header(user))
    assert res.status_code == 404
    assert res.json()["code"] == "NOT_FOUND"


async def test_friend_limit_returns_409(client):
    user = await make_user()
    target = await make_user()
    await add_bulk_friends(user, FRIEND_LIMIT)

    res = await client.post(f"/v1/me/friends/{target}", headers=auth_header(user))
    assert res.status_code == 409
    assert res.json()["code"] == "FRIEND_LIMIT_EXCEEDED"
    assert await friendship_rows(user, target) == 0


async def test_friend_change_invalidates_friend_cache(client):
    user = await make_user()
    friend = await make_user()
    await redis_client.sadd(f"friends:{user}", "stale")
    await redis_client.set(f"friends:count:{friend}", 7)

    await client.post(f"/v1/me/friends/{friend}", headers=auth_header(user))
    assert not await redis_client.exists(f"friends:{user}", f"friends:count:{friend}")


async def test_push_targets_skip_disabled_and_tokenless_users():
    enabled = await make_user(device_token="token-enabled")
    disabled = await make_user(push_enabled=False, device_token="token-disabled")
    tokenless = await make_user()

    async with async_session() as session:
        targets = await NotificationRepository(session).list_push_targets([enabled, disabled, tokenless])
    assert targets == [(enabled, "token-enabled")]


async def test_migration_constraints_are_enforced():
    author = await make_user()
    post_id = await insert_post(author, BASE)
    async with async_session() as session:
        assert await session.scalar(text("SELECT version_num FROM alembic_version")) == "0001"
        with pytest.raises(IntegrityError, match="chk_media_count"):
            await session.execute(
                text("INSERT INTO post_media (post_id, media_type, position) VALUES (:p, 'text', 10)"),
                {"p": post_id},
            )
        await session.rollback()

        await session.execute(text("DELETE FROM users WHERE id = :u"), {"u": author})
        await session.commit()
        assert await session.scalar(text("SELECT count(*) FROM post_media")) == 0
