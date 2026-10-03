from app.core.config import Settings


def test_defaults(monkeypatch):
    for key in ("DATABASE_URL", "REDIS_URL", "RABBITMQ_URL", "JWT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=None)
    assert s.database_url.startswith("postgresql+asyncpg://")
    assert s.redis_url.startswith("redis://")
    assert s.rabbitmq_url.startswith("amqp://")
    assert s.jwt_algorithm == "HS256"


def test_env_override(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/1")
    s = Settings(_env_file=None)
    assert s.jwt_secret == "s3cret"
    assert s.redis_url == "redis://redis:6379/1"
