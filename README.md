# AI Backend

태스크 생성, 매장 정보 질문 답변과 사진 검증을 제공하는 AI 전용 서버와 관리 콘솔입니다.

## 구성

- `apps/ai-backend`: FastAPI AI 백엔드
- `apps/ai-console`: Next.js 테스트·관리 콘솔
- `docs`: 제품 백엔드 연동 및 전체 API 명세
- `deploy/oci`: Oracle Cloud systemd 서비스 설정

## 로컬 실행

Python 3.12 이상, [uv](https://docs.astral.sh/uv/)와 Node.js가 필요합니다.
먼저 예시 환경 설정을 복사하고 실제 키와 서비스 토큰을 입력합니다. `.env`는 Git에 포함되지 않습니다.

```bash
cp .env.example .env
uv sync --project apps/ai-backend
npm ci --prefix apps/ai-console
```

비밀 환경변수는 루트 `.env` 한 곳에서 관리하며 `.env.example`과 같은 세 키만 둡니다.

- 제공사: `OPENROUTER_API_KEY`, `CEREBRAS_API_KEY`
- 공용 인증: `AI_SERVICE_TOKEN`

OpenAI·Kakao·AWS·S3 등 이 저장소에서 참조하지 않는 키는 루트 `.env`에 두지 않습니다.
로컬 콘솔 주소와 데이터 경로는 코드 기본값을 사용하며, Oracle의 주소·CORS·영구 데이터 경로는 `deploy/oci` systemd unit에서 관리합니다. 필요할 때만 실행 환경에서 `AI_BACKEND_URL`, `AI_CORS_ORIGINS`, `AI_RUNTIME_SETTINGS_PATH`, `AI_REQUEST_LOG_PATH`를 덮어씁니다.
AI 호출 동시 실행 수, 대기열과 대기 시간은 각각 `AI_MAX_CONCURRENT_REQUESTS`, `AI_MAX_QUEUED_REQUESTS`, `AI_QUEUE_TIMEOUT_SECONDS`로 조정하며 기본값은 16개, 64개, 3초입니다.

루트 `.env`를 환경에 로드한 뒤 실행합니다.

```bash
set -a
source .env
set +a
uv run --project apps/ai-backend uvicorn ai_backend.main:app \
  --app-dir apps/ai-backend --host 127.0.0.1 --port 8000
```

```bash
npm --prefix apps/ai-console run dev
```

- AI 백엔드: `http://127.0.0.1:8000`
- 관리 콘솔: `http://127.0.0.1:3100`

관리 콘솔의 PHOTO 태스크는 벤치마크 탭에 별도 케이스로 저장할 수 있습니다. 기대 `PASS`/`RETAKE`, 인증·모범 사진과 마지막 실행 결과는 브라우저 IndexedDB에 유지되며, 사진은 파일당 10MB·전체 1GB로 제한됩니다. 전체 실행은 최대 4개씩 병렬 처리하고 실제 응답 모델, 평균 시간과 일치율을 집계합니다. JSON 상세 보고서에는 실행 시 최초 호출에 사용된 유효 프롬프트도 포함됩니다.

## 검증

```bash
uv run --project apps/ai-backend pytest apps/ai-backend/tests
uv run --project apps/ai-backend ruff check apps/ai-backend/ai_backend apps/ai-backend/tests
uv run --project apps/ai-backend ruff format --check apps/ai-backend/ai_backend apps/ai-backend/tests
npm --prefix apps/ai-console run lint
npm --prefix apps/ai-console run build
```

## 주요 API

- `POST /v1/tasks/generate`
- `POST /v1/knowledge/answer`
- `POST /v1/attempts/check`

## 문서

- `docs/AI_BACKEND_SPEC.md`: 제품 백엔드 전달용
- `docs/AI_BACKEND_API_SPEC.md`: 전체 AI API 명세
