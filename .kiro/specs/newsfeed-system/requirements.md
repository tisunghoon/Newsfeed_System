# Requirements Document

# 요구사항 문서

## Introduction

## 소개

뉴스피드 시스템은 사용자가 스토리를 게시하고, 친구들의 최신 스토리를 시간 역순으로 조회할 수 있는 소셜 피드 서비스다. 피드 발행(Fan-out)과 피드 읽기 두 가지 핵심 흐름으로 구성되며, 캐시 계층을 적극 활용하여 빠른 읽기 성능을 보장한다. 본 토이프로젝트는 실제 대규모 시스템의 핵심 개념(팬아웃, 캐시, 소셜 그래프)을 학습 목적으로 구현하는 데 초점을 맞춘다.

---

## Glossary

## 용어 정의

- **User**: 뉴스피드 시스템에 등록된 사용자
- **Post**: 사용자가 게시하는 텍스트, 이미지, 비디오 형태의 콘텐츠 단위
- **Newsfeed**: 특정 사용자의 친구들이 게시한 Post를 시간 역순으로 정렬한 목록
- **Fanout_Service**: 새 Post를 작성자의 친구 목록으로 전파하는 서비스
- **Post_Service**: Post를 데이터베이스 및 캐시에 저장하는 서비스
- **Newsfeed_Service**: 뉴스피드 캐시에서 피드 목록을 조회하여 클라이언트에 반환하는 서비스
- **Notification_Service**: 친구에게 새 Post 알림(푸시 알림 포함)을 전송하는 서비스
- **Newsfeed_Cache**: 각 사용자의 피드에 표시될 Post ID 목록을 보관하는 캐시 레이어
- **Post_Cache**: Post의 상세 데이터(콘텐츠, 메타데이터)를 보관하는 캐시 레이어
- **Social_Graph**: 사용자 간 친구 관계 정보를 저장하는 그래프 데이터베이스 또는 캐시
- **Load_Balancer**: 클라이언트 요청을 웹 서버로 분산하는 구성 요소
- **Web_Server**: 클라이언트의 HTTP 요청을 인증·처리율 제한 후 내부 서비스로 중계하는 서버
- **Message_Queue**: Fanout_Service가 친구 목록과 Post ID를 비동기 전달하는 메시지 큐
- **Fanout_Worker**: Message_Queue에서 메시지를 소비하여 Newsfeed_Cache를 갱신하는 작업 서버
- **Dead_Letter_Queue**: 반복 실패한 메시지를 별도로 보관하는 큐

---

## Requirements

## 요구사항

### 요구사항 1: 사용자 인증 및 처리율 제한

**User Story:** 개발자로서, API가 인증된 요청만 처리하고 과도한 요청을 차단하기를 원한다. 그래야 서비스 안정성을 보장할 수 있다.

#### 수용 기준

1. WHEN 클라이언트가 Authorization 헤더 없이 API 요청을 보낼 때, THE Web_Server SHALL HTTP 401 응답과 함께 "인증 토큰이 없습니다"라는 오류 메시지를 반환한다.
2. WHEN 클라이언트가 서명 검증에 실패한 인증 토큰으로 API 요청을 보낼 때, THE Web_Server SHALL HTTP 401 응답과 함께 "유효하지 않은 토큰입니다"라는 오류 메시지를 반환한다.
3. IF 인증 토큰이 만료되었다면, THEN THE Web_Server SHALL HTTP 401 응답과 함께 "토큰이 만료되었습니다"라는 오류 메시지를 반환하고, 요청한 작업을 수행하지 않는다.
4. WHEN 동일 사용자가 60초 내에 100회를 초과하는 POST /v1/me/feed 요청을 보낼 때, THE Web_Server SHALL HTTP 429 응답과 함께 남은 대기 시간(초)을 응답 본문에 포함하여 반환하고 요청을 차단한다.
5. IF 처리율 제한 저장소에 접근할 수 없는 경우, THEN THE Web_Server SHALL HTTP 503 응답을 반환하고 오류를 로그에 기록한다.

---

### 요구사항 2: 포스팅 발행 (Post 저장)

**User Story:** 사용자로서, 텍스트·이미지·비디오를 포함한 스토리를 게시하고 싶다. 그래야 내 친구들이 내 소식을 볼 수 있다.

#### 수용 기준

1. WHEN 인증된 User가 POST /v1/me/feed 요청을 전송할 때, THE Post_Service SHALL Post를 데이터베이스에 저장하고 Post_Cache에도 기록한다.
2. THE Post_Service SHALL Post 콘텐츠 타입으로 텍스트, 이미지 URL, 비디오 URL을 지원하며, 미디어 항목은 1개 이상 10개 이하로 첨부할 수 있다.
3. WHEN Post_Service가 Post 저장에 성공할 때, THE Post_Service SHALL 생성된 Post ID와 타임스탬프를 포함한 HTTP 201 응답을 반환한다.
4. IF 데이터베이스 저장이 실패할 경우, THEN THE Post_Service SHALL HTTP 500 응답을 반환하고 오류를 로그에 기록하며, Post_Cache 기록도 수행하지 않는다.
5. THE Post_Service SHALL Post 본문의 최대 길이를 2,000자로 제한한다.
6. IF Post 본문이 2,000자를 초과하거나 잘못된 콘텐츠 타입이 포함된 경우, THEN THE Post_Service SHALL HTTP 400 응답과 함께 유효성 검사 오류 메시지를 반환한다.
7. IF Post_Cache 기록이 실패하더라도 데이터베이스 저장이 성공한 경우, THEN THE Post_Service SHALL HTTP 201 응답을 반환한다.

---

### 요구사항 3: 팬아웃 (친구 피드 전파)

**User Story:** 사용자로서, 내가 새 스토리를 게시하면 내 친구들의 뉴스피드에도 즉시 반영되기를 원한다.

#### 수용 기준

1. WHEN Post_Service가 Post 저장을 완료할 때, THE Fanout_Service SHALL 2,000ms 이내에 Social_Graph에서 작성자의 친구 ID 목록을 조회한다.
2. WHEN Fanout_Service가 친구 목록을 조회할 때, THE Fanout_Service SHALL 해당 작성자로부터 알림 비활성화 또는 차단 상태를 설정한 친구를 전파 대상에서 제외한다.
3. WHEN Fanout_Service가 전파 대상 목록을 확정할 때, THE Fanout_Service SHALL 친구 ID 목록과 Post ID를 Message_Queue에 전송한다.
4. WHEN Fanout_Worker가 Message_Queue에서 메시지를 수신할 때, THE Fanout_Worker SHALL 각 친구의 Newsfeed_Cache에 Post ID를 삽입한다.
5. IF 팔로어 수가 10,000명을 초과하는 User가 Post를 작성하는 경우, THEN THE Fanout_Service SHALL 해당 User의 Post를 Newsfeed_Cache에 즉시 푸시하지 않고, Post_Store에 저장하여 읽기 시점에 조회 가능하도록 한다(풀 모델).
6. THE Newsfeed_Cache SHALL 사용자 1인당 최대 500개의 Post ID를 보관하며, 초과 시 가장 오래된 항목을 제거한다.
7. IF Fanout_Service가 Social_Graph 조회에 실패하는 경우, THEN THE Fanout_Service SHALL 오류를 로그에 기록하고 팬아웃 작업을 중단한다.
8. IF Fanout_Service가 Message_Queue 전송에 실패하는 경우, THEN THE Fanout_Service SHALL 최대 3회 재시도하고, 3회 모두 실패하면 해당 메시지를 Dead_Letter_Queue에 기록한다.
9. IF Fanout_Worker가 Newsfeed_Cache 삽입에 실패하는 경우, THEN THE Fanout_Worker SHALL 최대 3회 재시도하고, 3회 모두 실패하면 오류를 로그에 기록한다.

---

### 요구사항 4: 뉴스피드 조회

**User Story:** 사용자로서, 친구들의 최신 스토리를 시간 역순으로 볼 수 있기를 원한다.

#### 수용 기준

1. WHEN 인증된 User가 GET /v1/me/feed 요청을 보낼 때, THE Newsfeed_Service SHALL Newsfeed_Cache에서 해당 사용자의 Post ID 목록을 조회한다.
2. WHEN Newsfeed_Service가 Post ID 목록을 조회할 때, THE Newsfeed_Service SHALL Post_Cache 및 데이터베이스에서 Post 상세 정보를 조회하여 완전한 피드를 구성한다.
3. WHEN Newsfeed_Service가 피드를 구성할 때, THE Newsfeed_Service SHALL 피드를 게시 시각 기준 내림차순(최신순)으로 정렬하여 반환한다.
4. THE Newsfeed_Service SHALL 한 번의 응답에서 최대 20개의 Post를 반환하며, 응답에 다음 페이지 조회를 위한 cursor 값을 포함한다. 더 이상 조회할 Post가 없을 경우 cursor 값은 null로 반환한다.
5. WHEN Newsfeed_Service가 피드 구성을 완료할 때, THE Newsfeed_Service SHALL Post 목록을 JSON 형식으로 HTTP 200 응답에 담아 반환한다.
6. IF Newsfeed_Cache에 해당 사용자의 Post ID가 존재하지 않는 경우, THEN THE Newsfeed_Service SHALL 데이터베이스에서 직접 친구들의 최신 Post를 조회하여 피드를 구성한다.
7. IF 요청에 유효하지 않은 cursor 값이 포함된 경우, THEN THE Newsfeed_Service SHALL HTTP 400 응답과 함께 오류 메시지를 반환한다.
8. IF 데이터베이스 조회가 실패할 경우, THEN THE Newsfeed_Service SHALL HTTP 500 응답을 반환하고 오류를 로그에 기록한다.

---

### 요구사항 5: 알림 전송

**User Story:** 사용자로서, 친구가 새 스토리를 올리면 알림을 받고 싶다. 그래야 최신 소식을 놓치지 않을 수 있다.

#### 수용 기준

1. WHEN Fanout_Worker가 Newsfeed_Cache 갱신을 완료할 때, THE Notification_Service SHALL 해당 Post를 게시한 사용자를 팔로우하는 모든 사용자에게 새 Post 알림을 전송한다.
2. WHERE 사용자가 푸시 알림을 활성화한 경우, WHEN Notification_Service가 해당 사용자에게 알림을 전송할 때, THE Notification_Service SHALL 해당 사용자의 등록된 디바이스로 푸시 알림을 전송한다.
3. IF Notification_Service가 알림 전송에 실패할 경우, THEN THE Notification_Service SHALL 30초 간격으로 최대 3회 재시도하고, 3회 재시도 후에도 실패하면 오류를 로그에 기록하고 해당 알림 전송 작업을 종료한다.
4. IF 사용자가 푸시 알림을 비활성화한 경우, THEN THE Notification_Service SHALL 해당 사용자에게 디바이스 푸시 알림을 전송하지 않는다.

---

### 요구사항 6: 소셜 그래프 (친구 관계 관리)

**User Story:** 사용자로서, 다른 사용자와 친구 관계를 맺거나 끊을 수 있기를 원한다.

#### 수용 기준

1. WHEN 인증된 User가 POST /v1/me/friends/{target_user_id} 요청을 보낼 때, THE Social_Graph SHALL 두 사용자 간 양방향 친구 관계를 저장하고 HTTP 201 응답을 반환한다.
2. WHEN 인증된 User가 DELETE /v1/me/friends/{target_user_id} 요청을 보낼 때, THE Social_Graph SHALL 두 사용자 간 친구 관계를 삭제하고 HTTP 204 응답을 반환한다.
3. THE Social_Graph SHALL 사용자 1인당 최대 5,000명의 친구 관계를 지원한다.
4. IF 이미 친구 관계인 두 사용자 간에 친구 추가 요청이 들어올 경우, THEN THE Social_Graph SHALL HTTP 409 응답을 반환한다.
5. IF target_user_id가 존재하지 않는 사용자인 경우, THEN THE Social_Graph SHALL HTTP 404 응답을 반환한다.
6. IF 친구 수가 5,000명 한도에 도달한 사용자가 친구 추가를 시도하는 경우, THEN THE Social_Graph SHALL HTTP 409 응답과 함께 한도 초과 오류 메시지를 반환한다.
7. IF 존재하지 않는 친구 관계를 삭제하려는 경우, THEN THE Social_Graph SHALL HTTP 404 응답을 반환한다.

---

### 요구사항 7: 캐시 일관성 유지

**User Story:** 개발자로서, 캐시와 데이터베이스 간 데이터 불일치가 최소화되기를 원한다. 그래야 사용자에게 정확한 피드를 제공할 수 있다.

#### 수용 기준

1. WHEN Post가 삭제될 때, THE Post_Service SHALL Post_Cache에서 해당 Post 데이터를 즉시 제거한다.
2. WHEN Post가 삭제될 때, THE Post_Service SHALL 데이터베이스에서 해당 Post 데이터를 즉시 제거한다.
3. IF Post_Cache 또는 데이터베이스에서 Post 삭제가 실패할 경우, THEN THE Post_Service SHALL 삭제 실패를 나타내는 오류를 반환하고, 해당 Post의 캐시와 데이터베이스 상태를 삭제 시도 이전 상태로 유지한다.
4. WHEN Post가 삭제될 때, THE Fanout_Service SHALL 영향받는 모든 사용자의 Newsfeed_Cache에서 해당 Post ID를 30초 이내에 제거한다.
5. IF Newsfeed_Cache에서 Post ID 제거가 실패할 경우, THEN THE Fanout_Service SHALL 최대 3회까지 재시도하며, 3회 모두 실패 시 해당 작업을 Dead_Letter_Queue에 기록한다.
6. THE Post_Cache SHALL 각 Post 항목에 대해 24시간의 TTL(Time-To-Live)을 적용한다.
7. THE Newsfeed_Cache SHALL 각 사용자 피드 항목에 대해 48시간의 TTL을 적용한다.
