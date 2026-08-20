# 백엔드 → AI 백엔드 API

## 1. 공통

| 메소드 | 경로 | 용도 |
| --- | --- | --- |
| `POST` | `/v1/tasks/generate` | 자연어에서 태스크 목록 생성 |
| `POST` | `/v1/knowledge/answer` | 사장이 기록한 매장 정보에 근거해 알바생 질문 답변 |
| `POST` | `/v1/attempts/check` | 태스크 인증 사진과 모범 사진 비교 검사 |

공통 헤더:

```text
Authorization: Bearer <service-token>
Content-Type: application/json
```

- 통신 방식은 동기식 HTTP다.
- 제한 시간은 60초다.
- 로컬 테스트 콘솔은 사진 파일을 시험하기 위해 검사 경로에 한해 `multipart/form-data`를 사용할 수 있다. 제품 백엔드 계약은 `application/json`이다.
- 기본값으로 실제 AI 제공사 호출은 동시에 16개까지만 실행하고, 추가 요청은 최대 64개까지 3초 동안 기다린다.
- 슬롯이 3초 안에 나지 않거나 대기열까지 차면 `429 AI_BUSY`와 `Retry-After`를 반환한다.
- 캐시 HIT와 동일 요청 JOIN은 새로운 AI 제공사 호출을 만들지 않으므로 추가 실행 슬롯을 사용하지 않는다.

## 2. 태스크 목록 생성

`POST /v1/tasks/generate`

### 요청

```json
{
  "message": "오픈 전에 조명을 켜고 POS기 전원과 카운터 정리를 확인해야 해"
}
```

### 응답

```json
{
  "tasks": [
    {
      "title": "조명 점등 확인",
      "instruction": "매장 전체 조명이 보이도록 촬영해 주세요.",
      "completionType": "PHOTO",
      "rule": "사진에서 출입문 주변과 간판을 확인할 수 있고 매장 조명이 켜져 있어야 한다."
    },
    {
      "title": "POS 전원 확인",
      "instruction": "POS 화면이 보이도록 촬영해 주세요.",
      "completionType": "PHOTO",
      "rule": "사진에서 POS 화면이 켜져 있고 정상 화면이 표시되어야 한다."
    },
    {
      "title": "매장 바닥 청소",
      "instruction": "바닥 청소를 마친 뒤 완료를 체크해 주세요.",
      "completionType": "CHECK",
      "rule": null
    }
  ]
}
```

### 규칙

- `message`: 1~2,000자
- `tasks`: 1~20개
- `title`: 1~80자
- `instruction`: 1~500자
- `completionType`: `PHOTO | CHECK`
- `PHOTO`: `rule`에 사진으로 확인할 모든 조건을 하나의 문자열로 작성한다.
- `CHECK`: `rule`은 `null`이다.
- `rule`: 최대 1,000자
- AI는 같은 호출 안에서 먼저 입력이 실제 업무로 변환 가능한지 판단한다.
- 구체적인 행동이나 점검 대상을 입력에서 찾을 수 있으면 기존과 같이 태스크를 생성한다.
- 무의미한 글자 나열, 맥락 없는 문장, 업무와 무관한 질문, 행동이나 대상이 없어 요구사항을 새로 지어내야 하는 입력은 생성하지 않고 `422 TASK_GENERATION_REJECTED`를 반환한다.
- 거부 판단은 별도 키워드 필터가 아니라 LLM이 입력 전체 문맥으로 수행하며, 응답 메시지에는 이유와 보완할 정보를 300자 이내로 안내한다.
- AI가 잘못된 형식으로 응답하면 AI 백엔드가 한 번 교정하고, 다시 실패하면 `503 AI_UNAVAILABLE`을 반환한다.

## 3. 매장 정보 질문

`POST /v1/knowledge/answer`

### 요청

```json
{
  "information": "이번 주 금요일 오후 6시에 신메뉴 시식 행사가 시작됩니다. 참여 고객에게는 샘플 한 개를 제공하고, 재고가 소진되면 행사를 종료합니다.",
  "question": "신메뉴 시식 행사는 언제 시작해?"
}
```

### 응답

```json
{
  "answer": "신메뉴 시식 행사는 이번 주 금요일 오후 6시에 시작합니다."
}
```

### 규칙

- `information`: 1~60,000자. 사장이 기록한 공지, 운영 정보와 이벤트 정보를 텍스트로 전달한다.
- `question`: 1~10,000자. 필요한 경우 이전 대화 내역을 함께 포함할 수 있다.
- `answer`: 1~8,000자. 응답 객체에는 이 필드 하나만 존재한다.
- AI는 `information`만 근거로 답하고, 확인할 수 없으면 기록된 정보에서 확인할 수 없다고 답한다.
- 이 경로는 캐시를 조회하거나 저장하지 않는다. 동일 요청도 매번 현재 정보로 AI를 호출한다.

## 4. 태스크 인증 사진 검사

`POST /v1/attempts/check`

`PHOTO` 태스크에만 호출한다. `CHECK` 태스크는 백엔드가 직접 완료 처리한다.

사용자가 제출한 사진은 `photo`, 사장이 미리 등록한 모범 사진은 `referencePhoto`로 전달한다. 모범 사진이 없는 태스크는 `referencePhoto`를 생략한다.

### 요청

```json
{
  "task": {
    "title": "POS 전원 확인",
    "instruction": "POS 화면이 보이도록 촬영해 주세요.",
    "rule": "사진에서 POS 화면이 켜져 있고 정상 화면이 표시되어야 한다."
  },
  "photo": {
    "mimeType": "image/jpeg",
    "sizeBytes": 182034,
    "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "url": "https://storage.example.com/user-photo-signed-url"
  },
  "referencePhoto": {
    "mimeType": "image/jpeg",
    "sizeBytes": 245781,
    "sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
    "url": "https://storage.example.com/reference-photo-signed-url"
  }
}
```

`photo`와 `referencePhoto`는 같은 형식을 사용한다.

| 필드 | 필수 | 내용 |
| --- | --- | --- |
| `photo` | 필수 | 사용자가 이번에 제출한 인증 사진 |
| `referencePhoto` | 선택 | 사장이 미리 등록한 모범 사진. 등록된 경우에만 포함 |

이미지 객체 조건:

- `mimeType`: `image/jpeg | image/png | image/webp`
- `sizeBytes`: 1바이트 이상, 최대 10MB
- `sha256`: 64자리 소문자 16진수
- `url`: HTTPS 읽기 전용 임시 URL
- 제품 백엔드는 두 이미지 모두 같은 방식으로 임시 URL과 메타데이터를 만든다.
- AI 백엔드는 각 이미지를 내려받아 형식, 크기와 SHA-256을 요청값과 비교한다.
- AI 백엔드는 이미지를 검사에만 사용하며 별도로 저장하지 않는다.

### 테스트 콘솔 직접 업로드

브라우저의 로컬 사진을 확인할 때만 같은 경로에 `multipart/form-data`로 요청할 수 있다.

| 필드 | 형식 | 내용 |
| --- | --- | --- |
| `task` | 문자열 | 위 요청의 `task` 객체를 JSON 문자열로 직렬화한 값 |
| `photo` | 파일 | JPEG, PNG 또는 WebP 이미지, 최대 10MB |
| `referencePhoto` | 파일, 선택 | 사장이 등록한 모범 이미지. `photo`와 같은 형식 및 용량 제한 적용 |

이 방식도 서비스 토큰이 필요하며 응답과 오류 형식은 JSON 방식과 같다. 제품 백엔드는 임시 HTTPS URL을 사용하는 원래 JSON 요청을 사용한다.

### 응답 — RETAKE

사용자 사진이 `rule` 또는 모범 사진의 관련 기준을 충족하지 않거나 판단하기 어려운 경우:

```json
{
  "status": "RETAKE",
  "reason": "POS 화면이 사진에서 보이지 않습니다.",
  "fix": "POS 화면이 선명하게 보이도록 다시 촬영해 주세요."
}
```

모범 사진과 사용자 사진에서 브랜드가 명백히 다른 경우:

```json
{
  "status": "RETAKE",
  "reason": "모범 사진은 GS25이지만 사용자 사진에는 CU 간판이 확인됩니다.",
  "fix": "모범 사진과 같은 매장에서 다시 촬영해 주세요."
}
```

### 응답 — PASS

`rule`의 모든 조건을 사용자 사진에서 확인하고, 모범 사진이 있다면 관련 상태가 기준에 맞는 경우:

```json
{
  "status": "PASS",
  "reason": "POS 화면이 켜져 있고 정상 화면이 선명하게 표시되어 있습니다."
}
```

### 규칙

- `status`: `PASS | RETAKE`
- `reason`: 항상 필요하며 최대 500자
- `fix`: `RETAKE`일 때만 필요하며 최대 500자
- `rule`을 명시적인 판정 기준으로 우선한다.
- `referencePhoto`는 대상의 모양, 배치와 완료 상태를 이해하기 위한 비교 기준이다.
- `referencePhoto`가 있으면 `rule` 검사 전에 브랜드, 간판, 고정 설비와 공간 구조가 명백히 충돌하는지 확인한다.
- GS25와 CU처럼 서로 다른 브랜드가 명확히 보이면 다른 매장으로 판정하고 `RETAKE`를 반환한다. `reason`에는 충돌한 단서를 쓴다.
- 촬영 각도, 밝기, 사람이나 일시적인 물건 배치 차이만으로 `RETAKE` 처리하지 않는다.
- 같은 브랜드라는 사실만으로 같은 지점이라고 단정하지 않는다.
- 어느 한쪽에서 브랜드나 장소 단서가 보이지 않으면 그 이유만으로 반려하지 않고 `rule`을 검사한다.
- `referencePhoto`가 없으면 기존처럼 `rule`과 사용자 사진만으로 판정한다.
- 불분명한 사진을 추측해서 `PASS`로 처리하지 않는다.
- `PASS`일 때는 `fix`를 반환하지 않는다.

## 5. 오류

오류는 호출 백엔드의 대응이 달라지는 다섯 종류만 사용한다.

```json
{
  "error": {
    "code": "PHOTO_UNAVAILABLE",
    "message": "모범 사진을 불러올 수 없습니다.",
    "field": "referencePhoto"
  }
}
```

문제가 발생한 필드를 특정할 수 있으면 `field`에 점 표기 경로로 반환한다. 예를 들어 질문 길이 초과는 `question`, 모범 사진 오류는 `referencePhoto`다. 길이 오류 메시지에는 최대 길이와 실제 길이가 함께 포함된다.

| HTTP | code | 의미 | 백엔드 대응 |
| --- | --- | --- | --- |
| `400` | `INVALID_REQUEST` | 메시지와 `field`로 설명된 입력 오류 | 해당 필드 수정 |
| `401` | `UNAUTHORIZED` | 서비스 토큰이 잘못됨 | 서버 설정 확인 |
| `422` | `TASK_GENERATION_REJECTED` | 입력에서 책임 있게 생성할 구체적인 업무를 찾지 못함 | AI가 반환한 이유에 맞게 업무 행동과 대상을 보완 |
| `422` | `PHOTO_UNAVAILABLE` | 사용자 또는 모범 사진 다운로드·무결성 확인 실패 | `field`에 해당하는 새 사진 URL로 재요청 |
| `429` | `AI_BUSY` | 동시 실행 슬롯과 대기 용량이 가득 참 | `Retry-After` 이후 재요청 |
| `503` | `AI_UNAVAILABLE` | AI 처리 실패 또는 시간 초과 | 동일 요청 한 번 재시도 |

- 사진 내용이 기준에 맞지 않거나 불분명한 것은 오류가 아니라 `RETAKE`다.
- `429` 응답에는 대기할 초 단위 시간을 `Retry-After` 헤더로 반환한다.
- `503` 재시도도 실패하면 백엔드는 제출 사진을 유지하고 검사 지연으로 표시한다.
- 모델 제공자의 원문 오류는 응답에 포함하지 않는다.

## 6. 운영 콘솔 설정 API

제품 백엔드가 호출하는 계약이 아니라 독립 AI 콘솔에서만 사용한다. 두 경로 모두 현재 서비스 Bearer 토큰이 필요하다.

| 메소드 | 경로 | 용도 |
| --- | --- | --- |
| `GET` | `/v1/admin/settings` | 현재 모델·프롬프트·키 설정 여부 조회 |
| `PUT` | `/v1/admin/settings` | 모델·프롬프트·서비스 토큰 변경 |

### 조회 응답

```json
{
  "provider": "OPENROUTER",
  "model": "openai/gpt-5.6-luna",
  "prompts": {
    "taskGeneration": "태스크 생성 프롬프트",
    "photoCheck": "사진 검증 프롬프트"
  },
  "openrouterModels": [
    "openai/gpt-5.6-luna",
    "google/gemini-2.5-flash"
  ],
  "fallbackModel": "google/gemini-2.5-flash",
  "cacheHitsEnabled": true,
  "cacheTtlSeconds": 86400,
  "fallbackEnabled": true,
  "requestLogsEnabled": true,
  "providerKeys": {
    "openrouter": true,
    "cerebras": true
  },
  "availableModels": [
    {
      "provider": "OPENROUTER",
      "id": "openai/gpt-5.6-luna",
      "label": "GPT-5.6 Luna · OpenRouter"
    },
    {
      "provider": "CEREBRAS",
      "id": "gemma-4-31b",
      "label": "Gemma 4 31B · Cerebras"
    }
  ],
  "revision": 1
}
```

### 변경 요청

```json
{
  "provider": "CEREBRAS",
  "model": "gemma-4-31b",
  "prompts": {
    "taskGeneration": "수정한 태스크 생성 프롬프트",
    "photoCheck": "수정한 사진 검증 프롬프트"
  },
  "openrouterModels": [
    "openai/gpt-5.6-luna",
    "google/gemini-2.5-flash"
  ],
  "fallbackModel": "google/gemini-2.5-flash",
  "cacheHitsEnabled": false,
  "cacheTtlSeconds": 21600,
  "fallbackEnabled": true,
  "requestLogsEnabled": true,
  "newServiceToken": "optional-new-service-token"
}
```

- `newServiceToken`은 바꿀 때만 보내며 24자 이상이다.
- `cacheHitsEnabled`가 `false`이면 메모리 캐시를 삭제하지 않고 캐시 조회만 건너뛴다. 생략하면 현재 설정을 유지한다.
- `cacheTtlSeconds`는 60초부터 7일까지 설정하며 기존 캐시에도 다음 조회부터 적용한다.
- `fallbackEnabled`가 `true`이면 Cerebras 장애 시 OpenRouter 키가 있을 때 `fallbackModel`로 한 번 재시도한다.
- `fallbackModel`은 `openrouterModels`에 등록된 모델 중 하나여야 하며, 활성 주 모델과 별도로 선택한다. 이전 설정 파일에 값이 없으면 기존 동작대로 첫 번째 OpenRouter 모델을 사용한다.
- `requestLogsEnabled`가 `false`이면 이후 요청 메타데이터를 파일에 추가하지 않는다. 기존 기록은 삭제하지 않는다.
- 변경 응답은 조회 응답과 같다. 새 토큰을 보냈다면 응답 직후부터 기존 토큰은 무효다.
- 제공사 API 키와 서비스 토큰 원문은 어떤 응답에도 포함하지 않는다.
- `providerKeys`는 환경변수 설정 여부만 나타낸다.
- 지원 모델은 사진 입력이 확인된 모델만 목록에 둔다.
- 요청 기록은 검증된 요청과 최종 응답 JSON을 각각 최대 128,000자까지 포함한다. 검증 사진 원본은 저장하지 않고 제출·모범 사진을 각각 최대 640px·200KB JPEG 미리보기로만 포함한다. 인증 헤더와 서비스 토큰, 사진 원본 바이너리, 사진 URL의 쿼리·프래그먼트는 제외한다. 실패한 Cerebras 출력은 최대 64,000자까지 포함하며 finish reason, completion token 수, 잘림 여부를 함께 반환한다.
- 요청 로그 JSONL 전체 하드 상한은 64 MiB(67,108,864바이트), 메모리 상한은 500건이다. 새 기록으로 파일이 상한을 넘기기 전에 최신 기록 기준 56 MiB 이내로 원자적 축소하며, 시작 시 이미 커진 파일도 마지막 64 MiB만 읽어 같은 기준으로 즉시 복구한다.
