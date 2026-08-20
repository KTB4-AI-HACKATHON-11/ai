from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

StrictText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ProviderName = Literal["OPENROUTER", "CEREBRAS"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TaskGenerationRequest(StrictModel):
    message: Annotated[StrictText, Field(max_length=2_000)]


class KnowledgeAnswerRequest(StrictModel):
    information: Annotated[StrictText, Field(max_length=60_000)]
    question: Annotated[StrictText, Field(max_length=10_000)]


class KnowledgeAnswerResponse(StrictModel):
    answer: Annotated[StrictText, Field(max_length=8_000)]


class ModelKnowledgeAnswerResponse(StrictModel):
    answer: str


class PhotoTask(StrictModel):
    title: Annotated[StrictText, Field(max_length=80)]
    instruction: Annotated[StrictText, Field(max_length=500)]
    completionType: Literal["PHOTO"]
    rule: Annotated[StrictText, Field(max_length=1_000)]


class CheckTask(StrictModel):
    title: Annotated[StrictText, Field(max_length=80)]
    instruction: Annotated[StrictText, Field(max_length=500)]
    completionType: Literal["CHECK"]
    rule: None


GeneratedTask = Annotated[PhotoTask | CheckTask, Field(discriminator="completionType")]


class TaskGenerationResponse(StrictModel):
    tasks: Annotated[list[GeneratedTask], Field(min_length=1, max_length=20)]


class ModelGeneratedTask(StrictModel):
    title: str
    instruction: str
    completionType: Literal["PHOTO", "CHECK"]
    rule: str | None


class ModelTaskGenerationResponse(StrictModel):
    tasks: Annotated[list[ModelGeneratedTask], Field(min_length=1, max_length=20)]


class CheckableTask(StrictModel):
    title: Annotated[StrictText, Field(max_length=80)]
    instruction: Annotated[StrictText, Field(max_length=500)]
    rule: Annotated[StrictText, Field(max_length=1_000)]


class PhotoInput(StrictModel):
    mimeType: Literal["image/jpeg", "image/png", "image/webp"]
    sizeBytes: int = Field(ge=1, le=10 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    url: AnyHttpUrl

    @field_validator("url")
    @classmethod
    def require_https(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.scheme != "https":
            raise ValueError("HTTPS URL만 사용할 수 있습니다.")
        return value


class AttemptCheckRequest(StrictModel):
    task: CheckableTask
    photo: PhotoInput
    referencePhoto: PhotoInput | None = None


class PassResponse(StrictModel):
    status: Literal["PASS"]
    reason: Annotated[StrictText, Field(max_length=500)]


class RetakeResponse(StrictModel):
    status: Literal["RETAKE"]
    reason: Annotated[StrictText, Field(max_length=500)]
    fix: Annotated[StrictText, Field(max_length=500)]


AttemptCheckResponse = Annotated[
    PassResponse | RetakeResponse, Field(discriminator="status")
]


class ModelAttemptCheckResponse(StrictModel):
    status: Literal["PASS", "RETAKE"]
    reason: str
    fix: str | None


class AdminPromptSettings(StrictModel):
    taskGeneration: Annotated[StrictText, Field(max_length=12_000)]
    photoCheck: Annotated[StrictText, Field(max_length=12_000)]


class AdminSettingsUpdate(StrictModel):
    provider: ProviderName
    model: Annotated[StrictText, Field(max_length=120)]
    prompts: AdminPromptSettings
    openrouterModels: (
        Annotated[
            list[
                Annotated[
                    StrictText,
                    Field(max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"),
                ]
            ],
            Field(min_length=1, max_length=20),
        ]
        | None
    ) = None
    fallbackModel: (
        Annotated[
            StrictText,
            Field(max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"),
        ]
        | None
    ) = None
    cacheHitsEnabled: bool | None = None
    cacheTtlSeconds: int | None = Field(default=None, ge=60, le=7 * 24 * 60 * 60)
    fallbackEnabled: bool | None = None
    requestLogsEnabled: bool | None = None
    newServiceToken: Annotated[str, Field(min_length=24, max_length=512)] | None = None

    @field_validator("openrouterModels")
    @classmethod
    def require_unique_openrouter_models(
        cls, value: list[str] | None
    ) -> list[str] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("OpenRouter 모델 ID는 중복될 수 없습니다.")
        return value


class AvailableModel(StrictModel):
    provider: ProviderName
    id: str
    label: str


class AdminEffectivePromptSettings(StrictModel):
    taskGeneration: str
    photoCheck: str
    photoCheckWithReference: str
    knowledgeAnswer: str


class AdminSettingsResponse(StrictModel):
    provider: ProviderName
    model: str
    prompts: AdminPromptSettings
    openrouterModels: list[str]
    fallbackModel: str
    cacheHitsEnabled: bool
    cacheTtlSeconds: int
    fallbackEnabled: bool
    requestLogsEnabled: bool
    providerKeys: dict[str, bool]
    availableModels: list[AvailableModel]
    effectivePrompts: AdminEffectivePromptSettings
    revision: int


class RequestLogItem(StrictModel):
    id: int
    occurredAt: str
    method: str
    path: str
    statusCode: int
    durationMs: int
    clientAddress: str
    provider: str = ""
    model: str = ""
    cacheStatus: Literal["", "HIT", "MISS", "JOIN"] = ""
    fallbackProvider: str = ""
    outcome: str = ""
    errorCode: str = ""
    taskCount: int | None = None
    providerFailureReason: str = ""
    providerFinishReason: str = ""
    providerCompletionTokens: int | None = None
    providerOutput: str = ""
    providerOutputTruncated: bool = False
    requestPayload: str = ""
    requestPayloadTruncated: bool = False
    responsePayload: str = ""
    responsePayloadTruncated: bool = False
    requestPhotoPreview: str = ""
    referencePhotoPreview: str = ""


class RequestLogResponse(StrictModel):
    requests: list[RequestLogItem]


class ErrorDetail(StrictModel):
    code: Literal[
        "INVALID_REQUEST",
        "UNAUTHORIZED",
        "PHOTO_UNAVAILABLE",
        "AI_BUSY",
        "AI_UNAVAILABLE",
    ]
    message: str
    field: Annotated[str, Field(max_length=200)] | None = None


class ErrorResponse(StrictModel):
    error: ErrorDetail
