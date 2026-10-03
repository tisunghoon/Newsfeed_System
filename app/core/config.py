from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://newsfeed:newsfeed@localhost:5432/newsfeed"
    redis_url: str = "redis://localhost:6379/0"
    rabbitmq_url: str = "amqp://newsfeed:newsfeed@localhost:5672/"
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"


settings = Settings()
