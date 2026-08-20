from __future__ import annotations

import json

import pytest

from ai_backend.config import Settings
from ai_backend.schemas import AdminSettingsUpdate
from ai_backend.settings_store import RuntimeSettingsStore


def test_runtime_settings_persist_without_plaintext_token(tmp_path: object) -> None:
    from pathlib import Path

    path = Path(str(tmp_path)) / "runtime.json"
    original_token = "original-service-token"
    replacement = "replacement-service-token-123"
    settings = Settings(
        service_token=original_token,
        openrouter_api_key="openrouter-key",
        cerebras_api_key="cerebras-key",
        runtime_settings_path=str(path),
    )
    store = RuntimeSettingsStore(settings)
    store.update(
        AdminSettingsUpdate.model_validate(
            {
                "provider": "CEREBRAS",
                "model": "gemma-4-31b",
                "prompts": {
                    "taskGeneration": "저장된 태스크 프롬프트",
                    "photoCheck": "저장된 사진 프롬프트",
                },
                "openrouterModels": [
                    "google/gemini-2.5-flash",
                    "openai/gpt-5.6-luna",
                ],
                "fallbackModel": "openai/gpt-5.6-luna",
                "cacheHitsEnabled": False,
                "cacheTtlSeconds": 21_600,
                "fallbackEnabled": False,
                "requestLogsEnabled": False,
                "newServiceToken": replacement,
            }
        )
    )

    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert original_token not in raw
    assert replacement not in raw
    assert payload["service_token_sha256"]

    reloaded = RuntimeSettingsStore(settings)
    assert reloaded.get().provider == "CEREBRAS"
    assert reloaded.get().openrouter_models == (
        "google/gemini-2.5-flash",
        "openai/gpt-5.6-luna",
    )
    assert reloaded.get().fallback_model == "openai/gpt-5.6-luna"
    assert reloaded.get().task_generation_prompt == "저장된 태스크 프롬프트"
    assert reloaded.get().cache_hits_enabled is False
    assert reloaded.get().cache_ttl_seconds == 21_600
    assert reloaded.get().fallback_enabled is False
    assert reloaded.get().request_logs_enabled is False
    assert reloaded.authenticate(replacement) is True
    assert reloaded.authenticate(original_token) is False


def test_changed_environment_token_recovers_access_after_restart(
    tmp_path: object,
) -> None:
    from pathlib import Path

    path = Path(str(tmp_path)) / "runtime.json"
    original_settings = Settings(
        service_token="original-service-token",
        openrouter_api_key="openrouter-key",
        runtime_settings_path=str(path),
    )
    store = RuntimeSettingsStore(original_settings)
    store.update(
        AdminSettingsUpdate.model_validate(
            {
                "provider": "OPENROUTER",
                "model": "openai/gpt-5.6-luna",
                "prompts": {
                    "taskGeneration": "태스크 프롬프트",
                    "photoCheck": "사진 프롬프트",
                },
                "newServiceToken": "runtime-rotated-service-token",
            }
        )
    )

    changed_environment = Settings(
        service_token="deployment-recovery-service-token",
        openrouter_api_key="openrouter-key",
        runtime_settings_path=str(path),
    )
    reloaded = RuntimeSettingsStore(changed_environment)

    assert reloaded.authenticate("deployment-recovery-service-token") is True
    assert reloaded.authenticate("runtime-rotated-service-token") is False


def test_openrouter_model_catalog_validates_active_model() -> None:
    settings = Settings(
        service_token="test-service-token",
        openrouter_api_key="openrouter-key",
    )
    store = RuntimeSettingsStore(settings)
    prompts = {
        "taskGeneration": "태스크 프롬프트",
        "photoCheck": "사진 프롬프트",
    }
    store.update(
        AdminSettingsUpdate.model_validate(
            {
                "provider": "OPENROUTER",
                "model": "google/gemini-2.5-flash",
                "prompts": prompts,
                "openrouterModels": [
                    "openai/gpt-5.6-luna",
                    "google/gemini-2.5-flash",
                ],
            }
        )
    )

    assert store.get().model == "google/gemini-2.5-flash"
    assert store.get().openrouter_models[-1] == "google/gemini-2.5-flash"

    with pytest.raises(ValueError, match="등록된 모델"):
        store.update(
            AdminSettingsUpdate.model_validate(
                {
                    "provider": "OPENROUTER",
                    "model": "google/gemini-2.5-flash",
                    "prompts": prompts,
                    "openrouterModels": ["openai/gpt-5.6-luna"],
                }
            )
        )


def test_legacy_runtime_settings_use_first_openrouter_model_as_fallback(
    tmp_path: object,
) -> None:
    from pathlib import Path

    path = Path(str(tmp_path)) / "runtime.json"
    path.write_text(
        json.dumps(
            {
                "provider": "CEREBRAS",
                "model": "gemma-4-31b",
                "task_generation_prompt": "태스크 프롬프트",
                "photo_check_prompt": "사진 프롬프트",
                "openrouter_models": [
                    "google/gemini-2.5-flash",
                    "openai/gpt-5.6-luna",
                ],
                "revision": 4,
            }
        ),
        encoding="utf-8",
    )

    store = RuntimeSettingsStore(
        Settings(
            service_token="test-service-token",
            openrouter_api_key="openrouter-key",
            cerebras_api_key="cerebras-key",
            runtime_settings_path=str(path),
        )
    )

    assert store.get().fallback_model == "google/gemini-2.5-flash"
    assert store.get().revision == 4
