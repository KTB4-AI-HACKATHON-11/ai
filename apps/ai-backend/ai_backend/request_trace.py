from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, replace


@dataclass
class RequestTrace:
    provider: str = ""
    model: str = ""
    cache_status: str = ""
    fallback_provider: str = ""
    fallback_model: str = ""
    outcome: str = ""
    error_code: str = ""
    task_count: int | None = None
    provider_failure_reason: str = ""
    provider_finish_reason: str = ""
    provider_completion_tokens: int | None = None
    provider_output: str = ""
    provider_output_truncated: bool = False
    request_payload: str = ""
    request_payload_truncated: bool = False
    response_payload: str = ""
    response_payload_truncated: bool = False
    request_photo_preview: str = ""
    reference_photo_preview: str = ""


_current_trace: ContextVar[RequestTrace | None] = ContextVar(
    "ai_backend_request_trace", default=None
)


def begin_request_trace() -> Token[RequestTrace | None]:
    return _current_trace.set(RequestTrace())


def update_request_trace(**values: str | int | bool | None) -> None:
    trace = _current_trace.get()
    if trace is None:
        return
    for name, value in values.items():
        if hasattr(trace, name):
            setattr(trace, name, value)


def request_trace_snapshot() -> RequestTrace:
    trace = _current_trace.get()
    return replace(trace) if trace is not None else RequestTrace()


def end_request_trace(token: Token[RequestTrace | None]) -> None:
    _current_trace.reset(token)
