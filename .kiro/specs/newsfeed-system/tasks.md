# Implementation Plan: Newsfeed System

## Overview

Python/FastAPI 기반 뉴스피드 시스템 구현 계획이다. 프로젝트 구조 설정 → 인증·처리율 제한 미들웨어 → Post_Service → Fanout_Service & Worker → Newsfeed_Service → Notification_Service → Social_Graph 순서로 점진적으로 구현하며, 각 단계에서 단위 테스트와 속성 기반 테스트(Hypothesis)를 함께 작성한다.

---

## Tasks

- [ ] 1. 프로젝트 구조 및 공통 인프라 설정
  - FastAPI 프로젝트 디렉토리 구조 생성 (`app/`, `tests/unit/`, `tests/property/`, `tests/integration/`)
  - `pyproject.toml` (또는 `requirements.txt`) 작성: fastapi, uvicorn, sqlalchemy, asyncpg, redis, aio-pika, pyjwt, hypothesis, pytest, pytest-asyncio 등 의존성 고정 버전으로 명시
  - `docker-compose.yml` 작성: PostgreSQL, Redis, RabbitMQ 컨테이너 정의
  - `app/core/config.py` 작성: 환경 변수(DATABASE_URL, REDIS_URL, RABBITMQ_URL, JWT_SECRET 등) Pydantic Settings로 관리
  - `app/core/database.py` 작성: SQLAlchemy 비동기 엔진 및 세션 팩토리 설정
  - `app/core/redis_client.py` 작성: Redis 비동기 클라이언트 초기화
  - `app/core/rabbitmq_client.py` 작성: aio-pika 연결 및 채널 초기화
  - `app/models/` 디렉토리에 SQLAlchemy ORM 모델 작성 (`User`, `Post`, `PostMedia`, `Friendship`)
  - Alembic 초기화 및 초기 마이그레이션 스크립트 작성 (users, posts, post_media, friendships 테이블 + 인덱스)
  - `app/schemas/` 디렉토리에 공통 Pydantic 응답 스키마 작성 (`ErrorResponse`, `PostResponse`, `FeedResponse`)
  - _Requirements: 모든 요구사항의 기반_

- [ ] 2. JWT 인증 미들웨어 구현
  - [ ] 2.1 JWT 검증 미들웨어 구현
    - `app/middleware/auth.py` 작성: `Authorization: Bearer <token>` 헤더 추출 → PyJWT HS256 서명 검증 → `user_id` 추출 후 request state에 주입
    - 헤더 없음 → `{"error": "인증 토큰이 없습니다", "code": "MISSING_TOKEN"}` + HTTP 401 반환
    - 서명 검증 실패 → `{"error": "유효하지 않은 토큰입니다", "code": "INVALID_TOKEN"}` + HTTP 401 반환
    - 토큰 만료 → `{"error": "토큰이 만료되었습니다", "code": "EXPIRED_TOKEN"}` + HTTP 401 반환
    - _Requirements: 1.1, 1.2, 1.3_
  - [ ]* 2.2 JWT 미들웨어 단위 테스트 작성
    - `tests/unit/test_auth_middleware.py`: 헤더 없음/서명 실패/만료 각 케이스 단위 테스트
    - _Requirements: 1.1, 1.2, 1.3_
  - [ ]* 2.3 JWT 속성 기반 테스트 작성
    - **Property 1: 유효하지 않은 JWT는 항상 401을 반환한다**
    - **Validates: Requirements 1.2**
    - **Property 2: 만료된 JWT는 항상 401을 반환한다**
    - **Validates: Requirements 1.3**
    - `tests/property/test_property_auth.py` 작성

- [ ] 3. 처리율 제한 미들웨어 구현
  - [ ] 3.1 슬라이딩 윈도우 Rate Limiter 구현
    - `app/middleware/rate_limit.py` 작성: Redis Sorted Set 기반 슬라이딩 윈도우 알고리즘 구현
    - Key: `rate_limit:{user_id}:{endpoint}`, 60초 윈도우, POST /v1/me/feed 기준 최대 100회
    - 초과 시 `{"retry_after": N, "code": "RATE_LIMIT_EXCEEDED"}` + HTTP 429 + `Retry-After` 헤더 반환
    - Redis 접근 불가 시 HTTP 503 반환 및 오류 로그 기록 (Fail-Closed)
    - _Requirements: 1.4, 1.5_
  - [ ]* 3.2 Rate Limiter 단위 테스트 작성
    - `tests/unit/test_auth_middleware.py`에 Rate Limiter 케이스 추가: Redis 정상/Redis 불가/경계값(100회) 테스트
    - _Requirements: 1.4, 1.5_
  - [ ]* 3.3 처리율 제한 속성 기반 테스트 작성
    - **Property 3: 처리율 제한은 100회 기준을 정확히 적용한다**
    - **Validates: Requirements 1.4**
    - `tests/property/test_property_auth.py`에 추가

- [ ] 4. Checkpoint — 인증·처리율 제한 검증
  - 모든 테스트가 통과하는지 확인하고, 궁금한 점이 있으면 사용자에게 질문한다.

- [ ] 5. Post_Service 구현
  - [ ] 5.1 Post 생성 엔드포인트 구현
    - `app/routers/post.py` 작성: `POST /v1/me/feed` 라우터 등록
    - `app/services/post_service.py` 작성: 입력 검증 → DB 저장 → Post_Cache 기록 → Fanout 이벤트 발행 순서 구현
    - 입력 검증: `body` ≤ 2,000자, `media` 1~10개, `media[].type` ∈ {"text", "image", "video"} — 실패 시 HTTP 400
    - PostgreSQL 저장 실패 시 HTTP 500 반환, Post_Cache 기록 미수행
    - Post_Cache(Redis Hash) 기록 실패 시 오류 로그 후 HTTP 201 반환 (best-effort)
    - 성공 시 `{"post_id": UUID, "created_at": ISO8601}` + HTTP 201 반환
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_
  - [ ] 5.2 Post 삭제 엔드포인트 구현
    - `DELETE /v1/me/feed/{post_id}` 라우터 추가
    - 삭제 순서: Post_Cache 삭제 → PostgreSQL soft delete (`deleted_at` 설정) → 둘 중 하나 실패 시 롤백 + HTTP 500
    - 성공 시 Fanout_Service에 삭제 이벤트 발행 (비동기)
    - _Requirements: 7.1, 7.2, 7.3_
  - [ ]* 5.3 Post_Service 단위 테스트 작성
    - `tests/unit/test_post_service.py` 작성: 정상 생성/DB 실패/캐시 실패/삭제 롤백 각 케이스 단위 테스트
    - _Requirements: 2.1~2.7, 7.1~7.3_
  - [ ]* 5.4 Post 저장 속성 기반 테스트 작성
    - **Property 4: 유효한 Post 저장은 항상 post_id와 타임스탬프를 포함한 201을 반환한다**
    - **Validates: Requirements 2.1, 2.3**
    - **Property 5: 무효한 Post는 항상 400을 반환한다**
    - **Validates: Requirements 2.2, 2.5, 2.6**
    - **Property 6: DB 저장 실패 시 캐시 상태는 변경되지 않는다**
    - **Validates: Requirements 2.4**
    - **Property 7: 캐시 저장 실패 시에도 DB 저장 성공이면 201을 반환한다**
    - **Validates: Requirements 2.7**
    - `tests/property/test_property_post.py` 작성

- [ ] 6. Checkpoint — Post_Service 검증
  - 모든 테스트가 통과하는지 확인하고, 궁금한 점이 있으면 사용자에게 질문한다.

- [ ] 7. Social_Graph 구현
  - [ ] 7.1 친구 관계 CRUD 엔드포인트 구현
    - `app/routers/social.py` 작성: `POST /v1/me/friends/{target_user_id}`, `DELETE /v1/me/friends/{target_user_id}` 라우터 등록
    - `app/services/social_graph_service.py` 작성
    - 친구 추가: target_user 존재 확인(없으면 HTTP 404) → 현재 친구 수 확인(5,000명 초과 시 HTTP 409 + `FRIEND_LIMIT_EXCEEDED`) → 이미 친구 확인(HTTP 409 + `ALREADY_FRIENDS`) → friendships 테이블에 양방향 관계 저장 → Redis Social_Graph Cache 무효화 → HTTP 201
    - 친구 삭제: 관계 존재 확인(없으면 HTTP 404) → friendships 양방향 삭제 → Redis Cache 무효화 → HTTP 204
    - Redis Social_Graph Cache(Set): Key `friends:{user_id}`, TTL 10분
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7_
  - [ ]* 7.2 Social_Graph 단위 테스트 작성
    - `tests/unit/test_social_graph.py` 작성: 친구 추가/삭제/중복/한도초과/존재하지 않는 사용자 각 케이스 단위 테스트
    - _Requirements: 6.1~6.7_
  - [ ]* 7.3 Social_Graph 속성 기반 테스트 작성
    - **Property 17: 친구 추가는 양방향 관계를 생성하고 라운드트립을 보장한다**
    - **Validates: Requirements 6.1**
    - **Property 18: 친구 삭제는 양방향 관계를 제거하는 라운드트립을 보장한다**
    - **Validates: Requirements 6.2**
    - **Property 19: 친구 추가 중복 요청은 항상 409를 반환한다 (멱등성)**
    - **Validates: Requirements 6.4**
    - **Property 20: 친구 수는 5,000명 상한을 정확히 준수한다**
    - **Validates: Requirements 6.3, 6.6**
    - `tests/property/test_property_social_graph.py` 작성

- [ ] 8. Fanout_Service 구현
  - [ ] 8.1 Fanout_Service 핵심 로직 구현
    - `app/services/fanout_service.py` 작성
    - Social_Graph에서 친구 ID 목록 조회 (2,000ms 타임아웃 적용)
    - `blocked=True` 또는 `muted=True` 친구 필터링
    - 팔로워 수 기준 Push/Pull 분기: ≤ 10,000명 → Message_Queue에 `{post_id, friend_ids}` 전송; > 10,000명 → Push 생략(Pull 모델)
    - MQ 전송 실패 시 즉시 재시도 최대 3회 → 3회 모두 실패 시 Dead_Letter_Queue 기록
    - Social_Graph 조회 실패 시 오류 로그 후 팬아웃 중단
    - _Requirements: 3.1, 3.2, 3.3, 3.5, 3.7, 3.8_
  - [ ] 8.2 Post 삭제 팬아웃 로직 구현
    - `fanout_service.py`에 삭제 이벤트 처리 추가: 영향받는 사용자의 Newsfeed_Cache에서 Post ID 제거 메시지 발행 (30초 이내 처리 목표)
    - 실패 시 최대 3회 재시도 → 실패 시 DLQ 기록
    - _Requirements: 7.4, 7.5_
  - [ ]* 8.3 Fanout_Service 단위 테스트 작성
    - `tests/unit/test_fanout_service.py` 작성: 친구 필터링/셀러브리티 분기/재시도/DLQ 각 케이스 단위 테스트
    - _Requirements: 3.1~3.9, 7.4~7.5_
  - [ ]* 8.4 팬아웃 속성 기반 테스트 작성
    - **Property 8: 팬아웃 필터는 차단/알림 비활성화 친구를 항상 제외한다**
    - **Validates: Requirements 3.2**
    - **Property 9: 셀러브리티 Post는 Newsfeed_Cache에 즉시 반영되지 않는다**
    - **Validates: Requirements 3.5**
    - **Property 11: 재시도 로직은 최대 3회를 정확히 준수한다**
    - **Validates: Requirements 3.8, 3.9**
    - `tests/property/test_property_fanout.py` 작성

- [ ] 9. Fanout_Worker 구현
  - [ ] 9.1 RabbitMQ Consumer 및 Newsfeed_Cache 갱신 구현
    - `app/workers/fanout_worker.py` 작성: aio-pika 기반 RabbitMQ 메시지 소비자
    - 메시지 형식: `{post_id: str, friend_ids: [str], action: "insert"|"delete"}`
    - `action=insert`: 각 친구의 `newsfeed:{user_id}` Sorted Set에 `ZADD score=created_at_ms member=post_id` 실행
    - 삽입 후 캐시 크기 > 500 시 `ZREMRANGEBYRANK newsfeed:{user_id} 0 0`으로 가장 오래된 항목 제거
    - `action=delete`: `ZREM newsfeed:{user_id} post_id` 실행
    - 캐시 삽입/삭제 실패 시 최대 3회 재시도 → 3회 모두 실패 시 오류 로그 (메시지 ACK하지 않고 MQ에 반환)
    - Newsfeed_Cache TTL: 48시간(172,800초) 설정
    - _Requirements: 3.4, 3.6, 3.9, 7.4_
  - [ ] 9.2 Fanout_Worker → Notification_Service 트리거 구현
    - `action=insert` 완료 후 `Notification_Service.send_notifications()` 비동기 호출
    - _Requirements: 5.1_
  - [ ]* 9.3 Fanout_Worker 단위 테스트 작성
    - `tests/unit/test_fanout_service.py`에 Worker 케이스 추가: 캐시 삽입/삭제/500개 상한 유지/재시도/메시지 ACK 처리 단위 테스트
    - _Requirements: 3.4, 3.6, 3.9_
  - [ ]* 9.4 Newsfeed_Cache 크기 속성 기반 테스트 작성
    - **Property 10: Newsfeed_Cache 크기는 항상 500개 이하로 유지된다**
    - **Validates: Requirements 3.6**
    - `tests/property/test_property_fanout.py`에 추가

- [ ] 10. Checkpoint — 팬아웃 파이프라인 검증
  - 모든 테스트가 통과하는지 확인하고, 궁금한 점이 있으면 사용자에게 질문한다.

- [ ] 11. Newsfeed_Service 구현
  - [ ] 11.1 피드 조회 엔드포인트 구현
    - `app/routers/newsfeed.py` 작성: `GET /v1/me/feed` 라우터 등록
    - `app/services/newsfeed_service.py` 작성
    - cursor 검증: 유효하지 않으면 HTTP 400 + `VALIDATION_ERROR` 반환 (base64 디코딩 실패 또는 구조 오류)
    - Newsfeed_Cache에서 `ZREVRANGE newsfeed:{user_id}` 조회 (cursor 기준 이후 항목, 최대 20개)
    - 캐시 MISS 시 PostgreSQL에서 친구들의 최신 Post 직접 조회 (Fallback)
    - Post_Cache Multi-Get(`HGETALL post:{post_id}`)으로 상세 데이터 조회, 캐시 없는 Post는 DB 조회 후 Post_Cache Back-fill
    - `created_at` 기준 내림차순 정렬
    - 다음 페이지 cursor: 마지막 post_id 기반 base64 인코딩; 다음 페이지 없으면 `null`
    - HTTP 200 `{"posts": [...], "next_cursor": str|null, "has_more": bool}` 반환
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8_
  - [ ]* 11.2 Newsfeed_Service 단위 테스트 작성
    - `tests/unit/test_newsfeed_service.py` 작성: 캐시 HIT/MISS 폴백/정렬/페이지네이션/잘못된 cursor/DB 실패 각 케이스 단위 테스트
    - _Requirements: 4.1~4.8_
  - [ ]* 11.3 피드 조회 속성 기반 테스트 작성
    - **Property 12: 피드는 항상 최신순(created_at 내림차순)으로 정렬된다**
    - **Validates: Requirements 4.3**
    - **Property 13: 피드 페이지 크기는 항상 20개 이하이며 cursor는 올바르게 동작한다**
    - **Validates: Requirements 4.4**
    - **Property 14: 유효하지 않은 cursor는 항상 400을 반환한다**
    - **Validates: Requirements 4.7**
    - **Property 15: 캐시 미스 시 DB 폴백은 올바른 피드를 반환한다**
    - **Validates: Requirements 4.6**
    - `tests/property/test_property_newsfeed.py` 작성

- [ ] 12. Notification_Service 구현
  - [ ] 12.1 알림 전송 서비스 구현
    - `app/services/notification_service.py` 작성
    - 수신자별 `push_enabled` 설정 조회 (`users.push_enabled`, `users.device_token`)
    - `push_enabled=True`인 사용자에게만 디바이스 푸시 전송 (외부 FCM/APNs 클라이언트 인터페이스 추상화)
    - 전송 실패 시 30초 간격, 최대 3회 재시도
    - 3회 모두 실패 시 오류 로그 기록 후 작업 종료
    - _Requirements: 5.1, 5.2, 5.3, 5.4_
  - [ ]* 12.2 Notification_Service 단위 테스트 작성
    - `tests/unit/test_notification_service.py` 작성: 활성화/비활성화 필터/재시도/작업 종료 각 케이스 단위 테스트
    - _Requirements: 5.1~5.4_
  - [ ]* 12.3 알림 속성 기반 테스트 작성
    - **Property 16: 알림은 push_enabled 설정에 따라 정확히 발송된다**
    - **Validates: Requirements 5.2, 5.4**
    - `tests/property/test_property_notification.py` 작성

- [ ] 13. 캐시 일관성 및 TTL 설정 완성
  - [ ] 13.1 캐시 TTL 및 Post 삭제 팬아웃 연동 완성
    - Post_Cache 항목 TTL 24시간(86,400초) `EXPIRE` 설정 확인 및 누락 부분 보완
    - Newsfeed_Cache TTL 48시간(172,800초) 설정 확인
    - Fanout_Worker의 삭제 이벤트 처리(9.1)와 Post_Service 삭제 이벤트 발행(5.2) 연동 확인
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_
  - [ ]* 13.2 캐시 일관성 속성 기반 테스트 작성
    - **Property 21: Post 삭제 후 캐시와 DB에서 모두 제거된다**
    - **Validates: Requirements 7.1, 7.2**
    - **Property 22: 부분 삭제 실패 시 캐시와 DB 상태는 삭제 이전으로 유지된다**
    - **Validates: Requirements 7.3**
    - `tests/property/test_property_social_graph.py`에 추가

- [ ] 14. FastAPI 앱 통합 및 라우터 배선
  - [ ] 14.1 FastAPI 앱 엔트리포인트 작성
    - `app/main.py` 작성: FastAPI 앱 인스턴스 생성, 미들웨어 등록 (JWT 인증, Rate Limit), 라우터 등록 (post, newsfeed, social)
    - 앱 시작/종료 시 DB 연결, Redis 연결, RabbitMQ 연결 초기화 및 정리 (`lifespan` 이벤트 핸들러)
    - 전역 오류 핸들러 등록: `RequestValidationError` → HTTP 400, `Exception` → HTTP 500 (구조화된 JSON 로그 포함)
    - _Requirements: 1.1~1.5, 2.1~2.7, 3.1~3.9, 4.1~4.8, 5.1~5.4, 6.1~6.7, 7.1~7.7_
  - [ ] 14.2 Fanout_Worker 백그라운드 프로세스 연동
    - `app/workers/fanout_worker.py`를 별도 프로세스 또는 FastAPI `lifespan` 내 백그라운드 태스크로 기동하는 진입점 작성 (`worker_main.py` 또는 `Makefile` 커맨드)
    - _Requirements: 3.4, 3.9_

- [ ] 15. Checkpoint — 전체 통합 검증
  - 모든 단위 테스트 및 속성 기반 테스트가 통과하는지 확인한다.
  - 궁금한 점이 있으면 사용자에게 질문한다.

- [ ] 16. 통합 테스트 작성
  - [ ]* 16.1 Post 발행 → 팬아웃 전체 흐름 통합 테스트 작성
    - `tests/integration/test_post_publish_flow.py` 작성: Docker Compose 환경에서 Post 저장 후 Newsfeed_Cache 갱신까지 전체 흐름 검증
    - _Requirements: 2.1, 3.1~3.4_
  - [ ]* 16.2 팬아웃 타이밍 통합 테스트 작성
    - `tests/integration/test_fanout_timing.py` 작성: Social_Graph 조회 2,000ms 이내 완료 및 Post 삭제 후 30초 이내 Newsfeed_Cache 반영 검증
    - _Requirements: 3.1, 7.4_
  - [ ]* 16.3 뉴스피드 읽기 흐름 통합 테스트 작성
    - `tests/integration/test_newsfeed_read_flow.py` 작성: 캐시 MISS → DB 폴백 → 피드 반환 전체 흐름 검증
    - _Requirements: 4.6_

- [ ] 17. 최종 Checkpoint — 전체 테스트 통과 확인
  - 모든 테스트(단위, 속성 기반, 통합)가 통과하는지 확인하고, 궁금한 점이 있으면 사용자에게 질문한다.

---

## Notes

- `*` 표시 서브태스크는 선택 사항으로, MVP 빠른 구현 시 건너뛸 수 있다.
- 각 태스크는 이전 태스크를 기반으로 점진적으로 빌드된다. 고아 코드가 생기지 않도록 각 단계에서 앱에 연결한다.
- 속성 기반 테스트는 Hypothesis 라이브러리를 사용하며 최소 100회 반복 실행한다.
- 각 속성 테스트 함수에 `# Feature: newsfeed-system, Property {번호}: {속성 요약}` 태그 주석을 포함한다.
- 통합 테스트는 `docker-compose up`으로 실제 PostgreSQL, Redis, RabbitMQ 컨테이너를 기동한 후 실행한다.
- 설계 문서의 22개 Correctness Property는 각 속성 기반 테스트 서브태스크에서 명시적으로 검증한다.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["2.1", "3.1", "7.1", "8.1", "8.2", "9.1", "9.2", "11.1", "12.1", "13.1", "14.1", "14.2"] },
    { "id": 1, "tasks": ["2.2", "3.2", "7.2", "8.3", "9.3", "11.2", "12.2", "13.2"] },
    { "id": 2, "tasks": ["2.3", "3.3", "5.1", "5.2", "7.3", "8.4", "9.4", "11.3", "12.3"] },
    { "id": 3, "tasks": ["5.3", "5.4"] },
    { "id": 4, "tasks": ["16.1", "16.2", "16.3"] }
  ]
}
```
