# Flowcheck AI Backend

[`docs/AI_BACKEND_API_SPEC.md`](../../docs/AI_BACKEND_API_SPEC.md)의 계약을 구현한 독립 FastAPI 서비스다.

```text
POST /v1/tasks/generate
POST /v1/knowledge/answer
POST /v1/attempts/check
```

## 실행

루트 `.env`에 아래 값이 필요하다.

```text
OPENROUTER_API_KEY
CEREBRAS_API_KEY       # Gemma 4 31B 사용 시
AI_SERVICE_TOKEN
AI_CORS_ORIGINS        # 선택
AI_RUNTIME_SETTINGS_PATH # 선택, 기본 .data/ai-backend-settings.json
AI_REQUEST_LOG_PATH      # 선택, 기본 .data/ai-request-log.jsonl
AI_MAX_CONCURRENT_REQUESTS # 선택, 기본 16
AI_MAX_QUEUED_REQUESTS     # 선택, 기본 64
AI_QUEUE_TIMEOUT_SECONDS   # 선택, 기본 3
```

- `OPENROUTER_API_KEY`: Luna 호출용 OpenRouter 키
- `CEREBRAS_API_KEY`: `gemma-4-31b` 호출용 Cerebras 키
- `AI_SERVICE_TOKEN`: 제품 백엔드와 이 서버가 공유할 충분히 긴 임의 토큰
- `AI_CORS_ORIGINS`: 배포된 테스트 콘솔 주소가 있으면 쉼표로 구분해 설정. 로컬 `localhost`와 `127.0.0.1`은 기본 허용
- `AI_RUNTIME_SETTINGS_PATH`: 모델·프롬프트·서비스 토큰 해시 저장 파일. OCI에서 재시작 후 유지하려면 영구 볼륨 경로로 지정
- `AI_REQUEST_LOG_PATH`: 최근 API 요청의 시각·경로·상태·소요 시간을 보존하는 JSONL 파일
- `AI_MAX_CONCURRENT_REQUESTS`: 한 프로세스에서 동시에 실행할 실제 AI 제공사 호출 수
- `AI_MAX_QUEUED_REQUESTS`: 실행 슬롯을 기다릴 수 있는 요청 수
- `AI_QUEUE_TIMEOUT_SECONDS`: 대기 요청이 슬롯을 기다릴 최대 시간. 초과하면 `429 AI_BUSY` 반환
- 초기 모델은 `openai/gpt-5.6-luna`다. 운영 콘솔에서 `gemma-4-31b`로 바꿀 수 있다.
- Cerebras를 선택해도 429, 공급자 오류, 출력 한도 도달 또는 응답 형식 실패가 발생하면 OpenRouter 키가 설정되고 운영 설정의 자동 장애 전환이 켜진 경우 같은 요청을 운영 콘솔에서 별도로 선택한 OpenRouter 폴백 모델로 자동 전환한다.
- OpenRouter Luna의 개별 요청 제한 시간은 20초다.
- Cerebras 태스크 생성은 최대 8,192 토큰, 사진 판정은 최대 512 토큰으로 제한하고 요청 시간 제한은 8초다.
- 매장 정보 질문은 최대 60,000자의 정보와 이전 대화 내역을 포함할 수 있는 최대 10,000자의 질문을 받을 수 있어 제공사와 관계없이 요청 시간 제한을 30초로 늘리고, 최대 2,048 토큰의 답을 받는다. 전체 정보·원문 복사를 요구하면 프롬프트 지시에 따라 질문 범위를 좁혀 달라는 짧은 답을 반환한다.
- Cerebras 태스크 생성은 제공사가 지원하는 strict JSON Schema 부분집합과 `temperature=0`, `seed=0`을 사용한다. 배열 개수 제한은 공급자 스키마에 넣지 않고 Pydantic 계약으로 검증한다.
- 태스크 생성은 별도 키워드 필터 없이 같은 LLM 호출에서 생성 가능성을 먼저 판정한다. 실제 행동이나 점검 대상을 입력에서 찾을 수 없거나 무의미·업무 무관·위험한 요청이면 태스크를 억지로 만들지 않고 `422 TASK_GENERATION_REJECTED`와 300자 이내의 보완 안내를 반환한다. 정상 생성 응답의 `{ "tasks": [...] }` 계약은 그대로 유지한다.
- 태스크 생성은 입력 문장과 현재 제공사·모델·태스크 생성 프롬프트가 모두 같으면 성공 응답을 24시간 동안 메모리에서 재사용한다. 최대 256건을 보관하며 동시 동일 요청도 한 번만 AI에 전달한다. 서버 재시작 시 캐시는 비워진다.
- 사진 검증은 두 이미지의 검증된 SHA-256, 태스크 내용, 현재 제공사·모델·사진 검증 프롬프트가 모두 같으면 PASS/RETAKE 응답을 24시간 동안 재사용한다. 최대 1,024건을 보관하고 URL이 달라도 실제 이미지가 같으면 적중하며, 서버 재시작 시 비워진다.
- 매장 정보 질문은 정보 본문이 클 수 있고 최신 기록을 그대로 반영해야 하므로 캐시를 조회하거나 저장하지 않는다. 같은 요청을 두 번 보내면 AI도 두 번 호출한다.
- 기본 동시 AI 제공사 호출은 16개이며, 추가 요청은 최대 64개까지 3초 동안 대기한다. 대기열이 가득 차거나 3초 안에 슬롯이 나지 않으면 `429 AI_BUSY`와 `Retry-After: 3`을 반환한다. 캐시 HIT와 동일 요청 JOIN은 추가 제공사 호출이 아니므로 새 슬롯을 사용하지 않는다.

프로젝트 루트에서:

```bash
uv sync --project apps/ai-backend
uv run --project apps/ai-backend uvicorn ai_backend.main:app --app-dir apps/ai-backend --host 127.0.0.1 --port 8000
```

Swagger UI는 `http://127.0.0.1:8000/docs`에서 확인한다. `GET /healthz`는 활성 제공사 키가 준비됐을 때 `200 {"status":"ok"}`, 준비되지 않았으면 503을 반환한다.

제품 백엔드는 모든 요청에 다음 헤더를 보낸다.

```text
Authorization: Bearer <AI_SERVICE_TOKEN>
Content-Type: application/json
```

사진 검사 API는 사용자 `photo`와 선택형 `referencePhoto`를 같은 형식으로 받는다. 각 HTTPS 사진을 직접 내려받고 실제 바이트의 MIME 시그니처, 크기, SHA-256을 요청 메타데이터와 비교한 뒤 Luna에 전달한다. 리다이렉트, 이미지당 10MB 초과 파일, 사설·루프백·링크로컬 주소로 해석되는 URL은 거부한다.

태스크 생성·사진 검증·매장 정보 답변 프롬프트의 기본값은 [`ai_backend/prompts.py`](./ai_backend/prompts.py)에 모여 있다. 운영 콘솔에서 바꾼 태스크·사진 프롬프트는 런타임 설정 파일에 저장되며 다음 요청부터 적용된다.

## 테스트 콘솔

[독립 Next.js 콘솔](../ai-console/README.md)을 실행하고 `http://127.0.0.1:3100`을 연다. 서버에 설정된 연결을 사용해 태스크 생성·편집, 매장 정보 질문, 사진별 PASS/RETAKE와 서버 설정을 확인할 수 있다.

브라우저의 로컬 파일에는 HTTPS URL이 없으므로 테스트 콘솔은 같은 `/v1/attempts/check`에 `multipart/form-data`로 `task` JSON 문자열, 필수 `photo`, 선택형 `referencePhoto` 파일을 보낸다. 제품 백엔드는 두 이미지 모두 임시 HTTPS URL을 사용하는 JSON 계약으로 호출한다.

## 운영 설정 API

콘솔 전용 경로이며 제품 백엔드 연동 계약에는 포함하지 않는다.

```text
GET /v1/admin/settings
PUT /v1/admin/settings
GET /v1/admin/requests
```

- 현재 `AI_SERVICE_TOKEN`으로 인증한다.
- 제공사·지원 비전 모델·별도 OpenRouter 폴백 모델·두 프롬프트와 캐시 히트·TTL, 자동 장애 전환, 요청 기록 정책을 조회하고 변경한다.
- `PUT`의 선택 필드 `newServiceToken`으로 서비스 토큰을 교체한다. 교체 직후 기존 토큰은 무효화된다.
- 서비스 토큰 원문과 제공사 API 키는 응답하지 않는다. API 키는 환경변수 설정 여부만 반환한다.
- 저장 파일에는 서비스 토큰의 SHA-256만 기록한다.
- 요청 기록에는 메소드, 경로, 실제 연결 주소, 상태 코드, 처리 시간과 함께 선택 제공사·모델, 캐시 상태, 장애 전환, 처리 결과, 검증된 요청과 최종 응답 JSON을 남긴다. 요청·응답은 각각 최대 128,000자까지 보존한다. 검증 사진은 원본을 저장하지 않고 장당 최대 640px·200KB JPEG 미리보기만 저장하며 요청당 제출·모범 사진 두 장까지다. 인증 헤더와 서비스 토큰, 사진 원본 바이너리, 사진 URL의 쿼리·프래그먼트는 기록하지 않는다. Cerebras 실패 시 finish reason, completion token 수, 모델 실패 출력 또는 HTTP 오류 본문을 최대 64,000자까지 보존한다.
- 요청 로그 JSONL 파일의 하드 상한은 64 MiB(67,108,864바이트)다. 다음 기록이 상한을 넘기기 전에 최신 기록부터 56 MiB 이내로 원자적으로 축소하며 메모리에도 최대 500건만 유지한다. 시작 시 기존 파일이 상한을 넘었어도 마지막 64 MiB만 제한된 메모리로 읽고 즉시 같은 기준으로 복구하므로 1 GiB까지 증가하지 않는다.

## 테스트

```bash
uv run --project apps/ai-backend pytest apps/ai-backend/tests
uv run --project apps/ai-backend ruff check apps/ai-backend/ai_backend apps/ai-backend/tests
uv run --project apps/ai-backend ruff format --check apps/ai-backend/ai_backend apps/ai-backend/tests
```

테스트는 서비스 토큰, 요청 스키마, PASS/RETAKE, 네 오류 응답, AI 형식 교정, 사진 다운로드·무결성 검사를 외부 API 호출 없이 검증한다.
