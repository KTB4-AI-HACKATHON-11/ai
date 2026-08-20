from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import ValidationError

from .config import CEREBRAS_GEMMA_MODEL, DEFAULT_PROVIDER, LUNA_MODEL, Settings
from .prompts import PHOTO_CHECK_PROMPT, TASK_GENERATION_PROMPT
from .schemas import AdminSettingsUpdate, ProviderName

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeSettings:
    provider: ProviderName
    model: str
    task_generation_prompt: str
    photo_check_prompt: str
    openrouter_models: tuple[str, ...] = (LUNA_MODEL,)
    fallback_model: str = LUNA_MODEL
    cache_hits_enabled: bool = True
    cache_ttl_seconds: int = 24 * 60 * 60
    fallback_enabled: bool = True
    request_logs_enabled: bool = True
    revision: int = 1


CEREBRAS_MODELS = (CEREBRAS_GEMMA_MODEL,)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class RuntimeSettingsStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._path = (
            Path(settings.runtime_settings_path)
            if settings.runtime_settings_path
            else None
        )
        self._lock = threading.RLock()
        self._token_digest = _token_hash(settings.service_token)
        self._value = RuntimeSettings(
            provider=DEFAULT_PROVIDER,
            model=LUNA_MODEL,
            task_generation_prompt=TASK_GENERATION_PROMPT,
            photo_check_prompt=PHOTO_CHECK_PROMPT,
        )
        self._load()

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            persisted_openrouter_models = payload.get("openrouter_models", [LUNA_MODEL])
            persisted_fallback_model = payload.get("fallback_model")
            if not isinstance(persisted_fallback_model, str):
                persisted_fallback_model = (
                    persisted_openrouter_models[0]
                    if isinstance(persisted_openrouter_models, list)
                    and persisted_openrouter_models
                    else LUNA_MODEL
                )
            update = AdminSettingsUpdate.model_validate(
                {
                    "provider": payload["provider"],
                    "model": payload["model"],
                    "prompts": {
                        "taskGeneration": payload["task_generation_prompt"],
                        "photoCheck": payload["photo_check_prompt"],
                    },
                    "openrouterModels": persisted_openrouter_models,
                    "fallbackModel": persisted_fallback_model,
                    "cacheHitsEnabled": payload.get("cache_hits_enabled", True),
                    "cacheTtlSeconds": payload.get("cache_ttl_seconds", 24 * 60 * 60),
                    "fallbackEnabled": payload.get("fallback_enabled", True),
                    "requestLogsEnabled": payload.get("request_logs_enabled", True),
                }
            )
            openrouter_models = tuple(update.openrouterModels or (LUNA_MODEL,))
            self._validate_model(update.provider, update.model, openrouter_models)
            fallback_model = update.fallbackModel or openrouter_models[0]
            self._validate_fallback_model(fallback_model, openrouter_models)
            self._value = RuntimeSettings(
                provider=update.provider,
                model=update.model,
                task_generation_prompt=update.prompts.taskGeneration,
                photo_check_prompt=update.prompts.photoCheck,
                openrouter_models=openrouter_models,
                fallback_model=fallback_model,
                cache_hits_enabled=(
                    update.cacheHitsEnabled
                    if update.cacheHitsEnabled is not None
                    else True
                ),
                cache_ttl_seconds=(
                    update.cacheTtlSeconds
                    if update.cacheTtlSeconds is not None
                    else 24 * 60 * 60
                ),
                fallback_enabled=(
                    update.fallbackEnabled
                    if update.fallbackEnabled is not None
                    else True
                ),
                request_logs_enabled=(
                    update.requestLogsEnabled
                    if update.requestLogsEnabled is not None
                    else True
                ),
                revision=max(1, int(payload.get("revision", 1))),
            )
            token_digest = payload.get("service_token_sha256")
            if isinstance(token_digest, str) and len(token_digest) == 64:
                environment_digest = _token_hash(self._settings.service_token)
                persisted_environment_digest = payload.get(
                    "environment_service_token_sha256"
                )
                if (
                    persisted_environment_digest is None
                    or persisted_environment_digest == environment_digest
                ):
                    self._token_digest = token_digest
                else:
                    logger.warning(
                        "runtime_service_token_overridden_by_environment path=%s",
                        self._path,
                    )
        except (
            KeyError,
            OSError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
            ValidationError,
        ) as error:
            logger.error(
                "runtime_settings_load_failed path=%s error=%s",
                self._path,
                type(error).__name__,
            )
            return

    def _validate_model(
        self,
        provider: ProviderName,
        model: str,
        openrouter_models: tuple[str, ...],
    ) -> None:
        supported_models = (
            openrouter_models if provider == "OPENROUTER" else CEREBRAS_MODELS
        )
        if model not in supported_models:
            raise ValueError("설정에 등록된 모델을 선택해 주세요.")

    @staticmethod
    def _validate_fallback_model(
        fallback_model: str,
        openrouter_models: tuple[str, ...],
    ) -> None:
        if fallback_model not in openrouter_models:
            raise ValueError("등록된 OpenRouter 폴백 모델을 선택해 주세요.")

    def authenticate(self, token: str) -> bool:
        return hmac.compare_digest(_token_hash(token), self._token_digest)

    def get(self) -> RuntimeSettings:
        with self._lock:
            return self._value

    def provider_keys(self) -> dict[str, bool]:
        return {
            "openrouter": bool(self._settings.openrouter_api_key),
            "cerebras": bool(self._settings.cerebras_api_key),
        }

    def provider_api_key(self, provider: ProviderName) -> str:
        if provider == "CEREBRAS":
            return self._settings.cerebras_api_key
        return self._settings.openrouter_api_key

    def update(self, update: AdminSettingsUpdate) -> RuntimeSettings:
        openrouter_models = (
            tuple(update.openrouterModels)
            if update.openrouterModels is not None
            else self._value.openrouter_models
        )
        self._validate_model(update.provider, update.model, openrouter_models)
        fallback_model = (
            update.fallbackModel
            if update.fallbackModel is not None
            else self._value.fallback_model
        )
        self._validate_fallback_model(fallback_model, openrouter_models)
        if not self.provider_api_key(update.provider):
            raise ValueError("선택한 제공사의 API 키가 환경변수에 없습니다.")
        with self._lock:
            next_value = RuntimeSettings(
                provider=update.provider,
                model=update.model,
                task_generation_prompt=update.prompts.taskGeneration,
                photo_check_prompt=update.prompts.photoCheck,
                openrouter_models=openrouter_models,
                fallback_model=fallback_model,
                cache_hits_enabled=(
                    update.cacheHitsEnabled
                    if update.cacheHitsEnabled is not None
                    else self._value.cache_hits_enabled
                ),
                cache_ttl_seconds=(
                    update.cacheTtlSeconds
                    if update.cacheTtlSeconds is not None
                    else self._value.cache_ttl_seconds
                ),
                fallback_enabled=(
                    update.fallbackEnabled
                    if update.fallbackEnabled is not None
                    else self._value.fallback_enabled
                ),
                request_logs_enabled=(
                    update.requestLogsEnabled
                    if update.requestLogsEnabled is not None
                    else self._value.request_logs_enabled
                ),
                revision=self._value.revision + 1,
            )
            next_digest = (
                _token_hash(update.newServiceToken)
                if update.newServiceToken is not None
                else self._token_digest
            )
            self._persist(next_value, next_digest)
            self._value = next_value
            self._token_digest = next_digest
            return next_value

    def _persist(self, value: RuntimeSettings, token_digest: str) -> None:
        if self._path is None:
            return
        payload = {
            **asdict(value),
            "service_token_sha256": token_digest,
            "environment_service_token_sha256": _token_hash(
                self._settings.service_token
            ),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(f"{self._path.suffix}.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
            os.chmod(self._path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()
