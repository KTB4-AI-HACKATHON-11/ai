from __future__ import annotations

import asyncio
import hashlib
import json
from io import BytesIO

import httpx
from ai_backend.app import create_app
from ai_backend.concurrency import AiBusyError
from ai_backend.config import Settings
from ai_backend.luna import AiUnavailableError
from ai_backend.photo import (
    PhotoUnavailableError,
    verified_photo_data_url,
    verify_photo_bytes,
)
from ai_backend.request_trace import update_request_trace
from ai_backend.schemas import (
    CheckableTask,
    KnowledgeAnswerResponse,
    PassResponse,
    PhotoInput,
    RetakeResponse,
    TaskGenerationResponse,
)
from fastapi import FastAPI
from PIL import Image

TOKEN = "test-service-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PHOTO_BUFFER = BytesIO()
Image.new("RGB", (16, 12), "navy").save(PHOTO_BUFFER, format="JPEG", quality=90)
PHOTO_BYTES = PHOTO_BUFFER.getvalue()
PHOTO = {
    "mimeType": "image/jpeg",
    "sizeBytes": len(PHOTO_BYTES),
    "sha256": hashlib.sha256(PHOTO_BYTES).hexdigest(),
    "url": "https://storage.example.com/photo.jpg",
}
REFERENCE_PHOTO = {**PHOTO, "url": "https://storage.example.com/reference.jpg"}


class FakeAi:
    async def generate_tasks(self, _message: str) -> TaskGenerationResponse:
        return TaskGenerationResponse.model_validate(
            {
                "tasks": [
                    {
                        "title": "POS 전원 확인",
                        "instruction": "POS 화면을 촬영해 주세요.",
                        "completionType": "PHOTO",
                        "rule": "POS 화면이 켜져 있어야 한다.",
                    },
                    {
                        "title": "바닥 청소",
                        "instruction": "청소 후 완료를 체크해 주세요.",
                        "completionType": "CHECK",
                        "rule": None,
                    },
                ]
            }
        )

    async def check_attempt(
        self,
        _task: CheckableTask,
        _photo_data_url: str,
        _reference_photo_data_url: str | None = None,
    ) -> RetakeResponse:
        return RetakeResponse(
            status="RETAKE",
            reason="POS가 보이지 않습니다.",
            fix="POS가 보이게 촬영해 주세요.",
        )

    async def answer_knowledge(
        self, _information: str, _question: str
    ) -> KnowledgeAnswerResponse:
        return KnowledgeAnswerResponse(answer="오늘 행사는 오후 6시에 시작합니다.")


class BusyAi(FakeAi):
    async def generate_tasks(self, _message: str) -> TaskGenerationResponse:
        raise AiBusyError


async def fake_photo_loader(photo: PhotoInput) -> str:
    return verify_photo_bytes(photo, PHOTO_BYTES)


def build_app(ai: FakeAi | None = None) -> FastAPI:
    return create_app(
        Settings(openrouter_api_key="test-key", service_token=TOKEN),
        ai=ai or FakeAi(),
        photo_loader=fake_photo_loader,
    )


def post(
    app: FastAPI,
    path: str,
    payload: dict[str, object],
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.post(path, headers=headers, json=payload)

    return asyncio.run(send())


def request(app: FastAPI, method: str, path: str, **kwargs: object) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_requires_service_token() -> None:
    response = post(build_app(), "/v1/tasks/generate", {"message": "태스크 생성"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_ai_busy_returns_retryable_429() -> None:
    response = post(
        build_app(BusyAi()),
        "/v1/tasks/generate",
        {"message": "오픈 업무를 만들어줘"},
        AUTH,
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "3"
    assert response.json() == {
        "error": {
            "code": "AI_BUSY",
            "message": "현재 AI 요청이 많습니다. 잠시 후 다시 시도해 주세요.",
        }
    }


def test_health_reports_ready_only_when_active_provider_has_a_key() -> None:
    ready = request(build_app(), "GET", "/healthz")
    not_ready_app = create_app(
        Settings(service_token=TOKEN),
        ai=FakeAi(),
        photo_loader=fake_photo_loader,
    )
    not_ready = request(not_ready_app, "GET", "/healthz")

    assert ready.status_code == 200
    assert ready.json() == {"status": "ok"}
    assert not_ready.status_code == 503
    assert not_ready.json() == {"status": "not_ready"}


def test_generates_photo_and_check_tasks() -> None:
    response = post(
        build_app(),
        "/v1/tasks/generate",
        {"message": "오픈 업무를 만들어줘"},
        AUTH,
    )
    assert response.status_code == 200
    assert [task["completionType"] for task in response.json()["tasks"]] == [
        "PHOTO",
        "CHECK",
    ]


def test_answers_a_question_from_store_information() -> None:
    response = post(
        build_app(),
        "/v1/knowledge/answer",
        {
            "information": "오늘 신메뉴 시식 행사는 오후 6시에 시작합니다.",
            "question": "오늘 행사는 몇 시에 시작해?",
        },
        AUTH,
    )

    assert response.status_code == 200
    assert response.json() == {"answer": "오늘 행사는 오후 6시에 시작합니다."}


def test_knowledge_answer_rejects_unknown_request_fields() -> None:
    response = post(
        build_app(),
        "/v1/knowledge/answer",
        {"information": "공지", "question": "질문", "unexpected": True},
        AUTH,
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "INVALID_REQUEST",
            "message": "지원하지 않는 필드입니다: unexpected.",
            "field": "unexpected",
        }
    }


def test_knowledge_answer_enforces_information_and_question_limits() -> None:
    app = build_app()
    maximum_question = post(
        app,
        "/v1/knowledge/answer",
        {"information": "정보", "question": "가" * 10_000},
        AUTH,
    )
    too_much_information = post(
        app,
        "/v1/knowledge/answer",
        {"information": "가" * 60_001, "question": "질문"},
        AUTH,
    )
    too_long_question = post(
        app,
        "/v1/knowledge/answer",
        {"information": "정보", "question": "가" * 10_001},
        AUTH,
    )

    assert maximum_question.status_code == 200
    assert too_much_information.status_code == 400
    assert too_much_information.json() == {
        "error": {
            "code": "INVALID_REQUEST",
            "message": (
                "information 필드는 최대 60,000자까지 허용됩니다. 현재 60,001자입니다."
            ),
            "field": "information",
        }
    }
    assert too_long_question.status_code == 400
    assert too_long_question.json() == {
        "error": {
            "code": "INVALID_REQUEST",
            "message": (
                "question 필드는 최대 10,000자까지 허용됩니다. 현재 10,001자입니다."
            ),
            "field": "question",
        }
    }


def test_validation_error_identifies_missing_required_field() -> None:
    response = post(
        build_app(),
        "/v1/knowledge/answer",
        {"information": "공지"},
        AUTH,
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "INVALID_REQUEST",
            "message": "question 필드가 필요합니다.",
            "field": "question",
        }
    }


def test_validation_error_explains_malformed_json() -> None:
    response = request(
        build_app(),
        "POST",
        "/v1/knowledge/answer",
        headers={**AUTH, "Content-Type": "application/json"},
        content=b'{"information":"notice","question":',
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "INVALID_REQUEST",
            "message": "요청 본문이 올바른 JSON 형식이 아닙니다.",
        }
    }


def test_request_log_contains_status_and_duration_without_logging_itself() -> None:
    app = build_app()
    generated = post(
        app,
        "/v1/tasks/generate",
        {"message": "오픈 업무를 만들어줘"},
        AUTH,
    )
    assert generated.status_code == 200

    response = request(app, "GET", "/v1/admin/requests", headers=AUTH)

    assert response.status_code == 200
    assert len(response.json()["requests"]) == 1
    entry = response.json()["requests"][0]
    assert entry["method"] == "POST"
    assert entry["path"] == "/v1/tasks/generate"
    assert entry["clientAddress"] == "127.0.0.1"
    assert entry["statusCode"] == 200
    assert entry["durationMs"] >= 0
    assert entry["outcome"] == "TASKS_GENERATED"
    assert entry["taskCount"] == 2
    assert entry["errorCode"] == ""
    assert json.loads(entry["requestPayload"]) == {"message": "오픈 업무를 만들어줘"}
    assert json.loads(entry["responsePayload"]) == generated.json()
    assert entry["requestPayloadTruncated"] is False
    assert entry["responsePayloadTruncated"] is False


def test_request_logging_can_be_disabled_without_deleting_existing_logs() -> None:
    app = build_app()
    first = post(
        app,
        "/v1/tasks/generate",
        {"message": "첫 요청"},
        AUTH,
    )
    assert first.status_code == 200

    update = request(
        app,
        "PUT",
        "/v1/admin/settings",
        headers=AUTH,
        json={
            "provider": "OPENROUTER",
            "model": "openai/gpt-5.6-luna",
            "prompts": {
                "taskGeneration": "태스크 생성 프롬프트",
                "photoCheck": "사진 검증 프롬프트",
            },
            "requestLogsEnabled": False,
        },
    )
    assert update.status_code == 200

    second = post(
        app,
        "/v1/tasks/generate",
        {"message": "두 번째 요청"},
        AUTH,
    )
    assert second.status_code == 200

    logs = request(app, "GET", "/v1/admin/requests", headers=AUTH).json()["requests"]
    assert [entry["path"] for entry in logs] == ["/v1/tasks/generate"]


def test_rejects_unknown_request_fields() -> None:
    response = post(
        build_app(),
        "/v1/tasks/generate",
        {"message": "오픈 업무를 만들어줘", "unexpected": True},
        AUTH,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_checks_a_photo() -> None:
    response = post(
        build_app(),
        "/v1/attempts/check",
        {
            "task": {
                "title": "POS 전원 확인",
                "instruction": "POS 화면을 촬영해 주세요.",
                "rule": "POS 화면이 켜져 있어야 한다.",
            },
            "photo": PHOTO,
        },
        AUTH,
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "RETAKE",
        "reason": "POS가 보이지 않습니다.",
        "fix": "POS가 보이게 촬영해 주세요.",
    }


def test_photo_check_exposes_actual_provider_model_and_cache_status() -> None:
    class TracedAi(FakeAi):
        async def check_attempt(
            self,
            task: CheckableTask,
            photo_data_url: str,
            reference_photo_data_url: str | None = None,
        ) -> RetakeResponse:
            update_request_trace(
                provider="CEREBRAS",
                model="gemma-4-31b",
                fallback_provider="OPENROUTER",
                fallback_model="openai/gpt-5.6-luna",
                cache_status="MISS",
            )
            return await super().check_attempt(
                task,
                photo_data_url,
                reference_photo_data_url,
            )

    response = post(
        build_app(TracedAi()),
        "/v1/attempts/check",
        {
            "task": {
                "title": "POS 전원 확인",
                "instruction": "POS 화면을 촬영해 주세요.",
                "rule": "POS 화면이 켜져 있어야 한다.",
            },
            "photo": PHOTO,
        },
        AUTH,
    )

    assert response.status_code == 200
    assert response.headers["X-AI-Provider"] == "OPENROUTER"
    assert response.headers["X-AI-Model"] == "openai/gpt-5.6-luna"
    assert response.headers["X-AI-Cache-Status"] == "MISS"


def test_checks_a_photo_with_an_optional_reference_photo() -> None:
    class CapturingAi(FakeAi):
        reference_photo_data_url: str | None = None

        async def check_attempt(
            self,
            task: CheckableTask,
            photo_data_url: str,
            reference_photo_data_url: str | None = None,
        ) -> RetakeResponse:
            self.reference_photo_data_url = reference_photo_data_url
            return await super().check_attempt(
                task, photo_data_url, reference_photo_data_url
            )

    ai = CapturingAi()
    response = post(
        build_app(ai),
        "/v1/attempts/check",
        {
            "task": {
                "title": "POS 전원 확인",
                "instruction": "POS 화면을 촬영해 주세요.",
                "rule": "POS 화면이 모범 사진과 같은 상태여야 한다.",
            },
            "photo": PHOTO,
            "referencePhoto": REFERENCE_PHOTO,
        },
        AUTH,
    )

    assert response.status_code == 200
    assert ai.reference_photo_data_url is not None
    assert ai.reference_photo_data_url.startswith("data:image/jpeg;base64,")


def test_json_photos_are_downloaded_in_parallel() -> None:
    active_loads = 0
    max_active_loads = 0

    async def delayed_loader(photo: PhotoInput) -> str:
        nonlocal active_loads, max_active_loads
        active_loads += 1
        max_active_loads = max(max_active_loads, active_loads)
        try:
            await asyncio.sleep(0.02)
            return verify_photo_bytes(photo, PHOTO_BYTES)
        finally:
            active_loads -= 1

    app = create_app(
        Settings(openrouter_api_key="test-key", service_token=TOKEN),
        ai=FakeAi(),
        photo_loader=delayed_loader,
    )
    response = post(
        app,
        "/v1/attempts/check",
        {
            "task": {
                "title": "POS",
                "instruction": "촬영",
                "rule": "모범 사진과 같은 상태여야 한다.",
            },
            "photo": PHOTO,
            "referencePhoto": REFERENCE_PHOTO,
        },
        AUTH,
    )

    assert response.status_code == 200
    assert max_active_loads == 2


def test_reference_photo_download_is_reused_by_sha256() -> None:
    photo_loads = 0
    reference_loads = 0

    async def counting_loader(photo: PhotoInput) -> str:
        nonlocal photo_loads, reference_loads
        if "reference" in str(photo.url):
            reference_loads += 1
        else:
            photo_loads += 1
        return verify_photo_bytes(photo, PHOTO_BYTES)

    app = create_app(
        Settings(openrouter_api_key="test-key", service_token=TOKEN),
        ai=FakeAi(),
        photo_loader=counting_loader,
    )
    payload = {
        "task": {
            "title": "POS",
            "instruction": "촬영",
            "rule": "모범 사진과 같은 상태여야 한다.",
        },
        "photo": PHOTO,
        "referencePhoto": REFERENCE_PHOTO,
    }

    assert post(app, "/v1/attempts/check", payload, AUTH).status_code == 200
    assert post(app, "/v1/attempts/check", payload, AUTH).status_code == 200
    assert photo_loads == 2
    assert reference_loads == 1


def test_checks_a_directly_uploaded_photo() -> None:
    app = build_app()
    response = request(
        app,
        "POST",
        "/v1/attempts/check",
        headers=AUTH,
        data={
            "task": '{"title":"POS","instruction":"촬영","rule":"POS가 켜져 있어야 한다."}'
        },
        files={"photo": ("pos.jpg", PHOTO_BYTES, "image/jpeg")},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "RETAKE"
    logs = request(app, "GET", "/v1/admin/requests", headers=AUTH).json()["requests"]
    logged_request = json.loads(logs[0]["requestPayload"])
    assert logged_request["photo"] == {
        "filename": "pos.jpg",
        "contentType": "image/jpeg",
        "sizeBytes": len(PHOTO_BYTES),
        "content": "[BINARY OMITTED]",
    }
    assert json.loads(logs[0]["responsePayload"]) == response.json()


def test_photo_log_redacts_signed_url_query() -> None:
    app = build_app()
    signed_photo = {
        **PHOTO,
        "url": "https://storage.example.com/photo.jpg?token=secret#private",
    }

    response = post(
        app,
        "/v1/attempts/check",
        {
            "task": {
                "title": "POS",
                "instruction": "촬영",
                "rule": "POS가 켜져 있어야 한다.",
            },
            "photo": signed_photo,
        },
        AUTH,
    )

    assert response.status_code == 200
    logs = request(app, "GET", "/v1/admin/requests", headers=AUTH).json()["requests"]
    logged_url = json.loads(logs[0]["requestPayload"])["photo"]["url"]
    assert logged_url == "https://storage.example.com/photo.jpg?[REDACTED]"
    assert "secret" not in logs[0]["requestPayload"]


def test_photo_log_includes_a_viewable_preview() -> None:
    source = BytesIO()
    Image.new("RGB", (1_000, 700), "teal").save(source, format="JPEG", quality=90)
    photo_data_url = verified_photo_data_url("image/jpeg", source.getvalue())

    async def preview_loader(_photo: PhotoInput) -> str:
        return photo_data_url

    app = create_app(
        Settings(openrouter_api_key="test-key", service_token=TOKEN),
        ai=FakeAi(),
        photo_loader=preview_loader,
    )
    response = post(
        app,
        "/v1/attempts/check",
        {
            "task": {
                "title": "POS",
                "instruction": "촬영",
                "rule": "POS가 켜져 있어야 한다.",
            },
            "photo": PHOTO,
        },
        AUTH,
    )

    assert response.status_code == 200
    logs = request(app, "GET", "/v1/admin/requests", headers=AUTH).json()["requests"]
    assert logs[0]["requestPhotoPreview"].startswith("data:image/jpeg;base64,")
    assert logs[0]["referencePhotoPreview"] == ""


def test_checks_a_direct_upload_with_a_reference_photo() -> None:
    class CapturingAi(FakeAi):
        received_reference = False

        async def check_attempt(
            self,
            task: CheckableTask,
            photo_data_url: str,
            reference_photo_data_url: str | None = None,
        ) -> RetakeResponse:
            self.received_reference = reference_photo_data_url is not None
            return await super().check_attempt(
                task, photo_data_url, reference_photo_data_url
            )

    ai = CapturingAi()
    response = request(
        build_app(ai),
        "POST",
        "/v1/attempts/check",
        headers=AUTH,
        data={
            "task": '{"title":"POS","instruction":"촬영","rule":"모범 사진과 같은 상태여야 한다."}'
        },
        files={
            "photo": ("pos.jpg", PHOTO_BYTES, "image/jpeg"),
            "referencePhoto": ("reference.jpg", PHOTO_BYTES, "image/jpeg"),
        },
    )

    assert response.status_code == 200
    assert ai.received_reference is True


def test_local_frontend_cors_preflight() -> None:
    response = request(
        build_app(),
        "OPTIONS",
        "/v1/tasks/generate",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_rejects_invalid_sha256() -> None:
    response = post(
        build_app(),
        "/v1/attempts/check",
        {
            "task": {
                "title": "POS",
                "instruction": "촬영",
                "rule": "POS가 켜져 있어야 한다.",
            },
            "photo": {**PHOTO, "sha256": "bad"},
        },
        AUTH,
    )
    assert response.status_code == 400


def test_photo_integrity_failure_is_422() -> None:
    async def broken_loader(_photo: PhotoInput) -> str:
        raise PhotoUnavailableError

    app = create_app(
        Settings(openrouter_api_key="test-key", service_token=TOKEN),
        ai=FakeAi(),
        photo_loader=broken_loader,
    )
    response = post(
        app,
        "/v1/attempts/check",
        {
            "task": {
                "title": "POS",
                "instruction": "촬영",
                "rule": "POS가 켜져 있어야 한다.",
            },
            "photo": PHOTO,
        },
        AUTH,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PHOTO_UNAVAILABLE"
    assert response.json()["error"]["field"] == "photo"


def test_reference_photo_integrity_failure_identifies_the_field() -> None:
    async def reference_broken_loader(photo: PhotoInput) -> str:
        if "reference" in str(photo.url):
            raise PhotoUnavailableError
        return verify_photo_bytes(photo, PHOTO_BYTES)

    app = create_app(
        Settings(openrouter_api_key="test-key", service_token=TOKEN),
        ai=FakeAi(),
        photo_loader=reference_broken_loader,
    )
    response = post(
        app,
        "/v1/attempts/check",
        {
            "task": {
                "title": "POS",
                "instruction": "촬영",
                "rule": "POS가 모범 사진과 같은 상태여야 한다.",
            },
            "photo": PHOTO,
            "referencePhoto": REFERENCE_PHOTO,
        },
        AUTH,
    )

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "PHOTO_UNAVAILABLE",
        "message": "모범 사진을 불러오거나 확인할 수 없습니다.",
        "field": "referencePhoto",
    }


def test_pass_response_omits_fix() -> None:
    class PassingAi(FakeAi):
        async def check_attempt(
            self,
            _task: CheckableTask,
            _photo_data_url: str,
            _reference_photo_data_url: str | None = None,
        ) -> PassResponse:
            return PassResponse(status="PASS", reason="POS 화면이 켜져 있습니다.")

    app = create_app(
        Settings(openrouter_api_key="test-key", service_token=TOKEN),
        ai=PassingAi(),
        photo_loader=fake_photo_loader,
    )
    response = post(
        app,
        "/v1/attempts/check",
        {
            "task": {
                "title": "POS",
                "instruction": "촬영",
                "rule": "POS가 켜져 있어야 한다.",
            },
            "photo": PHOTO,
        },
        AUTH,
    )
    assert response.status_code == 200
    assert response.json() == {"status": "PASS", "reason": "POS 화면이 켜져 있습니다."}


def test_ai_failure_is_503() -> None:
    class BrokenAi(FakeAi):
        async def generate_tasks(self, _message: str) -> TaskGenerationResponse:
            raise AiUnavailableError

    app = create_app(
        Settings(openrouter_api_key="test-key", service_token=TOKEN),
        ai=BrokenAi(),
        photo_loader=fake_photo_loader,
    )
    response = post(
        app,
        "/v1/tasks/generate",
        {"message": "오픈 업무를 만들어줘"},
        AUTH,
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AI_UNAVAILABLE"

    logs = request(app, "GET", "/v1/admin/requests", headers=AUTH).json()["requests"]
    assert logs[0]["outcome"] == "ERROR"
    assert logs[0]["errorCode"] == "AI_UNAVAILABLE"
    assert json.loads(logs[0]["requestPayload"]) == {"message": "오픈 업무를 만들어줘"}
    assert json.loads(logs[0]["responsePayload"]) == response.json()


def test_admin_settings_do_not_expose_provider_or_service_keys() -> None:
    response = request(
        build_app(),
        "GET",
        "/v1/admin/settings",
        headers=AUTH,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "OPENROUTER"
    assert payload["model"] == "openai/gpt-5.6-luna"
    assert payload["openrouterModels"] == ["openai/gpt-5.6-luna"]
    assert payload["fallbackModel"] == "openai/gpt-5.6-luna"
    assert payload["cacheHitsEnabled"] is True
    assert payload["cacheTtlSeconds"] == 86_400
    assert payload["fallbackEnabled"] is True
    assert payload["requestLogsEnabled"] is True
    assert payload["providerKeys"] == {
        "openrouter": True,
        "cerebras": False,
    }
    assert (
        payload["effectivePrompts"]["taskGeneration"]
        == payload["prompts"]["taskGeneration"]
    )
    assert payload["prompts"]["photoCheck"] in payload["effectivePrompts"]["photoCheck"]
    assert "GS25" not in payload["effectivePrompts"]["photoCheck"]
    assert "GS25" in payload["effectivePrompts"]["photoCheckWithReference"]
    assert "information" in payload["effectivePrompts"]["knowledgeAnswer"]
    assert "serviceToken" not in payload
    assert "apiKey" not in str(payload)


def test_admin_can_switch_model_prompts_and_rotate_service_token() -> None:
    app = create_app(
        Settings(
            service_token=TOKEN,
            openrouter_api_key="test-key",
            cerebras_api_key="test-cerebras-key",
        ),
        ai=FakeAi(),
        photo_loader=fake_photo_loader,
    )
    new_token = "new-test-service-token-123456"
    response = request(
        app,
        "PUT",
        "/v1/admin/settings",
        headers=AUTH,
        json={
            "provider": "CEREBRAS",
            "model": "gemma-4-31b",
            "prompts": {
                "taskGeneration": "태스크 생성용 새 프롬프트",
                "photoCheck": "사진 검증용 새 프롬프트",
            },
            "openrouterModels": [
                "openai/gpt-5.6-luna",
                "google/gemini-2.5-flash",
            ],
            "fallbackModel": "google/gemini-2.5-flash",
            "cacheHitsEnabled": False,
            "cacheTtlSeconds": 21_600,
            "fallbackEnabled": False,
            "requestLogsEnabled": False,
            "newServiceToken": new_token,
        },
    )

    assert response.status_code == 200
    assert response.json()["provider"] == "CEREBRAS"
    assert response.json()["openrouterModels"][-1] == "google/gemini-2.5-flash"
    assert response.json()["fallbackModel"] == "google/gemini-2.5-flash"
    assert response.json()["prompts"]["photoCheck"] == "사진 검증용 새 프롬프트"
    assert (
        "응답은 설명이나 코드 블록 없이"
        in response.json()["effectivePrompts"]["taskGeneration"]
    )
    assert (
        "응답은 설명이나 코드 블록 없이"
        in response.json()["effectivePrompts"]["photoCheck"]
    )
    assert response.json()["cacheHitsEnabled"] is False
    assert response.json()["cacheTtlSeconds"] == 21_600
    assert response.json()["fallbackEnabled"] is False
    assert response.json()["requestLogsEnabled"] is False
    assert request(app, "GET", "/v1/admin/settings", headers=AUTH).status_code == 401
    assert (
        request(
            app,
            "GET",
            "/v1/admin/settings",
            headers={"Authorization": f"Bearer {new_token}"},
        ).status_code
        == 200
    )


def test_settings_log_redacts_rotated_service_token() -> None:
    app = build_app()
    new_token = "new-test-service-token-123456"
    response = request(
        app,
        "PUT",
        "/v1/admin/settings",
        headers=AUTH,
        json={
            "provider": "OPENROUTER",
            "model": "openai/gpt-5.6-luna",
            "prompts": {
                "taskGeneration": "태스크 생성 프롬프트",
                "photoCheck": "사진 검증 프롬프트",
            },
            "newServiceToken": new_token,
        },
    )

    assert response.status_code == 200
    rotated_auth = {"Authorization": f"Bearer {new_token}"}
    logs = request(app, "GET", "/v1/admin/requests", headers=rotated_auth).json()[
        "requests"
    ]
    settings_update = next(entry for entry in logs if entry["method"] == "PUT")
    assert new_token not in settings_update["requestPayload"]
    assert (
        json.loads(settings_update["requestPayload"])["newServiceToken"] == "[REDACTED]"
    )


def test_admin_rejects_non_vision_model() -> None:
    response = request(
        build_app(),
        "PUT",
        "/v1/admin/settings",
        headers=AUTH,
        json={
            "provider": "CEREBRAS",
            "model": "text-only-model",
            "prompts": {
                "taskGeneration": "태스크 생성 프롬프트",
                "photoCheck": "사진 검증 프롬프트",
            },
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_admin_can_add_select_and_remove_openrouter_models() -> None:
    app = build_app()
    prompts = {
        "taskGeneration": "태스크 생성 프롬프트",
        "photoCheck": "사진 검증 프롬프트",
    }
    added = request(
        app,
        "PUT",
        "/v1/admin/settings",
        headers=AUTH,
        json={
            "provider": "OPENROUTER",
            "model": "google/gemini-2.5-flash",
            "prompts": prompts,
            "openrouterModels": [
                "openai/gpt-5.6-luna",
                "google/gemini-2.5-flash",
            ],
        },
    )

    assert added.status_code == 200
    assert added.json()["model"] == "google/gemini-2.5-flash"
    assert [
        item["id"]
        for item in added.json()["availableModels"]
        if item["provider"] == "OPENROUTER"
    ] == ["openai/gpt-5.6-luna", "google/gemini-2.5-flash"]

    active_removal = request(
        app,
        "PUT",
        "/v1/admin/settings",
        headers=AUTH,
        json={
            "provider": "OPENROUTER",
            "model": "google/gemini-2.5-flash",
            "prompts": prompts,
            "openrouterModels": ["openai/gpt-5.6-luna"],
        },
    )
    assert active_removal.status_code == 400

    removed = request(
        app,
        "PUT",
        "/v1/admin/settings",
        headers=AUTH,
        json={
            "provider": "OPENROUTER",
            "model": "openai/gpt-5.6-luna",
            "prompts": prompts,
            "openrouterModels": ["openai/gpt-5.6-luna"],
        },
    )
    assert removed.status_code == 200
    assert removed.json()["openrouterModels"] == ["openai/gpt-5.6-luna"]


def test_admin_rejects_removing_or_selecting_unregistered_fallback_model() -> None:
    app = create_app(
        Settings(
            service_token=TOKEN,
            openrouter_api_key="test-key",
            cerebras_api_key="test-cerebras-key",
        ),
        ai=FakeAi(),
        photo_loader=fake_photo_loader,
    )
    prompts = {
        "taskGeneration": "태스크 생성 프롬프트",
        "photoCheck": "사진 검증 프롬프트",
    }
    configured = request(
        app,
        "PUT",
        "/v1/admin/settings",
        headers=AUTH,
        json={
            "provider": "CEREBRAS",
            "model": "gemma-4-31b",
            "prompts": prompts,
            "openrouterModels": [
                "openai/gpt-5.6-luna",
                "google/gemini-2.5-flash",
            ],
            "fallbackModel": "google/gemini-2.5-flash",
        },
    )
    assert configured.status_code == 200

    removed = request(
        app,
        "PUT",
        "/v1/admin/settings",
        headers=AUTH,
        json={
            "provider": "CEREBRAS",
            "model": "gemma-4-31b",
            "prompts": prompts,
            "openrouterModels": ["openai/gpt-5.6-luna"],
        },
    )
    assert removed.status_code == 400

    unknown = request(
        app,
        "PUT",
        "/v1/admin/settings",
        headers=AUTH,
        json={
            "provider": "CEREBRAS",
            "model": "gemma-4-31b",
            "prompts": prompts,
            "openrouterModels": ["openai/gpt-5.6-luna"],
            "fallbackModel": "google/gemini-2.5-flash",
        },
    )
    assert unknown.status_code == 400
