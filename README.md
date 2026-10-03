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

앱 서버 실행:

```bash
.venv/bin/uvicorn app.main:app
```

Fanout_Worker 실행 (별도 터미널):

```bash
.venv/bin/python worker_main.py
```

푸시 알림은 실제로 보내지 않는다. FCM/APNs 연동 전까지 `log_push`가 로그만 남긴다.

테스트 (외부 서비스 없이 실행됨):

```bash
.venv/bin/pytest
```

통합 테스트 (`docker compose up -d` 상태에서 실행):

```bash
.venv/bin/pytest -m integration
```

통합 테스트는 `newsfeed_test` DB를 새로 만들어 마이그레이션을 적용하고 끝나면 지운다. Redis는 15번 DB, RabbitMQ는 `newsfeed_test` vhost를 쓴다(관리 API 15672 포트로 생성). 컨테이너에 접속할 수 없으면 건너뛰지 않고 실패한다.
