from __future__ import annotations

import os
from dataclasses import dataclass

LUNA_MODEL = "openai/gpt-5.6-luna"
CEREBRAS_GEMMA_MODEL = "gemma-4-31b"
DEFAULT_PROVIDER = "OPENROUTER"
DEFAULT_RUNTIME_SETTINGS_PATH = ".data/ai-backend-settings.json"
DEFAULT_REQUEST_LOG_PATH = ".data/ai-request-log.jsonl"
DEFAULT_MAX_CONCURRENT_AI_REQUESTS = 16
DEFAULT_MAX_QUEUED_AI_REQUESTS = 64
DEFAULT_AI_QUEUE_TIMEOUT_SECONDS = 3


def _integer_env(name: str, default: int, minimum: int) -> int:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError as error:
        raise RuntimeError(f"{name}은 정수여야 합니다.") from error
    if value < minimum:
        raise RuntimeError(f"{name}은 {minimum} 이상이어야 합니다.")
    return value


@dataclass(frozen=True)
class Settings:
    service_token: str
    openrouter_api_key: str = ""
    cerebras_api_key: str = ""
    cors_origins: tuple[str, ...] = ()
    runtime_settings_path: str | None = None
    request_log_path: str | None = None
    max_concurrent_ai_requests: int = DEFAULT_MAX_CONCURRENT_AI_REQUESTS
    max_queued_ai_requests: int = DEFAULT_MAX_QUEUED_AI_REQUESTS
    ai_queue_timeout_seconds: int = DEFAULT_AI_QUEUE_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls) -> Settings:
        openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        cerebras_api_key = os.getenv("CEREBRAS_API_KEY", "").strip()
        service_token = os.getenv("AI_SERVICE_TOKEN", "").strip()
        cors_origins = tuple(
            origin.strip().rstrip("/")
            for origin in os.getenv("AI_CORS_ORIGINS", "").split(",")
            if origin.strip()
        )
        if not service_token:
            raise RuntimeError("AI_SERVICE_TOKEN이 필요합니다.")
        return cls(
            service_token=service_token,
            openrouter_api_key=openrouter_api_key,
            cerebras_api_key=cerebras_api_key,
            cors_origins=cors_origins,
            runtime_settings_path=os.getenv(
                "AI_RUNTIME_SETTINGS_PATH", DEFAULT_RUNTIME_SETTINGS_PATH
            ).strip()
            or None,
            request_log_path=os.getenv(
                "AI_REQUEST_LOG_PATH", DEFAULT_REQUEST_LOG_PATH
            ).strip()
            or None,
            max_concurrent_ai_requests=_integer_env(
                "AI_MAX_CONCURRENT_REQUESTS", DEFAULT_MAX_CONCURRENT_AI_REQUESTS, 1
            ),
            max_queued_ai_requests=_integer_env(
                "AI_MAX_QUEUED_REQUESTS", DEFAULT_MAX_QUEUED_AI_REQUESTS, 0
            ),
            ai_queue_timeout_seconds=_integer_env(
                "AI_QUEUE_TIMEOUT_SECONDS", DEFAULT_AI_QUEUE_TIMEOUT_SECONDS, 1
            ),
        )
