from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from ai_backend.concurrency import AiBusyError
from ai_backend.config import Settings
from ai_backend.luna import (
    CEREBRAS_KNOWLEDGE_MAX_COMPLETION_TOKENS,
    CEREBRAS_PHOTO_MAX_COMPLETION_TOKENS,
    CEREBRAS_TASK_MAX_COMPLETION_TOKENS,
    KNOWLEDGE_TIMEOUT_SECONDS,
    LUNA_KNOWLEDGE_MAX_OUTPUT_TOKENS,
    LUNA_TIMEOUT_SECONDS,
    AiUnavailableError,
    AttemptCheckCache,
    CerebrasOperations,
    ConfigurableAiOperations,
    FailoverAiOperations,
    LunaOperations,
    TaskGenerationCache,
    _record_provider_error,
)
from ai_backend.request_trace import (
    begin_request_trace,
    end_request_trace,
    request_trace_snapshot,
)
from ai_backend.schemas import (
    AdminSettingsUpdate,
    CheckableTask,
    KnowledgeAnswerResponse,
    ModelAttemptCheckResponse,
    ModelKnowledgeAnswerResponse,
    ModelTaskGenerationResponse,
    PassResponse,
    TaskGenerationResponse,
)
from ai_backend.settings_store import RuntimeSettingsStore


def test_luna_timeout_is_20_seconds() -> None:
    assert LUNA_TIMEOUT_SECONDS == 20


def test_cerebras_task_completion_limit_is_8192_tokens() -> None:
    assert CEREBRAS_TASK_MAX_COMPLETION_TOKENS == 8_192


def test_knowledge_requests_have_a_larger_timeout() -> None:
    assert KNOWLEDGE_TIMEOUT_SECONDS == 30


def response_with(parsed: object) -> SimpleNamespace:
    return SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", parsed=parsed)],
            )
        ]
    )


class FakeResponses:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self._responses = iter(responses)
        self.instructions: list[str] = []
        self.inputs: list[object] = []
        self.calls: list[dict[str, object]] = []

    async def parse(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        self.instructions.append(str(kwargs["instructions"]))
        self.inputs.append(kwargs["input"])
        return next(self._responses)


class FakeCompletions:
    def __init__(
        self,
        outputs: list[str],
        finish_reason: str = "stop",
        completion_tokens: int | None = None,
    ) -> None:
        self._outputs = iter(outputs)
        self._finish_reason = finish_reason
        self._completion_tokens = completion_tokens
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=self._finish_reason,
                    message=SimpleNamespace(content=next(self._outputs)),
                )
            ],
            usage=SimpleNamespace(completion_tokens=self._completion_tokens),
        )


def test_task_generation_corrects_invalid_photo_rule_once() -> None:
    invalid = ModelTaskGenerationResponse.model_validate(
        {
            "tasks": [
                {
                    "title": "POS 확인",
                    "instruction": "POS를 촬영해 주세요.",
                    "completionType": "PHOTO",
                    "rule": None,
                }
            ]
        }
    )
    valid = ModelTaskGenerationResponse.model_validate(
        {
            "tasks": [
                {
                    "title": "POS 확인",
                    "instruction": "POS를 촬영해 주세요.",
                    "completionType": "PHOTO",
                    "rule": "POS 화면이 켜져 있어야 한다.",
                }
            ]
        }
    )
    responses = FakeResponses([response_with(invalid), response_with(valid)])
    service = LunaOperations("test-key")
    service._client = SimpleNamespace(responses=responses)  # type: ignore[assignment]

    result = asyncio.run(service.generate_tasks("POS 전원을 확인해"))

    assert result.tasks[0].rule == "POS 화면이 켜져 있어야 한다."
    assert len(responses.instructions) == 2
    assert "이전 결과가 계약을 어겼다" in responses.instructions[1]


def test_task_generation_correction_forbids_an_empty_task_list() -> None:
    valid = ModelTaskGenerationResponse.model_validate(
        {
            "tasks": [
                {
                    "title": "POS 확인",
                    "instruction": "POS를 촬영해 주세요.",
                    "completionType": "PHOTO",
                    "rule": "POS 화면이 켜져 있어야 한다.",
                }
            ]
        }
    )
    responses = FakeResponses([response_with({"tasks": []}), response_with(valid)])
    service = LunaOperations("test-key")
    service._client = SimpleNamespace(responses=responses)  # type: ignore[assignment]

    result = asyncio.run(service.generate_tasks("POS 전원을 확인해"))

    assert result.tasks[0].title == "POS 확인"
    assert len(responses.instructions) == 2
    assert "1개 이상 20개 이하" in responses.instructions[1]
    assert "빈 배열로 반환하지 않는다" in responses.instructions[1]


def test_luna_answers_knowledge_with_the_large_request_timeout() -> None:
    parsed = ModelKnowledgeAnswerResponse(answer="행사는 오후 6시에 시작합니다.")
    responses = FakeResponses([response_with(parsed)])
    service = LunaOperations("test-key")
    service._client = SimpleNamespace(responses=responses)  # type: ignore[assignment]

    result = asyncio.run(
        service.answer_knowledge(
            "오늘 신메뉴 시식 행사는 오후 6시에 시작합니다.",
            "행사는 언제 시작해?",
        )
    )

    assert result.answer == "행사는 오후 6시에 시작합니다."
    assert responses.inputs[0]
    assert responses.calls[0]["max_output_tokens"] == LUNA_KNOWLEDGE_MAX_OUTPUT_TOKENS


def test_pass_result_does_not_include_fix() -> None:
    parsed = ModelAttemptCheckResponse(
        status="PASS",
        reason="POS 화면이 켜져 있습니다.",
        fix=None,
    )
    service = LunaOperations("test-key")
    service._client = SimpleNamespace(  # type: ignore[assignment]
        responses=FakeResponses([response_with(parsed)])
    )
    task = CheckableTask.model_validate(
        {
            "title": "POS 전원 확인",
            "instruction": "POS 화면을 촬영해 주세요.",
            "rule": "POS 화면이 켜져 있어야 한다.",
        }
    )

    result = asyncio.run(service.check_attempt(task, "data:image/jpeg;base64,/9j/2Q=="))

    assert result.model_dump() == {
        "status": "PASS",
        "reason": "POS 화면이 켜져 있습니다.",
    }


def test_reference_photo_is_sent_as_the_second_image() -> None:
    parsed = ModelAttemptCheckResponse(
        status="PASS",
        reason="모범 사진과 같은 완료 상태입니다.",
        fix=None,
    )
    responses = FakeResponses([response_with(parsed)])
    service = LunaOperations("test-key", photo_check_prompt="내 사진 프롬프트")
    service._client = SimpleNamespace(responses=responses)  # type: ignore[assignment]
    task = CheckableTask.model_validate(
        {
            "title": "POS 전원 확인",
            "instruction": "POS 화면을 촬영해 주세요.",
            "rule": "모범 사진과 같은 완료 상태여야 한다.",
        }
    )

    asyncio.run(
        service.check_attempt(
            task,
            "data:image/jpeg;base64,dXNlcg==",
            "data:image/jpeg;base64,cmVmZXJlbmNl",
        )
    )

    content = responses.inputs[0][0]["content"]  # type: ignore[index]
    image_urls = [
        item["image_url"] for item in content if item["type"] == "input_image"
    ]
    assert image_urls == [
        "data:image/jpeg;base64,dXNlcg==",
        "data:image/jpeg;base64,cmVmZXJlbmNl",
    ]
    assert "내 사진 프롬프트" in responses.instructions[0]
    assert "GS25" in responses.instructions[0]
    assert "CU" in responses.instructions[0]
    assert "명백히 충돌" in responses.instructions[0]


def test_reference_identity_contract_is_omitted_without_reference_photo() -> None:
    parsed = ModelAttemptCheckResponse(
        status="PASS",
        reason="POS 화면이 켜져 있습니다.",
        fix=None,
    )
    responses = FakeResponses([response_with(parsed)])
    service = LunaOperations("test-key", photo_check_prompt="사용자 설정 프롬프트")
    service._client = SimpleNamespace(responses=responses)  # type: ignore[assignment]
    task = CheckableTask.model_validate(
        {
            "title": "POS 전원 확인",
            "instruction": "POS 화면을 촬영해 주세요.",
            "rule": "POS 화면이 켜져 있어야 한다.",
        }
    )

    asyncio.run(service.check_attempt(task, "data:image/jpeg;base64,dXNlcg=="))

    assert responses.instructions[0] == "사용자 설정 프롬프트"


def test_cerebras_generates_tasks_with_configured_prompt() -> None:
    completions = FakeCompletions(
        [
            (
                '{"firstTask":{"title":"POS 확인","instruction":"POS를 촬영해 주세요.",'
                '"completionType":"PHOTO","rule":"POS 화면이 켜져 있어야 한다."},'
                '"additionalTasks":[{"title":"카운터 확인","instruction":"카운터를 촬영해 주세요.",'
                '"completionType":"PHOTO","rule":"카운터가 정리되어 있어야 한다."}]}'
            )
        ]
    )
    service = CerebrasOperations(
        "test-key",
        "gemma-4-31b",
        "내 태스크 프롬프트",
        "내 사진 프롬프트",
    )
    service._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=completions)
    )

    result = asyncio.run(service.generate_tasks("POS 전원을 확인해"))

    assert [task.title for task in result.tasks] == ["POS 확인", "카운터 확인"]
    assert completions.calls[0]["model"] == "gemma-4-31b"
    assert (
        completions.calls[0]["max_completion_tokens"]
        == CEREBRAS_TASK_MAX_COMPLETION_TOKENS
    )
    assert completions.calls[0]["temperature"] == 0
    assert completions.calls[0]["seed"] == 0
    response_format = completions.calls[0]["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]["schema"]  # type: ignore[index]
    assert schema["required"] == ["firstTask", "additionalTasks"]
    assert "tasks" not in schema["properties"]
    additional_tasks_schema = schema["properties"]["additionalTasks"]
    assert "minItems" not in additional_tasks_schema
    assert "maxItems" not in additional_tasks_schema
    assert "내 태스크 프롬프트" in completions.calls[0]["messages"][0]["content"]  # type: ignore[index]
    assert "firstTask" in completions.calls[0]["messages"][0]["content"]  # type: ignore[index]
    assert "additionalTasks" in completions.calls[0]["messages"][0]["content"]  # type: ignore[index]


def test_cerebras_retries_an_invalid_required_first_task() -> None:
    completions = FakeCompletions(
        [
            (
                '{"firstTask":{"title":"POS 확인","instruction":"POS를 촬영해 주세요.",'
                '"completionType":"PHOTO","rule":null},"additionalTasks":[]}'
            ),
            (
                '{"firstTask":{"title":"POS 확인","instruction":"POS를 촬영해 주세요.",'
                '"completionType":"PHOTO","rule":"POS 화면이 켜져 있어야 한다."},'
                '"additionalTasks":[]}'
            ),
        ]
    )
    service = CerebrasOperations(
        "test-key",
        "gemma-4-31b",
        "내 태스크 프롬프트",
        "내 사진 프롬프트",
    )
    service._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=completions)
    )

    result = asyncio.run(service.generate_tasks("POS 전원을 확인해"))

    assert result.tasks[0].title == "POS 확인"
    assert len(completions.calls) == 2
    retry_prompt = completions.calls[1]["messages"][0]["content"]  # type: ignore[index]
    assert "firstTask 객체" in retry_prompt
    assert "additionalTasks 배열" in retry_prompt


def test_cerebras_answers_knowledge_with_strict_json_and_large_timeout() -> None:
    completions = FakeCompletions(['{"answer":"행사는 오후 6시에 시작합니다."}'])
    service = CerebrasOperations(
        "test-key",
        "gemma-4-31b",
        "내 태스크 프롬프트",
        "내 사진 프롬프트",
    )
    service._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=completions)
    )

    result = asyncio.run(
        service.answer_knowledge(
            "오늘 신메뉴 시식 행사는 오후 6시에 시작합니다.",
            "행사는 언제 시작해?",
        )
    )

    call = completions.calls[0]
    assert result.answer == "행사는 오후 6시에 시작합니다."
    assert call["temperature"] == 0
    assert call["seed"] == 0
    assert call["timeout"] == KNOWLEDGE_TIMEOUT_SECONDS
    assert call["max_completion_tokens"] == CEREBRAS_KNOWLEDGE_MAX_COMPLETION_TOKENS
    assert call["response_format"]["type"] == "json_schema"  # type: ignore[index]


def test_cerebras_sends_user_and_reference_images() -> None:
    completions = FakeCompletions(
        ['{"status":"PASS","reason":"모범 사진과 같은 완료 상태입니다.","fix":null}']
    )
    service = CerebrasOperations(
        "test-key",
        "gemma-4-31b",
        "내 태스크 프롬프트",
        "내 사진 프롬프트",
    )
    service._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=completions)
    )
    task = CheckableTask.model_validate(
        {
            "title": "POS 전원 확인",
            "instruction": "POS 화면을 촬영해 주세요.",
            "rule": "모범 사진과 같은 완료 상태여야 한다.",
        }
    )

    result = asyncio.run(
        service.check_attempt(
            task,
            "data:image/jpeg;base64,dXNlcg==",
            "data:image/jpeg;base64,cmVmZXJlbmNl",
        )
    )

    assert result.status == "PASS"
    messages = completions.calls[0]["messages"]  # type: ignore[assignment]
    content = messages[1]["content"]  # type: ignore[index]
    image_urls = [
        item["image_url"]["url"] for item in content if item["type"] == "image_url"
    ]
    assert image_urls == [
        "data:image/jpeg;base64,dXNlcg==",
        "data:image/jpeg;base64,cmVmZXJlbmNl",
    ]
    assert (
        completions.calls[0]["max_completion_tokens"]
        == CEREBRAS_PHOTO_MAX_COMPLETION_TOKENS
    )
    system_prompt = messages[0]["content"]  # type: ignore[index]
    assert "내 사진 프롬프트" in system_prompt
    assert "GS25" in system_prompt
    assert "CU" in system_prompt
    assert "명백히 충돌" in system_prompt


class StubAiOperations:
    def __init__(
        self,
        *,
        task_result: object | None = None,
        knowledge_result: object | None = None,
        check_result: object | None = None,
        error: AiUnavailableError | None = None,
    ) -> None:
        self.task_result = task_result
        self.knowledge_result = knowledge_result
        self.check_result = check_result
        self.error = error
        self.task_calls = 0
        self.knowledge_calls = 0
        self.check_calls = 0

    async def generate_tasks(self, _message: str) -> object:
        self.task_calls += 1
        if self.error is not None:
            raise self.error
        return self.task_result

    async def answer_knowledge(self, _information: str, _question: str) -> object:
        self.knowledge_calls += 1
        if self.error is not None:
            raise self.error
        return self.knowledge_result

    async def check_attempt(
        self,
        _task: CheckableTask,
        _photo_data_url: str,
        _reference_photo_data_url: str | None = None,
    ) -> object:
        self.check_calls += 1
        if self.error is not None:
            raise self.error
        return self.check_result


def test_failover_uses_openrouter_when_cerebras_task_generation_fails() -> None:
    expected = ModelTaskGenerationResponse.model_validate(
        {
            "tasks": [
                {
                    "title": "POS 확인",
                    "instruction": "POS를 확인해 주세요.",
                    "completionType": "CHECK",
                    "rule": None,
                }
            ]
        }
    )
    primary = StubAiOperations(error=AiUnavailableError("provider_error"))
    fallback = StubAiOperations(task_result=expected)
    service = FailoverAiOperations(primary, fallback)  # type: ignore[arg-type]

    token = begin_request_trace()
    try:
        result = asyncio.run(service.generate_tasks("POS를 확인해"))
        trace = request_trace_snapshot()
    finally:
        end_request_trace(token)

    assert result is expected
    assert primary.task_calls == 1
    assert fallback.task_calls == 1
    assert trace.fallback_provider == "OPENROUTER"
    assert trace.provider_failure_reason == "provider_error"


def test_failover_uses_openrouter_when_cerebras_photo_check_fails() -> None:
    task = CheckableTask.model_validate(
        {
            "title": "POS 전원 확인",
            "instruction": "POS 화면을 촬영해 주세요.",
            "rule": "POS 화면이 켜져 있어야 한다.",
        }
    )
    expected = ModelAttemptCheckResponse(
        status="PASS", reason="POS 화면이 켜져 있습니다.", fix=None
    )
    primary = StubAiOperations(error=AiUnavailableError("provider_error"))
    fallback = StubAiOperations(check_result=expected)
    service = FailoverAiOperations(primary, fallback)  # type: ignore[arg-type]

    result = asyncio.run(service.check_attempt(task, "data:image/jpeg;base64,dXNlcg=="))

    assert result is expected
    assert primary.check_calls == 1
    assert fallback.check_calls == 1


def test_failover_uses_openrouter_when_cerebras_knowledge_answer_fails() -> None:
    expected = KnowledgeAnswerResponse(answer="행사는 오후 6시에 시작합니다.")
    primary = StubAiOperations(error=AiUnavailableError("provider_error"))
    fallback = StubAiOperations(knowledge_result=expected)
    service = FailoverAiOperations(primary, fallback)  # type: ignore[arg-type]

    result = asyncio.run(service.answer_knowledge("행사는 오후 6시입니다.", "언제?"))

    assert result is expected
    assert primary.knowledge_calls == 1
    assert fallback.knowledge_calls == 1


def test_knowledge_output_limit_uses_fallback() -> None:
    primary = StubAiOperations(error=AiUnavailableError("output_limit"))
    expected = KnowledgeAnswerResponse(answer="필요한 항목을 요약했습니다.")
    fallback = StubAiOperations(knowledge_result=expected)
    service = FailoverAiOperations(primary, fallback)  # type: ignore[arg-type]

    result = asyncio.run(service.answer_knowledge("긴 정보", "전부 알려줘"))

    assert result is expected
    assert primary.knowledge_calls == 1
    assert fallback.knowledge_calls == 1


def test_provider_http_error_body_is_recorded_for_request_logs() -> None:
    class ProviderError(Exception):
        def __init__(self) -> None:
            super().__init__("bad schema")
            self.status_code = 400
            self.body = {
                "error": {
                    "message": "Invalid fields for schema with types ['array']: maxItems"
                }
            }

    token = begin_request_trace()
    try:
        reason = _record_provider_error(ProviderError())
        trace = request_trace_snapshot()
    finally:
        end_request_trace(token)

    assert reason == "http_400"
    assert trace.provider_failure_reason == "http_400"
    assert "maxItems" in trace.provider_output
    assert trace.provider_output_truncated is False


def test_cerebras_output_limit_is_logged_and_not_retried() -> None:
    completions = FakeCompletions(
        ['{"tasks":[{"title":"반복 중'],
        finish_reason="length",
        completion_tokens=8_192,
    )
    service = CerebrasOperations(
        "test-key",
        "gemma-4-31b",
        "내 태스크 프롬프트",
        "내 사진 프롬프트",
    )
    service._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=completions)
    )

    token = begin_request_trace()
    try:
        try:
            asyncio.run(service.generate_tasks("POS 전원을 확인해"))
        except AiUnavailableError as error:
            assert error.reason == "output_limit"
        else:
            raise AssertionError("출력 한도 도달은 AiUnavailableError여야 한다.")
        trace = request_trace_snapshot()
    finally:
        end_request_trace(token)

    assert len(completions.calls) == 1
    assert trace.provider_finish_reason == "length"
    assert trace.provider_completion_tokens == 8_192
    assert trace.provider_output == '{"tasks":[{"title":"반복 중'
    assert trace.provider_output_truncated is False


def generated_tasks() -> TaskGenerationResponse:
    return TaskGenerationResponse.model_validate(
        {
            "tasks": [
                {
                    "title": "POS 확인",
                    "instruction": "POS를 확인해 주세요.",
                    "completionType": "CHECK",
                    "rule": None,
                }
            ]
        }
    )


def test_task_generation_cache_reuses_successful_result() -> None:
    async def scenario() -> tuple[TaskGenerationResponse, TaskGenerationResponse, int]:
        calls = 0

        async def factory() -> TaskGenerationResponse:
            nonlocal calls
            calls += 1
            return generated_tasks()

        cache = TaskGenerationCache()
        first = await cache.get_or_create("same-key", factory)
        second = await cache.get_or_create("same-key", factory)
        return first, second, calls

    token = begin_request_trace()
    try:
        first, second, calls = asyncio.run(scenario())
        trace = request_trace_snapshot()
    finally:
        end_request_trace(token)

    assert calls == 1
    assert first == second
    assert first is not second
    assert trace.cache_status == "HIT"


def test_task_generation_cache_can_bypass_hits_without_clearing_cache() -> None:
    async def scenario() -> tuple[str, str, str, int]:
        calls = 0

        async def factory() -> TaskGenerationResponse:
            nonlocal calls
            calls += 1
            result = generated_tasks()
            result.tasks[0].title = f"호출 {calls}"
            return result

        cache = TaskGenerationCache()
        first = await cache.get_or_create("same-key", factory)
        bypassed = await cache.get_or_create(
            "same-key", factory, allow_cache_hits=False
        )
        restored = await cache.get_or_create("same-key", factory)
        return (
            first.tasks[0].title,
            bypassed.tasks[0].title,
            restored.tasks[0].title,
            calls,
        )

    first, bypassed, restored, calls = asyncio.run(scenario())

    assert first == "호출 1"
    assert bypassed == "호출 2"
    assert restored == "호출 2"
    assert calls == 2


def test_task_generation_cache_coalesces_concurrent_requests() -> None:
    async def scenario() -> int:
        calls = 0
        release = asyncio.Event()

        async def factory() -> TaskGenerationResponse:
            nonlocal calls
            calls += 1
            await release.wait()
            return generated_tasks()

        cache = TaskGenerationCache()
        first = asyncio.create_task(cache.get_or_create("same-key", factory))
        second = asyncio.create_task(cache.get_or_create("same-key", factory))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
        return calls

    assert asyncio.run(scenario()) == 1


def test_task_generation_cache_does_not_store_failures() -> None:
    async def scenario() -> int:
        calls = 0

        async def factory() -> TaskGenerationResponse:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise AiUnavailableError("provider_error")
            return generated_tasks()

        cache = TaskGenerationCache()
        try:
            await cache.get_or_create("same-key", factory)
        except AiUnavailableError:
            pass
        await cache.get_or_create("same-key", factory)
        return calls

    assert asyncio.run(scenario()) == 2


def test_configurable_service_uses_task_generation_cache() -> None:
    settings = Settings(
        service_token="test-service-token",
        openrouter_api_key="test-openrouter-key",
    )
    store = RuntimeSettingsStore(settings)
    current = store.get()
    fake = StubAiOperations(task_result=generated_tasks())
    service = ConfigurableAiOperations(settings, store)
    service._service = fake  # type: ignore[assignment]
    service._cache_key = service._service_cache_key(current)

    async def scenario() -> tuple[TaskGenerationResponse, TaskGenerationResponse]:
        first = await service.generate_tasks("POS를 확인해")
        second = await service.generate_tasks("POS를 확인해")
        return first, second

    token = begin_request_trace()
    try:
        first, second = asyncio.run(scenario())
        trace = request_trace_snapshot()
    finally:
        end_request_trace(token)

    assert first == second
    assert fake.task_calls == 1
    assert trace.provider == "OPENROUTER"
    assert trace.model == "openai/gpt-5.6-luna"
    assert trace.cache_status == "HIT"


def test_concurrent_identical_task_requests_join_without_using_another_slot() -> None:
    settings = Settings(
        service_token="test-service-token",
        openrouter_api_key="test-openrouter-key",
        max_concurrent_ai_requests=1,
        max_queued_ai_requests=0,
    )
    store = RuntimeSettingsStore(settings)
    current = store.get()
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingAi(StubAiOperations):
        async def generate_tasks(self, _message: str) -> object:
            self.task_calls += 1
            started.set()
            await release.wait()
            return self.task_result

    fake = BlockingAi(task_result=generated_tasks())
    service = ConfigurableAiOperations(settings, store)
    service._service = fake  # type: ignore[assignment]
    service._cache_key = service._service_cache_key(current)

    async def scenario() -> tuple[TaskGenerationResponse, TaskGenerationResponse]:
        first = asyncio.create_task(service.generate_tasks("같은 요청"))
        await started.wait()
        joined = asyncio.create_task(service.generate_tasks("같은 요청"))
        await asyncio.sleep(0)
        with pytest.raises(AiBusyError):
            await service.generate_tasks("다른 요청")
        release.set()
        return await first, await joined

    first, joined = asyncio.run(scenario())

    assert first == joined
    assert fake.task_calls == 1


def test_configurable_service_never_caches_knowledge_answers() -> None:
    settings = Settings(
        service_token="test-service-token",
        openrouter_api_key="test-openrouter-key",
    )
    store = RuntimeSettingsStore(settings)
    current = store.get()
    expected = KnowledgeAnswerResponse(answer="행사는 오후 6시에 시작합니다.")
    fake = StubAiOperations(knowledge_result=expected)
    service = ConfigurableAiOperations(settings, store)
    service._service = fake  # type: ignore[assignment]
    service._cache_key = service._service_cache_key(current)

    async def scenario() -> None:
        first = await service.answer_knowledge("행사는 오후 6시입니다.", "언제?")
        second = await service.answer_knowledge("행사는 오후 6시입니다.", "언제?")
        assert first == second

    asyncio.run(scenario())

    assert fake.knowledge_calls == 2


def test_cache_keys_change_when_fallback_policy_changes() -> None:
    settings = Settings(
        service_token="test-service-token",
        openrouter_api_key="test-openrouter-key",
    )
    store = RuntimeSettingsStore(settings)
    enabled = store.get()
    disabled = replace(enabled, fallback_enabled=False)
    task = CheckableTask(
        title="POS 확인",
        instruction="POS 화면을 촬영해 주세요.",
        rule="POS 화면이 켜져 있어야 한다.",
    )

    assert ConfigurableAiOperations._task_generation_cache_key(
        enabled, "POS 확인"
    ) != ConfigurableAiOperations._task_generation_cache_key(disabled, "POS 확인")
    assert ConfigurableAiOperations._attempt_check_cache_key(
        enabled,
        task,
        "data:image/jpeg;base64,dXNlcg==",
        None,
    ) != ConfigurableAiOperations._attempt_check_cache_key(
        disabled,
        task,
        "data:image/jpeg;base64,dXNlcg==",
        None,
    )


def test_attempt_check_cache_reuses_successful_result() -> None:
    async def scenario() -> int:
        calls = 0

        async def factory() -> PassResponse:
            nonlocal calls
            calls += 1
            return PassResponse(status="PASS", reason="POS 화면이 켜져 있습니다.")

        cache = AttemptCheckCache()
        first = await cache.get_or_create("same-key", factory)
        second = await cache.get_or_create("same-key", factory)
        assert first == second
        assert first is not second
        return calls

    assert asyncio.run(scenario()) == 1


def test_configurable_service_check_cache_uses_photo_and_reference_hashes() -> None:
    settings = Settings(
        service_token="test-service-token",
        openrouter_api_key="test-openrouter-key",
    )
    store = RuntimeSettingsStore(settings)
    current = store.get()
    expected = PassResponse(status="PASS", reason="POS 화면이 켜져 있습니다.")
    fake = StubAiOperations(check_result=expected)
    service = ConfigurableAiOperations(settings, store)
    service._service = fake  # type: ignore[assignment]
    service._cache_key = service._service_cache_key(current)
    task = CheckableTask.model_validate(
        {
            "title": "POS 전원 확인",
            "instruction": "POS 화면을 촬영해 주세요.",
            "rule": "POS 화면이 켜져 있어야 한다.",
        }
    )
    user_photo = "data:image/jpeg;base64,dXNlcg=="
    first_reference = "data:image/jpeg;base64,cmVmZXJlbmNlLTE="
    second_reference = "data:image/jpeg;base64,cmVmZXJlbmNlLTI="

    async def scenario() -> None:
        first = await service.check_attempt(task, user_photo, first_reference)
        second = await service.check_attempt(task, user_photo, first_reference)
        third = await service.check_attempt(task, user_photo, second_reference)
        assert first == second
        assert third == expected

    asyncio.run(scenario())

    assert fake.check_calls == 2


def test_configurable_service_can_disable_cerebras_fallback() -> None:
    settings = Settings(
        service_token="test-service-token",
        openrouter_api_key="test-openrouter-key",
        cerebras_api_key="test-cerebras-key",
    )
    store = RuntimeSettingsStore(settings)
    store.update(
        AdminSettingsUpdate.model_validate(
            {
                "provider": "CEREBRAS",
                "model": "gemma-4-31b",
                "prompts": {
                    "taskGeneration": "태스크 생성 프롬프트",
                    "photoCheck": "사진 검증 프롬프트",
                },
                "fallbackEnabled": False,
            }
        )
    )

    service = ConfigurableAiOperations(settings, store)

    assert isinstance(service._active_service(), CerebrasOperations)


def test_configurable_service_uses_selected_openrouter_model_for_fallback() -> None:
    settings = Settings(
        service_token="test-service-token",
        openrouter_api_key="test-openrouter-key",
        cerebras_api_key="test-cerebras-key",
    )
    store = RuntimeSettingsStore(settings)
    store.update(
        AdminSettingsUpdate.model_validate(
            {
                "provider": "CEREBRAS",
                "model": "gemma-4-31b",
                "prompts": {
                    "taskGeneration": "태스크 생성 프롬프트",
                    "photoCheck": "사진 검증 프롬프트",
                },
                "openrouterModels": [
                    "google/gemini-2.5-flash",
                    "openai/gpt-5.6-luna",
                ],
                "fallbackModel": "openai/gpt-5.6-luna",
                "fallbackEnabled": True,
            }
        )
    )

    service = ConfigurableAiOperations(settings, store)
    active = service._active_service()

    assert isinstance(active, FailoverAiOperations)
    assert isinstance(active._fallback, LunaOperations)
    assert active._fallback._model == "openai/gpt-5.6-luna"
