from __future__ import annotations

import pytest

from ai_backend.config import Settings


def test_ai_concurrency_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SERVICE_TOKEN", "test-service-token")
    monkeypatch.delenv("AI_MAX_CONCURRENT_REQUESTS", raising=False)
    monkeypatch.delenv("AI_MAX_QUEUED_REQUESTS", raising=False)
    monkeypatch.delenv("AI_QUEUE_TIMEOUT_SECONDS", raising=False)

    settings = Settings.from_env()

    assert settings.max_concurrent_ai_requests == 16
    assert settings.max_queued_ai_requests == 64
    assert settings.ai_queue_timeout_seconds == 3


def test_ai_concurrency_can_be_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SERVICE_TOKEN", "test-service-token")
    monkeypatch.setenv("AI_MAX_CONCURRENT_REQUESTS", "8")
    monkeypatch.setenv("AI_MAX_QUEUED_REQUESTS", "24")
    monkeypatch.setenv("AI_QUEUE_TIMEOUT_SECONDS", "5")

    settings = Settings.from_env()

    assert settings.max_concurrent_ai_requests == 8
    assert settings.max_queued_ai_requests == 24
    assert settings.ai_queue_timeout_seconds == 5


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AI_MAX_CONCURRENT_REQUESTS", "0"),
        ("AI_MAX_CONCURRENT_REQUESTS", "many"),
        ("AI_MAX_QUEUED_REQUESTS", "-1"),
        ("AI_QUEUE_TIMEOUT_SECONDS", "0"),
    ],
)
def test_invalid_ai_concurrency_fails_startup(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv("AI_SERVICE_TOKEN", "test-service-token")
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=name):
        Settings.from_env()
