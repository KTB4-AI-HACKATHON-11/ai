from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from starlette.datastructures import UploadFile
from starlette.responses import Response

from .concurrency import AiBusyError
from .config import LUNA_MODEL, Settings
from .luna import AiOperations, AiUnavailableError, ConfigurableAiOperations
from .photo import (
    PhotoUnavailableError,
    ReferencePhotoCache,
    SharedPhotoDownloader,
    load_uploaded_photo,
    photo_log_preview,
)
from .prompts import (
    KNOWLEDGE_ANSWER_PROMPT,
    TASK_GENERATION_JSON_CONTRACT,
    photo_check_instructions,
)
from .request_log import RequestLogStore
from .request_trace import (
    begin_request_trace,
    end_request_trace,
    request_trace_snapshot,
    update_request_trace,
)
from .schemas import (
    AdminEffectivePromptSettings,
    AdminPromptSettings,
    AdminSettingsResponse,
    AdminSettingsUpdate,
    AttemptCheckRequest,
    AttemptCheckResponse,
    AvailableModel,
    CheckableTask,
    ErrorResponse,
    KnowledgeAnswerRequest,
    KnowledgeAnswerResponse,
    PhotoInput,
    RequestLogResponse,
    TaskGenerationRequest,
    TaskGenerationResponse,
)
from .settings_store import RuntimeSettingsStore

PhotoLoader = Callable[[PhotoInput], Awaitable[str]]


class InvalidRequestError(Exception):
    def __init__(
        self,
        message: str = "요청 형식이나 값이 잘못되었습니다.",
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.field = field


_SENSITIVE_LOG_FIELDS = {
    "authorization",
    "apikey",
    "cerebrasapikey",
    "newservicetoken",
    "openrouterapikey",
    "servicetoken",
}


def _sanitize_log_value(value: object, field_name: str = "") -> object:
    normalized_field = field_name.replace("_", "").casefold()
    if normalized_field in _SENSITIVE_LOG_FIELDS:
        return "[REDACTED]"
    if isinstance(value, BaseModel):
        return _sanitize_log_value(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {
            str(key): _sanitize_log_value(item, str(key)) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_log_value(item) for item in value]
    if normalized_field == "url" and value is not None:
        raw_url = str(value)
        try:
            parts = urlsplit(raw_url)
        except ValueError:
            return "[INVALID URL]"
        query = "[REDACTED]" if parts.query else ""
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
    if isinstance(value, bytes):
        return f"[BINARY {len(value):,} bytes omitted]"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _serialized_log_payload(value: object) -> str:
    return json.dumps(
        _sanitize_log_value(value),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _record_request_payload(value: object) -> None:
    update_request_trace(request_payload=_serialized_log_payload(value))


def _record_response_payload(value: object) -> None:
    update_request_trace(response_payload=_serialized_log_payload(value))


def _uploaded_file_log_value(file: UploadFile | None) -> object:
    if file is None:
        return None
    return {
        "filename": file.filename,
        "contentType": file.content_type,
        "sizeBytes": file.size,
        "content": "[BINARY OMITTED]",
    }


def _error(
    status_code: int,
    code: str,
    message: str,
    field: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    update_request_trace(outcome="ERROR", error_code=code)
    detail = {"code": code, "message": message}
    if field is not None:
        detail["field"] = field
    payload = {"error": detail}
    _record_response_payload(payload)
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


def _validation_error_detail(
    errors: Sequence[Mapping[str, object]],
) -> tuple[str, str | None]:
    if not errors:
        return "요청 형식이나 값이 잘못되었습니다.", None

    first = errors[0]
    raw_location = first.get("loc")
    location = list(raw_location) if isinstance(raw_location, (list, tuple)) else []
    if location and location[0] == "body":
        location = location[1:]
    field = ".".join(str(part) for part in location) or None
    field_label = f"{field} 필드" if field is not None else "요청 본문"
    error_type = str(first.get("type", ""))
    raw_context = first.get("ctx")
    context = raw_context if isinstance(raw_context, Mapping) else {}
    value = first.get("input")

    if error_type == "json_invalid":
        message = "요청 본문이 올바른 JSON 형식이 아닙니다."
    elif error_type == "missing":
        message = f"{field_label}가 필요합니다."
    elif error_type == "extra_forbidden":
        message = f"지원하지 않는 필드입니다: {field}."
    elif error_type in {"string_too_long", "too_long"}:
        maximum = context.get("max_length")
        unit = "자" if isinstance(value, str) else "개"
        actual = len(value) if isinstance(value, (str, list, tuple, dict)) else None
        message = f"{field_label}는 최대 {int(maximum):,}{unit}까지 허용됩니다."
        if actual is not None:
            message += f" 현재 {actual:,}{unit}입니다."
    elif error_type in {"string_too_short", "too_short"}:
        minimum = context.get("min_length")
        unit = "자" if isinstance(value, str) else "개"
        if minimum == 1:
            message = f"{field_label}를 비워 둘 수 없습니다."
        else:
            message = f"{field_label}는 최소 {int(minimum):,}{unit}가 필요합니다."
    elif error_type in {"string_type", "int_type", "bool_type", "list_type"}:
        expected = {
            "string_type": "문자열",
            "int_type": "정수",
            "bool_type": "참/거짓 값",
            "list_type": "목록",
        }[error_type]
        message = f"{field_label}는 {expected}이어야 합니다."
    elif error_type == "literal_error":
        message = f"{field_label} 값이 허용 범위에 없습니다."
    elif error_type == "string_pattern_mismatch":
        message = f"{field_label} 형식이 올바르지 않습니다."
    elif error_type in {"url_parsing", "url_scheme"}:
        message = f"{field_label}는 올바른 HTTPS URL이어야 합니다."
    elif error_type == "greater_than_equal":
        message = f"{field_label}는 {context.get('ge')} 이상이어야 합니다."
    elif error_type == "less_than_equal":
        message = f"{field_label}는 {context.get('le')} 이하여야 합니다."
    elif error_type == "value_error" and context.get("error") is not None:
        message = str(context["error"])
    else:
        message = f"{field_label} 값이 올바르지 않습니다."

    remaining = len(errors) - 1
    if remaining:
        message += f" 추가 오류가 {remaining}건 있습니다."
    return message, None if error_type == "json_invalid" else field


def create_app(
    settings: Settings,
    ai: AiOperations | None = None,
    photo_loader: PhotoLoader | None = None,
    runtime_store: RuntimeSettingsStore | None = None,
    request_log_store: RequestLogStore | None = None,
) -> FastAPI:
    store = runtime_store or RuntimeSettingsStore(settings)
    logs = request_log_store or RequestLogStore(settings.request_log_path)
    service = ai or ConfigurableAiOperations(settings, store)
    shared_photo_downloader = SharedPhotoDownloader() if photo_loader is None else None
    active_photo_loader = photo_loader or shared_photo_downloader
    reference_photo_cache = ReferencePhotoCache()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            if shared_photo_downloader is not None:
                await shared_photo_downloader.aclose()

    app = FastAPI(
        title="Flowcheck AI Backend",
        version="1.0.0",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_origin_regex=r"^https?://(?:localhost|127\.0\.0\.1)(?::\d+)?$",
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-AI-Provider", "X-AI-Model", "X-AI-Cache-Status"],
    )

    @app.middleware("http")
    async def record_request(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        should_record = (
            request.url.path.startswith("/v1/")
            and request.url.path != "/v1/admin/requests"
            and request.method != "OPTIONS"
        )
        started = time.perf_counter()
        status_code = 500
        trace_token = begin_request_trace()
        try:
            response = await call_next(request)
            status_code = response.status_code
            trace = request_trace_snapshot()
            used_provider = trace.fallback_provider or trace.provider
            used_model = trace.fallback_model or trace.model
            if used_provider:
                response.headers["X-AI-Provider"] = used_provider
            if used_model:
                response.headers["X-AI-Model"] = used_model
            if trace.cache_status:
                response.headers["X-AI-Cache-Status"] = trace.cache_status
            return response
        finally:
            if should_record and store.get().request_logs_enabled:
                trace = request_trace_snapshot()
                logs.append(
                    request.method,
                    request.url.path,
                    status_code,
                    round((time.perf_counter() - started) * 1000),
                    request.client.host if request.client is not None else "",
                    provider=trace.provider,
                    model=trace.model,
                    cache_status=trace.cache_status,
                    fallback_provider=trace.fallback_provider,
                    outcome=trace.outcome,
                    error_code=trace.error_code,
                    task_count=trace.task_count,
                    provider_failure_reason=trace.provider_failure_reason,
                    provider_finish_reason=trace.provider_finish_reason,
                    provider_completion_tokens=trace.provider_completion_tokens,
                    provider_output=trace.provider_output,
                    provider_output_truncated=trace.provider_output_truncated,
                    request_payload=trace.request_payload,
                    request_payload_truncated=trace.request_payload_truncated,
                    response_payload=trace.response_payload,
                    response_payload_truncated=trace.response_payload_truncated,
                    request_photo_preview=trace.request_photo_preview,
                    reference_photo_preview=trace.reference_photo_preview,
                )
            end_request_trace(trace_token)

    async def require_auth(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        prefix = "Bearer "
        if not authorization or not authorization.startswith(prefix):
            raise PermissionError
        if not store.authenticate(authorization[len(prefix) :]):
            raise PermissionError

    def admin_settings_response() -> AdminSettingsResponse:
        current = store.get()
        is_cerebras = current.provider == "CEREBRAS"
        effective_task_prompt = current.task_generation_prompt
        if is_cerebras:
            effective_task_prompt = (
                f"{effective_task_prompt}\n{TASK_GENERATION_JSON_CONTRACT}"
            )
        return AdminSettingsResponse(
            provider=current.provider,
            model=current.model,
            prompts=AdminPromptSettings(
                taskGeneration=current.task_generation_prompt,
                photoCheck=current.photo_check_prompt,
            ),
            openrouterModels=list(current.openrouter_models),
            fallbackModel=current.fallback_model,
            cacheHitsEnabled=current.cache_hits_enabled,
            cacheTtlSeconds=current.cache_ttl_seconds,
            fallbackEnabled=current.fallback_enabled,
            requestLogsEnabled=current.request_logs_enabled,
            providerKeys=store.provider_keys(),
            availableModels=[
                AvailableModel(
                    provider="OPENROUTER",
                    id=model,
                    label=(
                        "GPT-5.6 Luna · OpenRouter"
                        if model == LUNA_MODEL
                        else f"{model} · OpenRouter"
                    ),
                )
                for model in current.openrouter_models
            ]
            + [
                AvailableModel(
                    provider="CEREBRAS",
                    id="gemma-4-31b",
                    label="Gemma 4 31B · Cerebras",
                ),
            ],
            effectivePrompts=AdminEffectivePromptSettings(
                taskGeneration=effective_task_prompt,
                photoCheck=photo_check_instructions(
                    current.photo_check_prompt,
                    has_reference_photo=False,
                    include_json_contract=is_cerebras,
                ),
                photoCheckWithReference=photo_check_instructions(
                    current.photo_check_prompt,
                    has_reference_photo=True,
                    include_json_contract=is_cerebras,
                ),
                knowledgeAnswer=KNOWLEDGE_ANSWER_PROMPT,
            ),
            revision=current.revision,
        )

    async def load_json_photo(photo: PhotoInput, field: str) -> str:
        try:
            if active_photo_loader is None:
                raise PhotoUnavailableError
            return await active_photo_loader(photo)
        except PhotoUnavailableError as error:
            raise PhotoUnavailableError(field) from error

    async def load_reference_json_photo(photo: PhotoInput) -> str:
        return await reference_photo_cache.get_or_load(
            photo,
            lambda: load_json_photo(photo, "referencePhoto"),
        )

    async def load_form_photo(photo: UploadFile, field: str) -> str:
        try:
            return await load_uploaded_photo(photo)
        except PhotoUnavailableError as error:
            raise PhotoUnavailableError(field) from error

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: object, error_value: RequestValidationError
    ) -> JSONResponse:
        if error_value.body is not None:
            _record_request_payload(error_value.body)
        message, field = _validation_error_detail(error_value.errors())
        return _error(400, "INVALID_REQUEST", message, field)

    @app.exception_handler(InvalidRequestError)
    async def invalid_request_handler(
        _request: object, error_value: InvalidRequestError
    ) -> JSONResponse:
        return _error(
            400,
            "INVALID_REQUEST",
            error_value.message,
            error_value.field,
        )

    @app.exception_handler(PermissionError)
    async def auth_error_handler(
        _request: object, _error_value: PermissionError
    ) -> JSONResponse:
        return _error(401, "UNAUTHORIZED", "서비스 인증에 실패했습니다.")

    @app.exception_handler(PhotoUnavailableError)
    async def photo_error_handler(
        _request: object, error_value: PhotoUnavailableError
    ) -> JSONResponse:
        message = (
            "모범 사진을 불러오거나 확인할 수 없습니다."
            if error_value.field == "referencePhoto"
            else "사용자 사진을 불러오거나 확인할 수 없습니다."
        )
        return _error(422, "PHOTO_UNAVAILABLE", message, error_value.field)

    @app.exception_handler(AiUnavailableError)
    async def ai_error_handler(
        _request: object, _error_value: AiUnavailableError
    ) -> JSONResponse:
        return _error(503, "AI_UNAVAILABLE", "AI 처리를 완료하지 못했습니다.")

    @app.exception_handler(AiBusyError)
    async def ai_busy_handler(
        _request: object, error_value: AiBusyError
    ) -> JSONResponse:
        return _error(
            429,
            "AI_BUSY",
            "현재 AI 요청이 많습니다. 잠시 후 다시 시도해 주세요.",
            headers={"Retry-After": str(error_value.retry_after_seconds)},
        )

    errors = {
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    }

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        current = store.get()
        if not store.provider_api_key(current.provider):
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return JSONResponse(content={"status": "ok"})

    @app.post(
        "/v1/tasks/generate",
        response_model=TaskGenerationResponse,
        responses=errors,
        dependencies=[Depends(require_auth)],
    )
    async def generate_tasks(payload: TaskGenerationRequest) -> TaskGenerationResponse:
        _record_request_payload(payload)
        response = await service.generate_tasks(payload.message)
        update_request_trace(outcome="TASKS_GENERATED", task_count=len(response.tasks))
        _record_response_payload(response)
        return response

    @app.post(
        "/v1/knowledge/answer",
        response_model=KnowledgeAnswerResponse,
        responses=errors,
        dependencies=[Depends(require_auth)],
    )
    async def answer_knowledge(
        payload: KnowledgeAnswerRequest,
    ) -> KnowledgeAnswerResponse:
        _record_request_payload(payload)
        response = await service.answer_knowledge(payload.information, payload.question)
        update_request_trace(outcome="KNOWLEDGE_ANSWERED")
        _record_response_payload(response)
        return response

    @app.post(
        "/v1/attempts/check",
        response_model=AttemptCheckResponse,
        response_model_exclude_none=True,
        responses={**errors, 422: {"model": ErrorResponse}},
        dependencies=[Depends(require_auth)],
    )
    async def check_attempt(request: Request) -> AttemptCheckResponse:
        media_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        if media_type == "application/json":
            try:
                raw_payload = await request.json()
                _record_request_payload(raw_payload)
                payload = AttemptCheckRequest.model_validate(raw_payload)
            except ValidationError as error:
                message, field = _validation_error_detail(error.errors())
                raise InvalidRequestError(message, field) from error
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise InvalidRequestError(
                    "요청 본문이 올바른 JSON 형식이 아닙니다."
                ) from error
            task = payload.task
            if payload.referencePhoto is None:
                photo_data_url = await load_json_photo(payload.photo, "photo")
                reference_photo_data_url = None
            else:
                photo_data_url, reference_photo_data_url = await asyncio.gather(
                    load_json_photo(payload.photo, "photo"),
                    load_reference_json_photo(payload.referencePhoto),
                )
        elif media_type == "multipart/form-data":
            try:
                form = await request.form()
                task_json = form.get("task")
                photo = form.get("photo")
                reference_photo = form.get("referencePhoto")
                try:
                    task_log_value = (
                        json.loads(task_json)
                        if isinstance(task_json, str)
                        else task_json
                    )
                except json.JSONDecodeError:
                    task_log_value = task_json
                _record_request_payload(
                    {
                        "task": task_log_value,
                        "photo": _uploaded_file_log_value(
                            photo if isinstance(photo, UploadFile) else None
                        ),
                        "referencePhoto": _uploaded_file_log_value(
                            reference_photo
                            if isinstance(reference_photo, UploadFile)
                            else None
                        ),
                    }
                )
                if not isinstance(task_json, str):
                    raise InvalidRequestError("task 필드가 필요합니다.", "task")
                if not isinstance(photo, UploadFile):
                    raise InvalidRequestError("photo 파일이 필요합니다.", "photo")
                if reference_photo is not None and not isinstance(
                    reference_photo, UploadFile
                ):
                    raise InvalidRequestError(
                        "referencePhoto는 파일이어야 합니다.", "referencePhoto"
                    )
                task = CheckableTask.model_validate_json(task_json)
            except ValidationError as error:
                message, field = _validation_error_detail(error.errors())
                nested_field = f"task.{field}" if field else "task"
                raise InvalidRequestError(message, nested_field) from error
            except (ValueError, UnicodeDecodeError) as error:
                raise InvalidRequestError(
                    "task 필드가 올바른 JSON 형식이 아닙니다.", "task"
                ) from error
            if isinstance(reference_photo, UploadFile):
                photo_data_url, reference_photo_data_url = await asyncio.gather(
                    load_form_photo(photo, "photo"),
                    load_form_photo(reference_photo, "referencePhoto"),
                )
            else:
                photo_data_url = await load_form_photo(photo, "photo")
                reference_photo_data_url = None
        else:
            _record_request_payload({"contentType": media_type or "[missing]"})
            raise InvalidRequestError(
                "Content-Type은 application/json 또는 multipart/form-data여야 합니다."
            )
        request_preview, reference_preview = await asyncio.gather(
            asyncio.to_thread(photo_log_preview, photo_data_url),
            asyncio.to_thread(photo_log_preview, reference_photo_data_url)
            if reference_photo_data_url is not None
            else asyncio.sleep(0, result=""),
        )
        update_request_trace(
            request_photo_preview=request_preview,
            reference_photo_preview=reference_preview,
        )
        response = await service.check_attempt(
            task, photo_data_url, reference_photo_data_url
        )
        update_request_trace(outcome=response.status)
        _record_response_payload(response.model_dump(mode="json", exclude_none=True))
        return response

    @app.get(
        "/v1/admin/requests",
        response_model=RequestLogResponse,
        responses={401: {"model": ErrorResponse}},
        dependencies=[Depends(require_auth)],
    )
    async def get_request_logs() -> RequestLogResponse:
        return RequestLogResponse(requests=[asdict(entry) for entry in logs.recent()])

    @app.get(
        "/v1/admin/settings",
        response_model=AdminSettingsResponse,
        responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
        dependencies=[Depends(require_auth)],
    )
    async def get_admin_settings() -> AdminSettingsResponse:
        response = admin_settings_response()
        update_request_trace(
            provider=response.provider,
            model=response.model,
            outcome="SETTINGS_READ",
        )
        _record_response_payload(response)
        return response

    @app.put(
        "/v1/admin/settings",
        response_model=AdminSettingsResponse,
        responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
        dependencies=[Depends(require_auth)],
    )
    async def update_admin_settings(
        payload: AdminSettingsUpdate,
    ) -> AdminSettingsResponse:
        _record_request_payload(payload)
        try:
            store.update(payload)
        except ValueError as error:
            raise InvalidRequestError(str(error)) from error
        except OSError as error:
            raise InvalidRequestError("서버 설정을 저장하지 못했습니다.") from error
        response = admin_settings_response()
        update_request_trace(
            provider=response.provider,
            model=response.model,
            outcome="SETTINGS_UPDATED",
        )
        _record_response_payload(response)
        return response

    return app
