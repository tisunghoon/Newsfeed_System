import asyncio
import contextlib
import json
from types import SimpleNamespace

import httpx
from fakeredis.aioredis import FakeRedis
from fastapi import Depends

from app.core.logging_config import JsonFormatter
from app.main import create_app
from app.middleware import rate_limit
from app.routers import post as post_router
from app.routers.deps import get_redis
from app.services.post_service import PostService
from tests.unit.test_auth_middleware import auth_header
from tests.unit.test_post_service import VALID, FakeRepo

FEED = "/v1/me/feed"


def make_app(publish=None):
    redis = FakeRedis(decode_responses=True)
    app = create_app(redis)
    app.state.mq_channel = object()
    app.dependency_overrides[get_redis] = lambda: redis
    repo = FakeRepo()

    def post_service(real_publish=Depends(post_router.get_publish_fanout)):
        return PostService(repo, redis, publish or real_publish)

    app.dependency_overrides[post_router.get_post_service] = post_service
    return app


def client_for(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def no_publish(event):
    pass


async def test_routers_are_registered():
    paths = create_app(FakeRedis()).openapi()["paths"]
    assert {path: set(ops) for path, ops in paths.items()} == {
        "/v1/me/feed": {"get", "post"},
        "/v1/me/feed/{post_id}": {"delete"},
        "/v1/me/friends/{target_user_id}": {"post", "delete"},
    }


async def test_missing_token_returns_401():
    async with client_for(make_app()) as client:
        for method, path in [("GET", FEED), ("POST", FEED), ("POST", "/v1/me/friends/x")]:
            res = await client.request(method, path)
            assert res.status_code == 401
            assert res.json()["code"] == "MISSING_TOKEN"


async def test_invalid_input_returns_400():
    async with client_for(make_app(no_publish)) as client:
        res = await client.post(FEED, json={**VALID, "media": []}, headers=auth_header())
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION_ERROR"


async def test_rate_limit_returns_429(monkeypatch):
    monkeypatch.setattr(rate_limit, "time", SimpleNamespace(time=lambda: 1_700_000_000.0))
    async with client_for(make_app(no_publish)) as client:
        statuses = [(await client.post(FEED, json=VALID, headers=auth_header())).status_code for _ in range(101)]
        res = await client.post(FEED, json=VALID, headers=auth_header())
    assert statuses[:100] == [201] * 100
    assert statuses[100] == 429
    assert res.json() == {"retry_after": 60, "code": "RATE_LIMIT_EXCEEDED"}


async def test_unhandled_error_returns_500_and_json_log(caplog):
    app = make_app()

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom")

    async with client_for(app) as client:
        res = await client.get("/boom", headers=auth_header("user-9"))
    assert res.status_code == 500
    assert res.json() == {"error": "서버 내부 오류가 발생했습니다", "code": "INTERNAL_ERROR"}

    record = next(r for r in caplog.records if r.name == "web_server")
    entry = json.loads(JsonFormatter().format(record))
    assert entry["level"] == "ERROR"
    assert entry["service"] == "web_server"
    assert entry["user_id"] == "user-9"
    assert entry["error_code"] == "INTERNAL_ERROR"
    assert "GET /boom" in entry["message"]
    assert "RuntimeError: boom" in entry["details"]
    assert "timestamp" in entry


async def test_fanout_runs_after_response(monkeypatch):
    gate = asyncio.Event()
    handled = []

    class GatedFanout:
        def __init__(self, *args):
            pass

        async def handle(self, event):
            await gate.wait()
            handled.append(event)

    monkeypatch.setattr(post_router, "FanoutService", GatedFanout)
    monkeypatch.setattr(post_router, "async_session", contextlib.nullcontext)
    async with client_for(make_app()) as client:
        res = await asyncio.wait_for(client.post(FEED, json=VALID, headers=auth_header()), 1)
    assert res.status_code == 201
    assert handled == []

    gate.set()
    await post_router.wait_fanout_tasks()
    assert [event["post_id"] for event in handled] == [res.json()["post_id"]]


async def test_fanout_task_failure_is_logged(monkeypatch, caplog):
    class BrokenFanout:
        def __init__(self, *args):
            pass

        async def handle(self, event):
            raise RuntimeError("session failed")

    monkeypatch.setattr(post_router, "FanoutService", BrokenFanout)
    monkeypatch.setattr(post_router, "async_session", contextlib.nullcontext)
    async with client_for(make_app()) as client:
        res = await client.post(FEED, json=VALID, headers=auth_header())
    await post_router.wait_fanout_tasks()
    assert res.status_code == 201
    assert "fanout task failed" in caplog.text
