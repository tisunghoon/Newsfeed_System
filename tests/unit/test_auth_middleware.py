from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from fastapi import FastAPI, Request

from app.core.config import settings
from app.middleware.auth import AuthMiddleware


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
