# Newsfeed_System

FastAPI 기반 뉴스피드 시스템. 스펙은 `.kiro/specs/newsfeed-system/`에 있다.

## 실행 방법

Python 3.12 이상이 필요하다.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
```

PostgreSQL, Redis, RabbitMQ 실행:

```bash
docker compose up -d
```

마이그레이션:

```bash
.venv/bin/alembic upgrade head
```

테스트 (외부 서비스 없이 실행됨):

```bash
.venv/bin/pytest
```
