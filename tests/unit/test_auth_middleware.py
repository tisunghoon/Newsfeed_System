from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from fakeredis.aioredis import FakeRedis
from fastapi import FastAPI, Request
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.config import settings
from app.middleware.auth import AuthMiddleware
from app.middleware.rate_limit import RateLimitMiddleware


def make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/ping")
    async def ping(request: Request):
        return {"user_id": request.state.user_id}

    return app


def make_token(secret=None, exp_delta=timedelta(minutes=5), **claims) -> str:
    payload = {"sub": "user-1", "exp": datetime.now(timezone.utc) + exp_delta, **claims}
    return jwt.encode(payload, secret or settings.jwt_secret, algorithm="HS256")


async def get(headers=None) -> httpx.Response:
    transport = httpx.ASGITransport(app=make_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/ping", headers=headers)


async def test_missing_header():
    res = await get()
    assert res.status_code == 401
    assert res.json() == {"error": "인증 토큰이 없습니다", "code": "MISSING_TOKEN"}


async def test_bad_signature():
    res = await get({"Authorization": f"Bearer {make_token(secret='wrong-secret')}"})
    assert res.status_code == 401
    assert res.json() == {"error": "유효하지 않은 토큰입니다", "code": "INVALID_TOKEN"}


async def test_not_a_bearer_header():
    res = await get({"Authorization": f"Basic {make_token()}"})
    assert res.status_code == 401
    assert res.json()["code"] == "INVALID_TOKEN"


async def test_malformed_token():
    res = await get({"Authorization": "Bearer not.a.jwt"})
    assert res.status_code == 401
    assert res.json()["code"] == "INVALID_TOKEN"


async def test_expired_token():
    res = await get({"Authorization": f"Bearer {make_token(exp_delta=timedelta(seconds=-10))}"})
    assert res.status_code == 401
    assert res.json() == {"error": "토큰이 만료되었습니다", "code": "EXPIRED_TOKEN"}


async def test_valid_token_sets_user_id():
    res = await get({"Authorization": f"Bearer {make_token(sub='abc')}"})
    assert res.status_code == 200
    assert res.json() == {"user_id": "abc"}


FEED = "/v1/me/feed"


def make_feed_app(redis, now_ms=lambda: 1_700_000_000_000) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, redis=redis, now_ms=now_ms)
    app.add_middleware(AuthMiddleware)

    @app.post(FEED)
    async def create_post():
        return {"ok": True}

    @app.get(FEED)
    async def read_feed():
        return {"ok": True}

    return app


def auth_header(user_id="user-1") -> dict:
    return {"Authorization": f"Bearer {make_token(sub=user_id)}"}


def client_for(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class BrokenRedis:
    async def zremrangebyscore(self, *args, **kwargs):
        raise RedisConnectionError("redis down")


async def test_rate_limit_allows_request():
    async with client_for(make_feed_app(FakeRedis(decode_responses=True))) as client:
        res = await client.post(FEED, headers=auth_header())
    assert res.status_code == 200


async def test_rate_limit_boundary_at_100():
    async with client_for(make_feed_app(FakeRedis(decode_responses=True))) as client:
        statuses = [(await client.post(FEED, headers=auth_header())).status_code for _ in range(101)]
        res = await client.post(FEED, headers=auth_header())
    assert statuses[:100] == [200] * 100
    assert statuses[100] == 429
    assert res.status_code == 429


async def test_rate_limit_response_body_and_header():
    now = 1_700_000_000_000
    async with client_for(make_feed_app(FakeRedis(decode_responses=True), lambda: now)) as client:
        for _ in range(100):
            await client.post(FEED, headers=auth_header())
        res = await client.post(FEED, headers=auth_header())
    assert res.json() == {"retry_after": 60, "code": "RATE_LIMIT_EXCEEDED"}
    assert res.headers["Retry-After"] == "60"


async def test_rate_limit_window_slides():
    clock = {"now": 1_700_000_000_000}
    async with client_for(make_feed_app(FakeRedis(decode_responses=True), lambda: clock["now"])) as client:
        for _ in range(100):
            await client.post(FEED, headers=auth_header())
        clock["now"] += 61_000
        res = await client.post(FEED, headers=auth_header())
    assert res.status_code == 200


async def test_rate_limit_is_per_user():
    async with client_for(make_feed_app(FakeRedis(decode_responses=True))) as client:
        for _ in range(100):
            await client.post(FEED, headers=auth_header("user-1"))
        res = await client.post(FEED, headers=auth_header("user-2"))
    assert res.status_code == 200


async def test_rate_limit_ignores_other_endpoints():
    async with client_for(make_feed_app(FakeRedis(decode_responses=True))) as client:
        for _ in range(100):
            await client.post(FEED, headers=auth_header())
        res = await client.get(FEED, headers=auth_header())
    assert res.status_code == 200


async def test_redis_failure_returns_503(caplog):
    async with client_for(make_feed_app(BrokenRedis())) as client:
        res = await client.post(FEED, headers=auth_header())
    assert res.status_code == 503
    assert res.json()["code"] == "SERVICE_UNAVAILABLE"
    assert "rate limit redis error" in caplog.text
