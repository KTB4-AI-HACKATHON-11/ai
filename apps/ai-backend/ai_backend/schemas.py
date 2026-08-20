from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
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
    decision: Literal["GENERATE", "REJECT"]
    reason: Annotated[str, Field(max_length=300)] | None
    tasks: Annotated[list[ModelGeneratedTask], Field(max_length=20)]

    @model_validator(mode="after")
    def require_consistent_decision(self) -> ModelTaskGenerationResponse:
        if self.decision == "GENERATE":
            if self.reason is not None or not self.tasks:
                raise ValueError(
                    "GENERATE에는 태스크가 필요하고 reason은 null이어야 합니다."
                )
        elif self.tasks or self.reason is None or not self.reason:
            raise ValueError(
                "REJECT에는 짧은 reason이 필요하고 tasks는 비어 있어야 합니다."
            )
        return self


class ModelCerebrasTaskGenerationResponse(StrictModel):
    decision: Literal["GENERATE", "REJECT"]
    reason: Annotated[str, Field(max_length=300)] | None
    firstTask: ModelGeneratedTask | None
    additionalTasks: Annotated[list[ModelGeneratedTask], Field(max_length=19)]

    @model_validator(mode="after")
    def require_consistent_decision(self) -> ModelCerebrasTaskGenerationResponse:
        if self.decision == "GENERATE":
            if self.reason is not None or self.firstTask is None:
                raise ValueError(
                    "GENERATE에는 firstTask가 필요하고 reason은 null이어야 합니다."
                )
        elif (
            self.firstTask is not None
            or self.additionalTasks
            or self.reason is None
            or not self.reason
        ):
            raise ValueError(
                "REJECT에는 짧은 reason이 필요하고 태스크는 비어 있어야 합니다."
            )
        return self


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


AgentToolName = Literal[
    "CREATE_TASK",
    "UPDATE_TASK",
    "COMPLETE_CHECKLIST",
    "REPLACE_STORE_INFO",
    "SEND_NOTIFICATION",
]


class AgentMember(StrictModel):
    memberId: int = Field(gt=0)
    nickname: Annotated[StrictText, Field(max_length=30)]
    role: Literal["MANAGER", "WORKER"]


class AgentTask(StrictModel):
    taskId: int = Field(gt=0)
    runId: Annotated[StrictText, Field(max_length=80)]
    title: Annotated[StrictText, Field(max_length=80)]
    workerId: int | None = Field(default=None, gt=0)
    workerNickname: Annotated[str, Field(max_length=30)] | None = None
    dueAt: Annotated[str, Field(max_length=50)] | None = None
    status: Literal["WAITING", "IN_PROGRESS", "COMPLETED", "OVERDUE"]
    itemCount: int = Field(ge=0, le=20)
    completedItemCount: int = Field(ge=0, le=20)
    progress: int = Field(ge=0, le=100)
    notifyOnCompletion: bool


class AgentStoreInfoItem(StrictModel):
    storeInfoId: int | None = Field(gt=0)
    category: Literal["LOCATION", "PROMOTION", "DELIVERY", "EQUIPMENT", "RULE", "ETC"]
    title: Annotated[StrictText, Field(max_length=60)]
    content: Annotated[StrictText, Field(max_length=1_000)]


class AgentTaskDetailChecklist(StrictModel):
    checklistId: int = Field(gt=0)
    title: Annotated[StrictText, Field(max_length=80)]
    instruction: Annotated[StrictText, Field(max_length=500)]
    completionType: Literal["PHOTO", "CHECK"]
    rule: Annotated[str, Field(max_length=1_000)] | None
    performed: bool


class AgentTaskDetail(StrictModel):
    taskId: int = Field(gt=0)
    runId: Annotated[StrictText, Field(max_length=80)]
    title: Annotated[StrictText, Field(max_length=80)]
    sourceMessage: Annotated[StrictText, Field(max_length=2_000)]
    workerId: int | None = Field(default=None, gt=0)
    workerNickname: Annotated[str, Field(max_length=30)] | None = None
    dueAt: Annotated[str, Field(max_length=50)] | None = None
    status: Literal["WAITING", "IN_PROGRESS", "COMPLETED", "OVERDUE"]
    notifyOnCompletion: bool
    checklists: Annotated[list[AgentTaskDetailChecklist], Field(max_length=20)]


class AgentConversationMessage(StrictModel):
    role: Literal["USER", "ASSISTANT"]
    content: Annotated[StrictText, Field(max_length=4_000)]


class AgentExecutionResult(StrictModel):
    callId: Annotated[StrictText, Field(max_length=40)]
    tool: AgentToolName
    success: bool
    summary: Annotated[StrictText, Field(max_length=1_000)]
    decisionBasis: Annotated[StrictText, Field(max_length=300)]
    evidence: Annotated[list[Annotated[StrictText, Field(max_length=100)]], Field(max_length=10)]


class AgentContext(StrictModel):
    groupId: int = Field(gt=0)
    groupName: Annotated[StrictText, Field(max_length=80)]
    currentDateTime: Annotated[StrictText, Field(max_length=50)]
    memberTotalCount: int = Field(ge=0)
    members: Annotated[list[AgentMember], Field(max_length=100)]
    taskTotalCount: int = Field(ge=0)
    tasks: Annotated[list[AgentTask], Field(max_length=100)]
    taskDetails: Annotated[list[AgentTaskDetail], Field(max_length=5)]
    storeInfo: Annotated[list[AgentStoreInfoItem], Field(max_length=100)]


class GroupAgentRequest(StrictModel):
    context: AgentContext
    history: Annotated[list[AgentConversationMessage], Field(max_length=60)]
    message: Annotated[StrictText, Field(max_length=2_000)]
    toolResults: Annotated[list[AgentExecutionResult], Field(max_length=5)] = Field(
        default_factory=list
    )


class AgentChecklist(StrictModel):
    title: Annotated[StrictText, Field(max_length=80)]
    instruction: Annotated[StrictText, Field(max_length=500)]
    completionType: Literal["PHOTO", "CHECK"]
    rule: Annotated[str, Field(max_length=1_000)] | None

    @model_validator(mode="after")
    def require_rule_by_type(self) -> AgentChecklist:
        if self.completionType == "PHOTO" and (self.rule is None or not self.rule):
            raise ValueError("PHOTO에는 rule이 필요합니다.")
        if self.completionType == "CHECK" and self.rule is not None:
            raise ValueError("CHECK의 rule은 null이어야 합니다.")
        return self


class AgentToolCall(StrictModel):
    callId: Annotated[StrictText, Field(max_length=40)]
    tool: AgentToolName
    dependsOnCallIds: Annotated[
        list[Annotated[StrictText, Field(max_length=40)]], Field(max_length=4)
    ]
    evidenceRefs: Annotated[
        list[
            Annotated[
                StrictText,
                Field(
                    max_length=100,
                    pattern=r"^(USER_REQUEST|CURRENT_TIME|MEMBER:[1-9][0-9]*|STORE_INFO:[1-9][0-9]*|TASK:[1-9][0-9]*|TASK_RUN:r[1-9][0-9]*-[0-9]{8})$",
                ),
            ]
        ],
        Field(min_length=1, max_length=10),
    ]
    decisionBasis: Annotated[StrictText, Field(max_length=300)]
    taskId: int | None = Field(default=None, gt=0)
    runId: Annotated[str, Field(max_length=80)] | None = None
    checklistId: int | None = Field(default=None, gt=0)
    title: Annotated[str, Field(max_length=80)] | None = None
    sourceMessage: Annotated[str, Field(max_length=2_000)] | None = None
    workerId: int | None = Field(default=None, gt=0)
    dueAt: Annotated[str, Field(max_length=50)] | None = None
    notifyOnCompletion: bool | None = None
    active: bool | None = None
    checklists: Annotated[list[AgentChecklist], Field(max_length=20)]
    storeInfo: Annotated[list[AgentStoreInfoItem], Field(max_length=100)]
    removedStoreInfoIds: Annotated[
        list[Annotated[int, Field(gt=0)]], Field(max_length=100)
    ]
    recipientMemberIds: Annotated[
        list[Annotated[int, Field(gt=0)]], Field(max_length=20)
    ]
    notificationMessage: Annotated[str, Field(max_length=300)] | None = None

    @model_validator(mode="after")
    def require_tool_arguments(self) -> AgentToolCall:
        if self.tool == "CREATE_TASK":
            if (
                not all([self.title, self.sourceMessage, self.workerId, self.dueAt])
                or self.notifyOnCompletion is None
                or not self.checklists
            ):
                raise ValueError("CREATE_TASK 인자가 부족합니다.")
        elif self.tool == "UPDATE_TASK":
            if self.taskId is None or not any(
                value is not None
                for value in [
                    self.title,
                    self.sourceMessage,
                    self.workerId,
                    self.dueAt,
                    self.notifyOnCompletion,
                    self.active,
                ]
            ):
                raise ValueError("UPDATE_TASK 인자가 부족합니다.")
        elif self.tool == "COMPLETE_CHECKLIST":
            if self.taskId is None or not self.runId or self.checklistId is None:
                raise ValueError("COMPLETE_CHECKLIST 인자가 부족합니다.")
        elif self.tool == "REPLACE_STORE_INFO":
            if any(
                value is not None
                for value in [
                    self.taskId,
                    self.title,
                    self.sourceMessage,
                    self.workerId,
                    self.dueAt,
                ]
            ):
                raise ValueError(
                    "REPLACE_STORE_INFO에 태스크 인자를 사용할 수 없습니다."
                )
        elif self.tool == "SEND_NOTIFICATION" and (
            not self.recipientMemberIds or not self.notificationMessage
        ):
            raise ValueError("SEND_NOTIFICATION 인자가 부족합니다.")
        return self


class GroupAgentResponse(StrictModel):
    message: Annotated[str, Field(max_length=4_000)]
    toolCalls: Annotated[list[AgentToolCall], Field(max_length=5)]

    @model_validator(mode="after")
    def require_unique_call_ids(self) -> GroupAgentResponse:
        if not self.toolCalls and not self.message:
            raise ValueError("도구 호출이 없으면 message가 필요합니다.")
        call_ids = [call.callId for call in self.toolCalls]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("callId는 중복될 수 없습니다.")
        previous_ids: set[str] = set()
        for call in self.toolCalls:
            if call.callId in call.dependsOnCallIds:
                raise ValueError("도구 호출은 자기 자신에 의존할 수 없습니다.")
            if any(dependency not in previous_ids for dependency in call.dependsOnCallIds):
                raise ValueError("dependsOnCallIds에는 앞선 호출만 사용할 수 있습니다.")
            previous_ids.add(call.callId)
        return self


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
        "TASK_GENERATION_REJECTED",
    ]
    message: str
    field: Annotated[str, Field(max_length=200)] | None = None


class ErrorResponse(StrictModel):
    error: ErrorDetail
