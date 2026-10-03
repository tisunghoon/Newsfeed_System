import asyncio
from datetime import datetime, timedelta, timezone

import jwt
from fakeredis.aioredis import FakeRedis
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.config import settings as app_settings
from tests.unit.test_auth_middleware import (
    FEED,
    client_for,
    make_app,
    make_feed_app,
    make_token,
)

SECRETS = st.text(min_size=1, max_size=40).filter(lambda s: s != app_settings.jwt_secret)


async def _get_ping(token: str):
    async with client_for(make_app()) as client:
        return await client.get("/ping", headers={"Authorization": f"Bearer {token}"})


async def _post_feed_n_times(user_id: str, n: int) -> list[int]:
    app = make_feed_app(FakeRedis(decode_responses=True))
    headers = {"Authorization": f"Bearer {make_token(sub=user_id)}"}
    async with client_for(app) as client:
        return [(await client.post(FEED, headers=headers)).status_code for _ in range(n)]


# Feature: newsfeed-system, Property 1: 유효하지 않은 JWT는 항상 401을 반환한다
@given(secret=SECRETS, user_id=st.uuids())
@settings(max_examples=100, deadline=None)
def test_wrong_signature_returns_401(secret, user_id):
    res = asyncio.run(_get_ping(make_token(secret=secret, sub=str(user_id))))
    assert res.status_code == 401
    assert res.json()["code"] == "INVALID_TOKEN"


# Feature: newsfeed-system, Property 1: 유효하지 않은 JWT는 항상 401을 반환한다
@given(token=st.text(alphabet=st.characters(min_codepoint=33, max_codepoint=126), min_size=1, max_size=200))
@settings(max_examples=100, deadline=None)
def test_garbage_token_returns_401(token):
    res = asyncio.run(_get_ping(token))
    assert res.status_code == 401
    assert res.json()["code"] == "INVALID_TOKEN"


# Feature: newsfeed-system, Property 1: 유효하지 않은 JWT는 항상 401을 반환한다
@given(user_id=st.uuids())
@settings(max_examples=100, deadline=None)
def test_unsigned_token_returns_401(user_id):
    payload = {"sub": str(user_id), "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}
    res = asyncio.run(_get_ping(jwt.encode(payload, None, algorithm="none")))
    assert res.status_code == 401
    assert res.json()["code"] == "INVALID_TOKEN"


# Feature: newsfeed-system, Property 2: 만료된 JWT는 항상 401을 반환한다
@given(elapsed=st.integers(min_value=1, max_value=10 * 365 * 24 * 3600), user_id=st.uuids())
@settings(max_examples=100, deadline=None)
def test_expired_token_returns_401(elapsed, user_id):
    token = make_token(sub=str(user_id), exp_delta=-timedelta(seconds=elapsed))
    res = asyncio.run(_get_ping(token))
    assert res.status_code == 401
    assert res.json()["code"] == "EXPIRED_TOKEN"


# Feature: newsfeed-system, Property 3: 처리율 제한은 100회 기준을 정확히 적용한다
@given(user_id=st.uuids(), request_count=st.integers(min_value=1, max_value=150))
@settings(max_examples=100, deadline=None)
def test_rate_limit_boundary(user_id, request_count):
    statuses = asyncio.run(_post_feed_n_times(str(user_id), request_count))
    assert statuses.count(200) == min(request_count, 100)
    assert statuses.count(429) == max(0, request_count - 100)
