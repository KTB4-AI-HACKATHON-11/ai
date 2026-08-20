from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Protocol, cast

from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from .concurrency import AiRequestLimiter
from .config import LUNA_MODEL, Settings
from .photo import PhotoUnavailableError, normalize_cerebras_photo
from .prompts import (
    CEREBRAS_TASK_GENERATION_FORMAT_CORRECTION,
    GROUP_AGENT_FORMAT_CORRECTION,
    GROUP_AGENT_PROMPT,
    KNOWLEDGE_ANSWER_PROMPT,
    PHOTO_CHECK_FIX_CORRECTION,
    PHOTO_CHECK_FORMAT_CORRECTION,
    PHOTO_CHECK_PROMPT,
    TASK_GENERATION_DECISION_CONTRACT,
    TASK_GENERATION_FORMAT_CORRECTION,
    TASK_GENERATION_JSON_CONTRACT,
    TASK_GENERATION_PROMPT,
    instructions,
    photo_check_instructions,
)
from .request_trace import update_request_trace
from .schemas import (
    AttemptCheckResponse,
    CheckableTask,
    GroupAgentRequest,
    GroupAgentResponse,
    KnowledgeAnswerResponse,
    ModelAttemptCheckResponse,
    ModelCerebrasTaskGenerationResponse,
    ModelKnowledgeAnswerResponse,
    ModelTaskGenerationResponse,
    PassResponse,
    RetakeResponse,
    TaskGenerationResponse,
)
from .settings_store import RuntimeSettings, RuntimeSettingsStore

logger = logging.getLogger(__name__)

LUNA_TIMEOUT_SECONDS = 20
CEREBRAS_TIMEOUT_SECONDS = 8
KNOWLEDGE_TIMEOUT_SECONDS = 30
CEREBRAS_TASK_MAX_COMPLETION_TOKENS = 8_192
CEREBRAS_KNOWLEDGE_MAX_COMPLETION_TOKENS = 2_048
CEREBRAS_PHOTO_MAX_COMPLETION_TOKENS = 512
LUNA_KNOWLEDGE_MAX_OUTPUT_TOKENS = 2_048
LUNA_AGENT_MAX_OUTPUT_TOKENS = 32_768
CEREBRAS_AGENT_MAX_COMPLETION_TOKENS = 32_768
AGENT_TIMEOUT_SECONDS = 20
TASK_GENERATION_CACHE_MAX_ENTRIES = 256
TASK_GENERATION_CACHE_TTL_SECONDS = 24 * 60 * 60
ATTEMPT_CHECK_CACHE_MAX_ENTRIES = 1_024
ATTEMPT_CHECK_CACHE_TTL_SECONDS = 24 * 60 * 60
MAX_LOGGED_PROVIDER_OUTPUT_CHARS = 64_000
CEREBRAS_TASK_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "instruction": {"type": "string"},
        "completionType": {
            "type": "string",
            "enum": ["PHOTO", "CHECK"],
        },
        "rule": {
            "anyOf": [
                {"type": "string"},
                {"type": "null"},
            ]
        },
    },
    "required": [
        "title",
        "instruction",
        "completionType",
        "rule",
    ],
    "additionalProperties": False,
}
CEREBRAS_TASK_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "task_generation",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "decision": {
                    "type": "string",
                    "enum": ["GENERATE", "REJECT"],
                },
                "reason": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "null"},
                    ]
                },
                "firstTask": {
                    "anyOf": [
                        CEREBRAS_TASK_ITEM_SCHEMA,
                        {"type": "null"},
                    ]
                },
                "additionalTasks": {
                    "type": "array",
                    "items": CEREBRAS_TASK_ITEM_SCHEMA,
                },
            },
            "required": ["decision", "reason", "firstTask", "additionalTasks"],
            "additionalProperties": False,
        },
    },
}
CEREBRAS_AGENT_CHECKLIST_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "instruction": {"type": "string"},
        "completionType": {"type": "string", "enum": ["PHOTO", "CHECK"]},
        "rule": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["title", "instruction", "completionType", "rule"],
    "additionalProperties": False,
}
CEREBRAS_AGENT_STORE_INFO_SCHEMA = {
    "type": "object",
    "properties": {
        "storeInfoId": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "category": {
            "type": "string",
            "enum": ["LOCATION", "PROMOTION", "DELIVERY", "EQUIPMENT", "RULE", "ETC"],
        },
        "title": {"type": "string"},
        "content": {"type": "string"},
    },
    "required": ["storeInfoId", "category", "title", "content"],
    "additionalProperties": False,
}
CEREBRAS_AGENT_TOOL_CALL_PROPERTIES = {
    "callId": {"type": "string"},
    "tool": {
        "type": "string",
        "enum": [
            "CREATE_TASK",
            "UPDATE_TASK",
            "DELETE_TASK",
            "COMPLETE_CHECKLIST",
            "REPLACE_STORE_INFO",
            "SEND_NOTIFICATION",
        ],
    },
    "dependsOnCallIds": {"type": "array", "items": {"type": "string"}},
    "evidenceRefs": {"type": "array", "items": {"type": "string"}},
    "decisionBasis": {"type": "string"},
    "taskId": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    "runId": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    "checklistId": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    "title": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    "sourceMessage": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    "workerId": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    "dueAt": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    "notifyOnCompletion": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
    "active": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
    "checklists": {"type": "array", "items": CEREBRAS_AGENT_CHECKLIST_SCHEMA},
    "storeInfo": {"type": "array", "items": CEREBRAS_AGENT_STORE_INFO_SCHEMA},
    "removedStoreInfoIds": {"type": "array", "items": {"type": "integer"}},
    "recipientMemberIds": {"type": "array", "items": {"type": "integer"}},
    "notificationMessage": {"anyOf": [{"type": "string"}, {"type": "null"}]},
}
CEREBRAS_AGENT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "group_agent_response",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "toolCalls": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": CEREBRAS_AGENT_TOOL_CALL_PROPERTIES,
                        "required": list(CEREBRAS_AGENT_TOOL_CALL_PROPERTIES),
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["message", "toolCalls"],
            "additionalProperties": False,
        },
    },
}


class AiUnavailableError(Exception):
    def __init__(self, reason: str = "unavailable") -> None:
        super().__init__(reason)
        self.reason = reason


class TaskGenerationRejectedError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AiOperations(Protocol):
    async def generate_tasks(self, message: str) -> TaskGenerationResponse: ...

    async def answer_knowledge(
        self, information: str, question: str
    ) -> KnowledgeAnswerResponse: ...

    async def check_attempt(
        self,
        task: CheckableTask,
        photo_data_url: str,
        reference_photo_data_url: str | None = None,
    ) -> AttemptCheckResponse: ...

    async def run_group_agent(
        self, payload: GroupAgentRequest
    ) -> GroupAgentResponse: ...


def _parsed_output(response: object) -> object | None:
    for output in getattr(response, "output", []):
        if getattr(output, "type", None) != "message":
            continue
        for item in getattr(output, "content", []):
            if (
                getattr(item, "type", None) == "output_text"
                and getattr(item, "parsed", None) is not None
            ):
                return item.parsed
    return None


def _provider_error_reason(error: BaseException) -> str:
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int):
        return f"http_{status_code}"
    if isinstance(error, TimeoutError):
        return "timeout"
    return "provider_error"


def _provider_error_output(error: BaseException) -> str:
    body = getattr(error, "body", None)
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    try:
        return json.dumps(body, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(body)


def _record_provider_error(error: BaseException) -> str:
    reason = _provider_error_reason(error)
    output = _provider_error_output(error)
    values: dict[str, str | bool] = {"provider_failure_reason": reason}
    if output:
        values.update(
            provider_output=output[:MAX_LOGGED_PROVIDER_OUTPUT_CHARS],
            provider_output_truncated=(len(output) > MAX_LOGGED_PROVIDER_OUTPUT_CHARS),
        )
    update_request_trace(**values)
    return reason


class LunaOperations:
    def __init__(
        self,
        api_key: str,
        model: str = LUNA_MODEL,
        task_generation_prompt: str = TASK_GENERATION_PROMPT,
        photo_check_prompt: str = PHOTO_CHECK_PROMPT,
    ) -> None:
        self._model = model
        self._task_generation_prompt = task_generation_prompt
        self._photo_check_prompt = photo_check_prompt
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            timeout=LUNA_TIMEOUT_SECONDS,
            max_retries=0,
        )

    async def generate_tasks(self, message: str) -> TaskGenerationResponse:
        correction = ""
        for _ in range(2):
            try:
                response = await self._client.responses.parse(
                    model=self._model,
                    instructions=instructions(
                        f"{self._task_generation_prompt}\n"
                        f"{TASK_GENERATION_DECISION_CONTRACT}",
                        correction,
                    ),
                    input=message,
                    text_format=ModelTaskGenerationResponse,
                )
            except ValidationError:
                correction = TASK_GENERATION_FORMAT_CORRECTION
                continue
            except (OpenAIError, TypeError, ValueError) as error:
                raise AiUnavailableError(_record_provider_error(error)) from error
            parsed = _parsed_output(response)
            try:
                decision = ModelTaskGenerationResponse.model_validate(
                    parsed.model_dump()
                    if isinstance(parsed, ModelTaskGenerationResponse)
                    else parsed
                )
                if decision.decision == "REJECT":
                    raise TaskGenerationRejectedError(decision.reason or "")
                return TaskGenerationResponse.model_validate(
                    {"tasks": [task.model_dump() for task in decision.tasks]}
                )
            except TaskGenerationRejectedError:
                _clear_provider_output_trace()
                raise
            except ValidationError:
                correction = TASK_GENERATION_FORMAT_CORRECTION
        update_request_trace(provider_failure_reason="invalid_model_output")
        raise AiUnavailableError

    async def answer_knowledge(
        self, information: str, question: str
    ) -> KnowledgeAnswerResponse:
        payload = json.dumps(
            {"information": information, "question": question}, ensure_ascii=False
        )
        try:
            response = await self._client.responses.parse(
                model=self._model,
                instructions=KNOWLEDGE_ANSWER_PROMPT,
                input=payload,
                text_format=ModelKnowledgeAnswerResponse,
                timeout=KNOWLEDGE_TIMEOUT_SECONDS,
                max_output_tokens=LUNA_KNOWLEDGE_MAX_OUTPUT_TOKENS,
            )
        except (OpenAIError, ValidationError, TypeError, ValueError) as error:
            raise AiUnavailableError(_record_provider_error(error)) from error
        parsed = _parsed_output(response)
        try:
            return KnowledgeAnswerResponse.model_validate(
                parsed.model_dump()
                if isinstance(parsed, ModelKnowledgeAnswerResponse)
                else parsed
            )
        except ValidationError as error:
            update_request_trace(provider_failure_reason="invalid_model_output")
            raise AiUnavailableError("invalid_model_output") from error

    async def run_group_agent(self, payload: GroupAgentRequest) -> GroupAgentResponse:
        correction = ""
        for _ in range(2):
            try:
                response = await self._client.responses.parse(
                    model=self._model,
                    instructions=instructions(GROUP_AGENT_PROMPT, correction),
                    input=json.dumps(
                        payload.model_dump(mode="json"), ensure_ascii=False
                    ),
                    text_format=GroupAgentResponse,
                    timeout=AGENT_TIMEOUT_SECONDS,
                    max_output_tokens=LUNA_AGENT_MAX_OUTPUT_TOKENS,
                )
            except ValidationError:
                correction = GROUP_AGENT_FORMAT_CORRECTION
                continue
            except (OpenAIError, TypeError, ValueError) as error:
                raise AiUnavailableError(_record_provider_error(error)) from error
            parsed = _parsed_output(response)
            try:
                result = GroupAgentResponse.model_validate(
                    parsed.model_dump()
                    if isinstance(parsed, GroupAgentResponse)
                    else parsed
                )
            except ValidationError:
                correction = GROUP_AGENT_FORMAT_CORRECTION
                continue
            _clear_provider_output_trace()
            return result
        update_request_trace(provider_failure_reason="invalid_model_output")
        raise AiUnavailableError("invalid_model_output")

    async def check_attempt(
        self,
        task: CheckableTask,
        photo_data_url: str,
        reference_photo_data_url: str | None = None,
    ) -> AttemptCheckResponse:
        correction = ""
        image_context = {
            "task": task.model_dump(),
            "images": {
                "first": "사용자가 제출한 인증 사진",
                "second": (
                    "사장이 등록한 모범 사진"
                    if reference_photo_data_url is not None
                    else None
                ),
            },
        }
        image_content = [
            {
                "type": "input_text",
                "text": json.dumps(image_context, ensure_ascii=False),
            },
            {
                "type": "input_image",
                "image_url": photo_data_url,
                "detail": "auto",
            },
        ]
        if reference_photo_data_url is not None:
            image_content.append(
                {
                    "type": "input_image",
                    "image_url": reference_photo_data_url,
                    "detail": "auto",
                }
            )
        for _ in range(2):
            try:
                response = await self._client.responses.parse(
                    model=self._model,
                    instructions=photo_check_instructions(
                        self._photo_check_prompt,
                        has_reference_photo=reference_photo_data_url is not None,
                        correction=correction,
                    ),
                    input=[
                        {
                            "role": "user",
                            "content": image_content,
                        }
                    ],
                    text_format=ModelAttemptCheckResponse,
                )
            except ValidationError:
                correction = PHOTO_CHECK_FORMAT_CORRECTION
                continue
            except (OpenAIError, TypeError, ValueError) as error:
                raise AiUnavailableError(_record_provider_error(error)) from error
            parsed = _parsed_output(response)
            if not isinstance(parsed, ModelAttemptCheckResponse):
                correction = PHOTO_CHECK_FORMAT_CORRECTION
                continue
            candidate = parsed.model_dump()
            if parsed.status == "PASS":
                candidate.pop("fix", None)
                target = PassResponse
            else:
                target = RetakeResponse
            try:
                return cast(AttemptCheckResponse, target.model_validate(candidate))
            except ValidationError:
                correction = PHOTO_CHECK_FIX_CORRECTION
        update_request_trace(provider_failure_reason="invalid_model_output")
        raise AiUnavailableError


def _chat_output(response: object) -> str:
    try:
        choices = response.choices
        finish_reason = choices[0].finish_reason
        content = choices[0].message.content
    except (AttributeError, IndexError, TypeError) as error:
        update_request_trace(provider_failure_reason="invalid_provider_response")
        raise AiUnavailableError("invalid_provider_response") from error
    usage = getattr(response, "usage", None)
    completion_tokens = getattr(usage, "completion_tokens", None)
    logged_output = content if isinstance(content, str) else ""
    output_truncated = len(logged_output) > MAX_LOGGED_PROVIDER_OUTPUT_CHARS
    update_request_trace(
        provider_finish_reason=str(finish_reason or ""),
        provider_completion_tokens=(
            completion_tokens if isinstance(completion_tokens, int) else None
        ),
        provider_output=logged_output[:MAX_LOGGED_PROVIDER_OUTPUT_CHARS],
        provider_output_truncated=output_truncated,
    )
    if finish_reason == "length":
        update_request_trace(provider_failure_reason="output_limit")
        raise AiUnavailableError("output_limit")
    if not isinstance(content, str) or not content.strip():
        update_request_trace(provider_failure_reason="empty_provider_response")
        raise AiUnavailableError("empty_provider_response")
    return content


def _clear_provider_output_trace() -> None:
    update_request_trace(
        provider_failure_reason="",
        provider_finish_reason="",
        provider_completion_tokens=None,
        provider_output="",
        provider_output_truncated=False,
    )


class CerebrasOperations:
    def __init__(
        self,
        api_key: str,
        model: str,
        task_generation_prompt: str,
        photo_check_prompt: str,
    ) -> None:
        self._model = model
        self._task_generation_prompt = task_generation_prompt
        self._photo_check_prompt = photo_check_prompt
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.cerebras.ai/v1",
            timeout=CEREBRAS_TIMEOUT_SECONDS,
            max_retries=0,
        )

    async def generate_tasks(self, message: str) -> TaskGenerationResponse:
        correction = ""
        for _ in range(2):
            try:
                prompt = (
                    f"{self._task_generation_prompt}\n"
                    f"{TASK_GENERATION_DECISION_CONTRACT}\n"
                    f"{TASK_GENERATION_JSON_CONTRACT}"
                )
                response = await asyncio.wait_for(
                    self._client.chat.completions.create(
                        model=self._model,
                        messages=[
                            {
                                "role": "system",
                                "content": instructions(prompt, correction),
                            },
                            {"role": "user", "content": message},
                        ],
                        response_format=CEREBRAS_TASK_RESPONSE_FORMAT,
                        temperature=0,
                        seed=0,
                        max_completion_tokens=CEREBRAS_TASK_MAX_COMPLETION_TOKENS,
                    ),
                    timeout=CEREBRAS_TIMEOUT_SECONDS,
                )
                parsed = ModelCerebrasTaskGenerationResponse.model_validate_json(
                    _chat_output(response)
                )
                if parsed.decision == "REJECT":
                    _clear_provider_output_trace()
                    raise TaskGenerationRejectedError(parsed.reason or "")
                candidate = parsed.model_dump()
                result = TaskGenerationResponse.model_validate(
                    {
                        "tasks": [
                            candidate["firstTask"],
                            *candidate["additionalTasks"],
                        ]
                    }
                )
                _clear_provider_output_trace()
                return result
            except ValidationError:
                correction = CEREBRAS_TASK_GENERATION_FORMAT_CORRECTION
            except (
                OpenAIError,
                TimeoutError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ) as error:
                raise AiUnavailableError(_record_provider_error(error)) from error
        update_request_trace(provider_failure_reason="invalid_model_output")
        raise AiUnavailableError("invalid_model_output")

    async def answer_knowledge(
        self, information: str, question: str
    ) -> KnowledgeAnswerResponse:
        payload = json.dumps(
            {"information": information, "question": question}, ensure_ascii=False
        )
        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": KNOWLEDGE_ANSWER_PROMPT},
                        {"role": "user", "content": payload},
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "knowledge_answer",
                            "strict": True,
                            "schema": {
                                "type": "object",
                                "properties": {"answer": {"type": "string"}},
                                "required": ["answer"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    temperature=0,
                    seed=0,
                    max_completion_tokens=CEREBRAS_KNOWLEDGE_MAX_COMPLETION_TOKENS,
                    timeout=KNOWLEDGE_TIMEOUT_SECONDS,
                ),
                timeout=KNOWLEDGE_TIMEOUT_SECONDS,
            )
            parsed = ModelKnowledgeAnswerResponse.model_validate_json(
                _chat_output(response)
            )
            result = KnowledgeAnswerResponse.model_validate(parsed.model_dump())
            _clear_provider_output_trace()
            return result
        except ValidationError as error:
            update_request_trace(provider_failure_reason="invalid_model_output")
            raise AiUnavailableError("invalid_model_output") from error
        except (
            OpenAIError,
            TimeoutError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            raise AiUnavailableError(_record_provider_error(error)) from error

    async def run_group_agent(self, payload: GroupAgentRequest) -> GroupAgentResponse:
        correction = ""
        for _ in range(2):
            try:
                response = await asyncio.wait_for(
                    self._client.chat.completions.create(
                        model=self._model,
                        messages=[
                            {
                                "role": "system",
                                "content": instructions(GROUP_AGENT_PROMPT, correction),
                            },
                            {
                                "role": "user",
                                "content": json.dumps(
                                    payload.model_dump(mode="json"),
                                    ensure_ascii=False,
                                ),
                            },
                        ],
                        response_format=CEREBRAS_AGENT_RESPONSE_FORMAT,
                        temperature=0,
                        seed=0,
                        max_completion_tokens=CEREBRAS_AGENT_MAX_COMPLETION_TOKENS,
                    ),
                    timeout=AGENT_TIMEOUT_SECONDS,
                )
                result = GroupAgentResponse.model_validate_json(_chat_output(response))
            except ValidationError:
                correction = GROUP_AGENT_FORMAT_CORRECTION
                continue
            except (
                OpenAIError,
                TimeoutError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ) as error:
                raise AiUnavailableError(_record_provider_error(error)) from error
            _clear_provider_output_trace()
            return result
        update_request_trace(provider_failure_reason="invalid_model_output")
        raise AiUnavailableError("invalid_model_output")

    async def check_attempt(
        self,
        task: CheckableTask,
        photo_data_url: str,
        reference_photo_data_url: str | None = None,
    ) -> AttemptCheckResponse:
        correction = ""
        try:
            photo_data_url = normalize_cerebras_photo(photo_data_url)
            if reference_photo_data_url is not None:
                reference_photo_data_url = normalize_cerebras_photo(
                    reference_photo_data_url
                )
        except PhotoUnavailableError as error:
            update_request_trace(provider_failure_reason="invalid_photo_data")
            raise AiUnavailableError("invalid_photo_data") from error
        image_context = {
            "task": task.model_dump(),
            "images": {
                "first": "사용자가 제출한 인증 사진",
                "second": (
                    "사장이 등록한 모범 사진"
                    if reference_photo_data_url is not None
                    else None
                ),
            },
        }
        image_content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": json.dumps(image_context, ensure_ascii=False),
            },
            {
                "type": "image_url",
                "image_url": {"url": photo_data_url},
            },
        ]
        if reference_photo_data_url is not None:
            image_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": reference_photo_data_url},
                }
            )
        for _ in range(2):
            try:
                response = await asyncio.wait_for(
                    self._client.chat.completions.create(
                        model=self._model,
                        messages=[
                            {
                                "role": "system",
                                "content": photo_check_instructions(
                                    self._photo_check_prompt,
                                    has_reference_photo=(
                                        reference_photo_data_url is not None
                                    ),
                                    include_json_contract=True,
                                    correction=correction,
                                ),
                            },
                            {"role": "user", "content": image_content},
                        ],
                        response_format={"type": "json_object"},
                        max_completion_tokens=CEREBRAS_PHOTO_MAX_COMPLETION_TOKENS,
                    ),
                    timeout=CEREBRAS_TIMEOUT_SECONDS,
                )
                parsed = ModelAttemptCheckResponse.model_validate_json(
                    _chat_output(response)
                )
            except ValidationError:
                correction = PHOTO_CHECK_FORMAT_CORRECTION
                continue
            except (
                OpenAIError,
                TimeoutError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ) as error:
                raise AiUnavailableError(_record_provider_error(error)) from error
            candidate = parsed.model_dump()
            if parsed.status == "PASS":
                candidate.pop("fix", None)
                target = PassResponse
            else:
                target = RetakeResponse
            try:
                result = cast(AttemptCheckResponse, target.model_validate(candidate))
                _clear_provider_output_trace()
                return result
            except ValidationError:
                correction = PHOTO_CHECK_FIX_CORRECTION
        update_request_trace(provider_failure_reason="invalid_model_output")
        raise AiUnavailableError("invalid_model_output")


class FailoverAiOperations:
    """Cerebras가 완료하지 못한 요청을 OpenRouter GPT로 한 번 넘긴다."""

    def __init__(
        self,
        primary: AiOperations,
        fallback: AiOperations,
        fallback_model: str = "",
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._fallback_model = fallback_model

    @staticmethod
    def _log_fallback(operation: str, error: AiUnavailableError) -> None:
        cause = error.__cause__
        logger.warning(
            "ai_provider_fallback operation=%s primary=CEREBRAS "
            "fallback=OPENROUTER reason=%s upstream_status=%s request_id=%s",
            operation,
            error.reason,
            getattr(cause, "status_code", None),
            getattr(cause, "request_id", None),
        )

    async def generate_tasks(self, message: str) -> TaskGenerationResponse:
        try:
            return await self._primary.generate_tasks(message)
        except AiUnavailableError as error:
            update_request_trace(
                fallback_provider="OPENROUTER",
                fallback_model=self._fallback_model,
                provider_failure_reason=error.reason,
            )
            self._log_fallback("tasks.generate", error)
            return await self._fallback.generate_tasks(message)

    async def answer_knowledge(
        self, information: str, question: str
    ) -> KnowledgeAnswerResponse:
        try:
            return await self._primary.answer_knowledge(information, question)
        except AiUnavailableError as error:
            update_request_trace(
                fallback_provider="OPENROUTER",
                fallback_model=self._fallback_model,
                provider_failure_reason=error.reason,
            )
            self._log_fallback("knowledge.answer", error)
            return await self._fallback.answer_knowledge(information, question)

    async def check_attempt(
        self,
        task: CheckableTask,
        photo_data_url: str,
        reference_photo_data_url: str | None = None,
    ) -> AttemptCheckResponse:
        try:
            return await self._primary.check_attempt(
                task, photo_data_url, reference_photo_data_url
            )
        except AiUnavailableError as error:
            update_request_trace(
                fallback_provider="OPENROUTER",
                fallback_model=self._fallback_model,
                provider_failure_reason=error.reason,
            )
            self._log_fallback("attempts.check", error)
            return await self._fallback.check_attempt(
                task, photo_data_url, reference_photo_data_url
            )

    async def run_group_agent(self, payload: GroupAgentRequest) -> GroupAgentResponse:
        try:
            return await self._primary.run_group_agent(payload)
        except AiUnavailableError as error:
            update_request_trace(
                fallback_provider="OPENROUTER",
                fallback_model=self._fallback_model,
                provider_failure_reason=error.reason,
            )
            self._log_fallback("agent.respond", error)
            return await self._fallback.run_group_agent(payload)


class ResponseCache[ResponseModel: BaseModel]:
    """성공한 동일 요청과 동시에 들어온 요청을 메모리에서 재사용한다."""

    cache_name = "response"

    def __init__(
        self,
        *,
        max_entries: int = TASK_GENERATION_CACHE_MAX_ENTRIES,
        ttl_seconds: int = TASK_GENERATION_CACHE_TTL_SECONDS,
    ) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, tuple[float, ResponseModel]] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[ResponseModel]] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(
        self,
        key: str,
        factory: Callable[[], Awaitable[ResponseModel]],
        *,
        allow_cache_hits: bool = True,
        cache_ttl_seconds: int | None = None,
    ) -> ResponseModel:
        ttl_seconds = (
            cache_ttl_seconds if cache_ttl_seconds is not None else self._ttl_seconds
        )
        now = time.monotonic()
        async with self._lock:
            cached = self._entries.get(key) if allow_cache_hits else None
            if cached is not None:
                created_at, value = cached
                if created_at + ttl_seconds > now:
                    self._entries.move_to_end(key)
                    update_request_trace(cache_status="HIT")
                    logger.info("%s_cache_hit key=%s", self.cache_name, key[:12])
                    return value.model_copy(deep=True)
                del self._entries[key]

            task = self._inflight.get(key)
            if task is None:
                update_request_trace(cache_status="MISS")
                task = asyncio.create_task(factory())
                self._inflight[key] = task
            else:
                update_request_trace(cache_status="JOIN")
                logger.info("%s_cache_join key=%s", self.cache_name, key[:12])

        try:
            result = await asyncio.shield(task)
        except BaseException:
            async with self._lock:
                if self._inflight.get(key) is task and task.done():
                    del self._inflight[key]
            raise

        async with self._lock:
            if self._inflight.get(key) is task:
                del self._inflight[key]
                self._entries[key] = (
                    time.monotonic(),
                    result.model_copy(deep=True),
                )
                self._entries.move_to_end(key)
                while len(self._entries) > self._max_entries:
                    self._entries.popitem(last=False)
        return result.model_copy(deep=True)


class TaskGenerationCache(ResponseCache[TaskGenerationResponse]):
    cache_name = "task_generation"


class AttemptCheckCache(ResponseCache[PassResponse | RetakeResponse]):
    cache_name = "attempt_check"


class ConfigurableAiOperations:
    def __init__(self, settings: Settings, store: RuntimeSettingsStore) -> None:
        self._settings = settings
        self._store = store
        self._cache_key: tuple[str | bool, ...] | None = None
        self._service: AiOperations | None = None
        self._task_generation_cache = TaskGenerationCache()
        self._attempt_check_cache = AttemptCheckCache(
            max_entries=ATTEMPT_CHECK_CACHE_MAX_ENTRIES,
            ttl_seconds=ATTEMPT_CHECK_CACHE_TTL_SECONDS,
        )
        self._request_limiter = AiRequestLimiter(
            settings.max_concurrent_ai_requests,
            settings.max_queued_ai_requests,
            settings.ai_queue_timeout_seconds,
        )

    @staticmethod
    def _fallback_model(current: RuntimeSettings) -> str:
        if current.provider == "CEREBRAS" and current.fallback_enabled:
            return current.fallback_model
        return ""

    @classmethod
    def _service_cache_key(cls, current: RuntimeSettings) -> tuple[str | bool, ...]:
        return (
            current.provider,
            current.model,
            current.task_generation_prompt,
            current.photo_check_prompt,
            current.fallback_enabled,
            cls._fallback_model(current),
        )

    @staticmethod
    def _task_generation_cache_key(current: RuntimeSettings, message: str) -> str:
        material = json.dumps(
            [
                current.provider,
                current.model,
                current.task_generation_prompt,
                current.fallback_enabled,
                ConfigurableAiOperations._fallback_model(current),
                message,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _photo_sha256(data_url: str) -> str:
        try:
            metadata, encoded = data_url.split(",", 1)
            if not metadata.startswith("data:image/") or ";base64" not in metadata:
                raise ValueError
            photo_bytes = base64.b64decode(encoded, validate=True)
            if not photo_bytes:
                raise ValueError
        except (ValueError, binascii.Error) as error:
            raise AiUnavailableError("invalid_photo_data") from error
        return hashlib.sha256(photo_bytes).hexdigest()

    @classmethod
    def _attempt_check_cache_key(
        cls,
        current: RuntimeSettings,
        task: CheckableTask,
        photo_data_url: str,
        reference_photo_data_url: str | None,
    ) -> str:
        material = json.dumps(
            {
                "provider": current.provider,
                "model": current.model,
                "photoCheckPrompt": current.photo_check_prompt,
                "fallbackEnabled": current.fallback_enabled,
                "fallbackModel": cls._fallback_model(current),
                "task": task.model_dump(mode="json"),
                "photoSha256": cls._photo_sha256(photo_data_url),
                "referencePhotoSha256": (
                    cls._photo_sha256(reference_photo_data_url)
                    if reference_photo_data_url is not None
                    else None
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _active_service(self, current: RuntimeSettings | None = None) -> AiOperations:
        current = current or self._store.get()
        cache_key = self._service_cache_key(current)
        if self._service is not None and self._cache_key == cache_key:
            return self._service
        api_key = self._store.provider_api_key(current.provider)
        if not api_key:
            raise AiUnavailableError
        if current.provider == "CEREBRAS":
            primary: AiOperations = CerebrasOperations(
                api_key,
                current.model,
                current.task_generation_prompt,
                current.photo_check_prompt,
            )
            if current.fallback_enabled and self._settings.openrouter_api_key:
                service: AiOperations = FailoverAiOperations(
                    primary,
                    LunaOperations(
                        self._settings.openrouter_api_key,
                        current.fallback_model,
                        current.task_generation_prompt,
                        current.photo_check_prompt,
                    ),
                    current.fallback_model,
                )
            else:
                service = primary
        else:
            service = LunaOperations(
                api_key,
                current.model,
                current.task_generation_prompt,
                current.photo_check_prompt,
            )
        self._cache_key = cache_key
        self._service = service
        return service

    async def generate_tasks(self, message: str) -> TaskGenerationResponse:
        current = self._store.get()
        update_request_trace(provider=current.provider, model=current.model)
        service = self._active_service(current)
        key = self._task_generation_cache_key(current, message)
        return await self._task_generation_cache.get_or_create(
            key,
            lambda: self._request_limiter.run(lambda: service.generate_tasks(message)),
            allow_cache_hits=current.cache_hits_enabled,
            cache_ttl_seconds=current.cache_ttl_seconds,
        )

    async def answer_knowledge(
        self, information: str, question: str
    ) -> KnowledgeAnswerResponse:
        current = self._store.get()
        update_request_trace(provider=current.provider, model=current.model)
        service = self._active_service(current)
        return await self._request_limiter.run(
            lambda: service.answer_knowledge(information, question)
        )

    async def run_group_agent(self, payload: GroupAgentRequest) -> GroupAgentResponse:
        current = self._store.get()
        update_request_trace(provider=current.provider, model=current.model)
        service = self._active_service(current)
        return await self._request_limiter.run(lambda: service.run_group_agent(payload))

    async def check_attempt(
        self,
        task: CheckableTask,
        photo_data_url: str,
        reference_photo_data_url: str | None = None,
    ) -> AttemptCheckResponse:
        current = self._store.get()
        update_request_trace(provider=current.provider, model=current.model)
        service = self._active_service(current)
        key = self._attempt_check_cache_key(
            current, task, photo_data_url, reference_photo_data_url
        )
        return await self._attempt_check_cache.get_or_create(
            key,
            lambda: self._request_limiter.run(
                lambda: service.check_attempt(
                    task, photo_data_url, reference_photo_data_url
                )
            ),
            allow_cache_hits=current.cache_hits_enabled,
            cache_ttl_seconds=current.cache_ttl_seconds,
        )
