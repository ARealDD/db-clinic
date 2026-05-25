from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ExecutionMode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    EXECUTION_MODE_UNSPECIFIED: _ClassVar[ExecutionMode]
    EXECUTION_MODE_LOCAL: _ClassVar[ExecutionMode]
    EXECUTION_MODE_PROXY: _ClassVar[ExecutionMode]
    EXECUTION_MODE_HYBRID: _ClassVar[ExecutionMode]

class ToolExecutionStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TOOL_EXECUTION_STATUS_UNSPECIFIED: _ClassVar[ToolExecutionStatus]
    TOOL_EXECUTION_STARTED: _ClassVar[ToolExecutionStatus]
    TOOL_EXECUTION_COMPLETED: _ClassVar[ToolExecutionStatus]
    TOOL_EXECUTION_FAILED: _ClassVar[ToolExecutionStatus]

class TurnStopReason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TURN_STOP_REASON_UNSPECIFIED: _ClassVar[TurnStopReason]
    TURN_STOP_END_TURN: _ClassVar[TurnStopReason]
    TURN_STOP_MAX_ITERATIONS: _ClassVar[TurnStopReason]
    TURN_STOP_CANCELLED: _ClassVar[TurnStopReason]
    TURN_STOP_AWAITING_PROXY: _ClassVar[TurnStopReason]

class ErrorCode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ERROR_CODE_UNSPECIFIED: _ClassVar[ErrorCode]
    ERROR_LLM_API_FAILURE: _ClassVar[ErrorCode]
    ERROR_LLM_RATE_LIMITED: _ClassVar[ErrorCode]
    ERROR_TOOL_EXECUTION_FAILED: _ClassVar[ErrorCode]
    ERROR_SESSION_NOT_FOUND: _ClassVar[ErrorCode]
    ERROR_SESSION_EXPIRED: _ClassVar[ErrorCode]
    ERROR_PROXY_TIMEOUT: _ClassVar[ErrorCode]
    ERROR_INTERNAL: _ClassVar[ErrorCode]
    ERROR_CONTEXT_TOO_LONG: _ClassVar[ErrorCode]

class MessageRole(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MESSAGE_ROLE_UNSPECIFIED: _ClassVar[MessageRole]
    MESSAGE_ROLE_SYSTEM: _ClassVar[MessageRole]
    MESSAGE_ROLE_USER: _ClassVar[MessageRole]
    MESSAGE_ROLE_ASSISTANT: _ClassVar[MessageRole]
    MESSAGE_ROLE_TOOL: _ClassVar[MessageRole]

class ServiceStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SERVICE_STATUS_UNSPECIFIED: _ClassVar[ServiceStatus]
    SERVICE_STATUS_SERVING: _ClassVar[ServiceStatus]
    SERVICE_STATUS_NOT_SERVING: _ClassVar[ServiceStatus]
EXECUTION_MODE_UNSPECIFIED: ExecutionMode
EXECUTION_MODE_LOCAL: ExecutionMode
EXECUTION_MODE_PROXY: ExecutionMode
EXECUTION_MODE_HYBRID: ExecutionMode
TOOL_EXECUTION_STATUS_UNSPECIFIED: ToolExecutionStatus
TOOL_EXECUTION_STARTED: ToolExecutionStatus
TOOL_EXECUTION_COMPLETED: ToolExecutionStatus
TOOL_EXECUTION_FAILED: ToolExecutionStatus
TURN_STOP_REASON_UNSPECIFIED: TurnStopReason
TURN_STOP_END_TURN: TurnStopReason
TURN_STOP_MAX_ITERATIONS: TurnStopReason
TURN_STOP_CANCELLED: TurnStopReason
TURN_STOP_AWAITING_PROXY: TurnStopReason
ERROR_CODE_UNSPECIFIED: ErrorCode
ERROR_LLM_API_FAILURE: ErrorCode
ERROR_LLM_RATE_LIMITED: ErrorCode
ERROR_TOOL_EXECUTION_FAILED: ErrorCode
ERROR_SESSION_NOT_FOUND: ErrorCode
ERROR_SESSION_EXPIRED: ErrorCode
ERROR_PROXY_TIMEOUT: ErrorCode
ERROR_INTERNAL: ErrorCode
ERROR_CONTEXT_TOO_LONG: ErrorCode
MESSAGE_ROLE_UNSPECIFIED: MessageRole
MESSAGE_ROLE_SYSTEM: MessageRole
MESSAGE_ROLE_USER: MessageRole
MESSAGE_ROLE_ASSISTANT: MessageRole
MESSAGE_ROLE_TOOL: MessageRole
SERVICE_STATUS_UNSPECIFIED: ServiceStatus
SERVICE_STATUS_SERVING: ServiceStatus
SERVICE_STATUS_NOT_SERVING: ServiceStatus

class CreateSessionRequest(_message.Message):
    __slots__ = ("model", "system_prompts", "config", "api_config")
    MODEL_FIELD_NUMBER: _ClassVar[int]
    SYSTEM_PROMPTS_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    API_CONFIG_FIELD_NUMBER: _ClassVar[int]
    model: str
    system_prompts: _containers.RepeatedScalarFieldContainer[str]
    config: SessionConfig
    api_config: ApiConfig
    def __init__(self, model: _Optional[str] = ..., system_prompts: _Optional[_Iterable[str]] = ..., config: _Optional[_Union[SessionConfig, _Mapping]] = ..., api_config: _Optional[_Union[ApiConfig, _Mapping]] = ...) -> None: ...

class ApiConfig(_message.Message):
    __slots__ = ("provider", "api_key", "base_url")
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    API_KEY_FIELD_NUMBER: _ClassVar[int]
    BASE_URL_FIELD_NUMBER: _ClassVar[int]
    provider: str
    api_key: str
    base_url: str
    def __init__(self, provider: _Optional[str] = ..., api_key: _Optional[str] = ..., base_url: _Optional[str] = ...) -> None: ...

class SessionConfig(_message.Message):
    __slots__ = ("max_iterations", "default_execution_mode", "enabled_tools", "skill_ids", "auto_compaction_threshold")
    MAX_ITERATIONS_FIELD_NUMBER: _ClassVar[int]
    DEFAULT_EXECUTION_MODE_FIELD_NUMBER: _ClassVar[int]
    ENABLED_TOOLS_FIELD_NUMBER: _ClassVar[int]
    SKILL_IDS_FIELD_NUMBER: _ClassVar[int]
    AUTO_COMPACTION_THRESHOLD_FIELD_NUMBER: _ClassVar[int]
    max_iterations: int
    default_execution_mode: ExecutionMode
    enabled_tools: _containers.RepeatedScalarFieldContainer[str]
    skill_ids: _containers.RepeatedScalarFieldContainer[str]
    auto_compaction_threshold: int
    def __init__(self, max_iterations: _Optional[int] = ..., default_execution_mode: _Optional[_Union[ExecutionMode, str]] = ..., enabled_tools: _Optional[_Iterable[str]] = ..., skill_ids: _Optional[_Iterable[str]] = ..., auto_compaction_threshold: _Optional[int] = ...) -> None: ...

class CreateSessionResponse(_message.Message):
    __slots__ = ("session_id", "created_at_ms")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_MS_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    created_at_ms: int
    def __init__(self, session_id: _Optional[str] = ..., created_at_ms: _Optional[int] = ...) -> None: ...

class CloseSessionRequest(_message.Message):
    __slots__ = ("session_id",)
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    def __init__(self, session_id: _Optional[str] = ...) -> None: ...

class CloseSessionResponse(_message.Message):
    __slots__ = ("total_usage",)
    TOTAL_USAGE_FIELD_NUMBER: _ClassVar[int]
    total_usage: TokenUsage
    def __init__(self, total_usage: _Optional[_Union[TokenUsage, _Mapping]] = ...) -> None: ...

class ChatInput(_message.Message):
    __slots__ = ("session_id", "user_message", "proxy_result", "cancel")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    USER_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    PROXY_RESULT_FIELD_NUMBER: _ClassVar[int]
    CANCEL_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    user_message: UserMessage
    proxy_result: ProxyToolResult
    cancel: CancelTurn
    def __init__(self, session_id: _Optional[str] = ..., user_message: _Optional[_Union[UserMessage, _Mapping]] = ..., proxy_result: _Optional[_Union[ProxyToolResult, _Mapping]] = ..., cancel: _Optional[_Union[CancelTurn, _Mapping]] = ...) -> None: ...

class UserMessage(_message.Message):
    __slots__ = ("content", "context")
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    content: str
    context: _containers.RepeatedCompositeFieldContainer[ContextAttachment]
    def __init__(self, content: _Optional[str] = ..., context: _Optional[_Iterable[_Union[ContextAttachment, _Mapping]]] = ...) -> None: ...

class ContextAttachment(_message.Message):
    __slots__ = ("source", "content", "metadata")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    source: str
    content: str
    metadata: _containers.ScalarMap[str, str]
    def __init__(self, source: _Optional[str] = ..., content: _Optional[str] = ..., metadata: _Optional[_Mapping[str, str]] = ...) -> None: ...

class ProxyToolResult(_message.Message):
    __slots__ = ("instruction_id", "tool_use_id", "output", "is_error")
    INSTRUCTION_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_USE_ID_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_FIELD_NUMBER: _ClassVar[int]
    IS_ERROR_FIELD_NUMBER: _ClassVar[int]
    instruction_id: str
    tool_use_id: str
    output: str
    is_error: bool
    def __init__(self, instruction_id: _Optional[str] = ..., tool_use_id: _Optional[str] = ..., output: _Optional[str] = ..., is_error: bool = ...) -> None: ...

class CancelTurn(_message.Message):
    __slots__ = ("reason",)
    REASON_FIELD_NUMBER: _ClassVar[int]
    reason: str
    def __init__(self, reason: _Optional[str] = ...) -> None: ...

class ChatOutput(_message.Message):
    __slots__ = ("session_id", "text_delta", "thinking_delta", "tool_execution", "proxy_instruction", "turn_complete", "error", "usage_update", "skill_match", "proxy_instruction_expired")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    TEXT_DELTA_FIELD_NUMBER: _ClassVar[int]
    THINKING_DELTA_FIELD_NUMBER: _ClassVar[int]
    TOOL_EXECUTION_FIELD_NUMBER: _ClassVar[int]
    PROXY_INSTRUCTION_FIELD_NUMBER: _ClassVar[int]
    TURN_COMPLETE_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    USAGE_UPDATE_FIELD_NUMBER: _ClassVar[int]
    SKILL_MATCH_FIELD_NUMBER: _ClassVar[int]
    PROXY_INSTRUCTION_EXPIRED_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    text_delta: TextDelta
    thinking_delta: ThinkingDelta
    tool_execution: ToolExecution
    proxy_instruction: ProxyToolInstruction
    turn_complete: TurnComplete
    error: ErrorEvent
    usage_update: TokenUsage
    skill_match: SkillMatch
    proxy_instruction_expired: ProxyInstructionExpired
    def __init__(self, session_id: _Optional[str] = ..., text_delta: _Optional[_Union[TextDelta, _Mapping]] = ..., thinking_delta: _Optional[_Union[ThinkingDelta, _Mapping]] = ..., tool_execution: _Optional[_Union[ToolExecution, _Mapping]] = ..., proxy_instruction: _Optional[_Union[ProxyToolInstruction, _Mapping]] = ..., turn_complete: _Optional[_Union[TurnComplete, _Mapping]] = ..., error: _Optional[_Union[ErrorEvent, _Mapping]] = ..., usage_update: _Optional[_Union[TokenUsage, _Mapping]] = ..., skill_match: _Optional[_Union[SkillMatch, _Mapping]] = ..., proxy_instruction_expired: _Optional[_Union[ProxyInstructionExpired, _Mapping]] = ...) -> None: ...

class ProxyInstructionExpired(_message.Message):
    __slots__ = ("instruction_id", "tool_use_id", "reason")
    INSTRUCTION_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_USE_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    instruction_id: str
    tool_use_id: str
    reason: str
    def __init__(self, instruction_id: _Optional[str] = ..., tool_use_id: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

class TextDelta(_message.Message):
    __slots__ = ("content",)
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    content: str
    def __init__(self, content: _Optional[str] = ...) -> None: ...

class ThinkingDelta(_message.Message):
    __slots__ = ("content",)
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    content: str
    def __init__(self, content: _Optional[str] = ...) -> None: ...

class ToolExecution(_message.Message):
    __slots__ = ("tool_use_id", "tool_name", "input_json", "status", "output", "is_error", "duration_ms")
    TOOL_USE_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    INPUT_JSON_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_FIELD_NUMBER: _ClassVar[int]
    IS_ERROR_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    tool_use_id: str
    tool_name: str
    input_json: str
    status: ToolExecutionStatus
    output: str
    is_error: bool
    duration_ms: int
    def __init__(self, tool_use_id: _Optional[str] = ..., tool_name: _Optional[str] = ..., input_json: _Optional[str] = ..., status: _Optional[_Union[ToolExecutionStatus, str]] = ..., output: _Optional[str] = ..., is_error: bool = ..., duration_ms: _Optional[int] = ...) -> None: ...

class ProxyToolInstruction(_message.Message):
    __slots__ = ("instruction_id", "tool_use_id", "tool_name", "card")
    INSTRUCTION_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_USE_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    CARD_FIELD_NUMBER: _ClassVar[int]
    instruction_id: str
    tool_use_id: str
    tool_name: str
    card: InstructionCard
    def __init__(self, instruction_id: _Optional[str] = ..., tool_use_id: _Optional[str] = ..., tool_name: _Optional[str] = ..., card: _Optional[_Union[InstructionCard, _Mapping]] = ...) -> None: ...

class InstructionCard(_message.Message):
    __slots__ = ("command", "target_environment", "expected_format", "hint", "timeout_seconds", "read_only", "parameters", "purpose")
    class ParametersEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    COMMAND_FIELD_NUMBER: _ClassVar[int]
    TARGET_ENVIRONMENT_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_FORMAT_FIELD_NUMBER: _ClassVar[int]
    HINT_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_SECONDS_FIELD_NUMBER: _ClassVar[int]
    READ_ONLY_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_FIELD_NUMBER: _ClassVar[int]
    PURPOSE_FIELD_NUMBER: _ClassVar[int]
    command: str
    target_environment: str
    expected_format: str
    hint: str
    timeout_seconds: int
    read_only: bool
    parameters: _containers.ScalarMap[str, str]
    purpose: str
    def __init__(self, command: _Optional[str] = ..., target_environment: _Optional[str] = ..., expected_format: _Optional[str] = ..., hint: _Optional[str] = ..., timeout_seconds: _Optional[int] = ..., read_only: bool = ..., parameters: _Optional[_Mapping[str, str]] = ..., purpose: _Optional[str] = ...) -> None: ...

class SkillMatch(_message.Message):
    __slots__ = ("skills",)
    SKILLS_FIELD_NUMBER: _ClassVar[int]
    skills: _containers.RepeatedCompositeFieldContainer[MatchedSkillInfo]
    def __init__(self, skills: _Optional[_Iterable[_Union[MatchedSkillInfo, _Mapping]]] = ...) -> None: ...

class MatchedSkillInfo(_message.Message):
    __slots__ = ("skill_id", "skill_name", "category", "score", "skill_type")
    SKILL_ID_FIELD_NUMBER: _ClassVar[int]
    SKILL_NAME_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    SKILL_TYPE_FIELD_NUMBER: _ClassVar[int]
    skill_id: str
    skill_name: str
    category: str
    score: float
    skill_type: str
    def __init__(self, skill_id: _Optional[str] = ..., skill_name: _Optional[str] = ..., category: _Optional[str] = ..., score: _Optional[float] = ..., skill_type: _Optional[str] = ...) -> None: ...

class TurnComplete(_message.Message):
    __slots__ = ("messages", "turn_usage", "stop_reason")
    MESSAGES_FIELD_NUMBER: _ClassVar[int]
    TURN_USAGE_FIELD_NUMBER: _ClassVar[int]
    STOP_REASON_FIELD_NUMBER: _ClassVar[int]
    messages: _containers.RepeatedCompositeFieldContainer[ConversationMessage]
    turn_usage: TokenUsage
    stop_reason: TurnStopReason
    def __init__(self, messages: _Optional[_Iterable[_Union[ConversationMessage, _Mapping]]] = ..., turn_usage: _Optional[_Union[TokenUsage, _Mapping]] = ..., stop_reason: _Optional[_Union[TurnStopReason, str]] = ...) -> None: ...

class ErrorEvent(_message.Message):
    __slots__ = ("code", "message", "recoverable")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    RECOVERABLE_FIELD_NUMBER: _ClassVar[int]
    code: ErrorCode
    message: str
    recoverable: bool
    def __init__(self, code: _Optional[_Union[ErrorCode, str]] = ..., message: _Optional[str] = ..., recoverable: bool = ...) -> None: ...

class TokenUsage(_message.Message):
    __slots__ = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    INPUT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    CACHE_CREATION_INPUT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    CACHE_READ_INPUT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    def __init__(self, input_tokens: _Optional[int] = ..., output_tokens: _Optional[int] = ..., cache_creation_input_tokens: _Optional[int] = ..., cache_read_input_tokens: _Optional[int] = ...) -> None: ...

class ConversationMessage(_message.Message):
    __slots__ = ("role", "blocks", "usage")
    ROLE_FIELD_NUMBER: _ClassVar[int]
    BLOCKS_FIELD_NUMBER: _ClassVar[int]
    USAGE_FIELD_NUMBER: _ClassVar[int]
    role: MessageRole
    blocks: _containers.RepeatedCompositeFieldContainer[ContentBlock]
    usage: TokenUsage
    def __init__(self, role: _Optional[_Union[MessageRole, str]] = ..., blocks: _Optional[_Iterable[_Union[ContentBlock, _Mapping]]] = ..., usage: _Optional[_Union[TokenUsage, _Mapping]] = ...) -> None: ...

class ContentBlock(_message.Message):
    __slots__ = ("text", "thinking", "tool_use", "tool_result")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    THINKING_FIELD_NUMBER: _ClassVar[int]
    TOOL_USE_FIELD_NUMBER: _ClassVar[int]
    TOOL_RESULT_FIELD_NUMBER: _ClassVar[int]
    text: TextContent
    thinking: ThinkingContent
    tool_use: ToolUseContent
    tool_result: ToolResultContent
    def __init__(self, text: _Optional[_Union[TextContent, _Mapping]] = ..., thinking: _Optional[_Union[ThinkingContent, _Mapping]] = ..., tool_use: _Optional[_Union[ToolUseContent, _Mapping]] = ..., tool_result: _Optional[_Union[ToolResultContent, _Mapping]] = ...) -> None: ...

class TextContent(_message.Message):
    __slots__ = ("text",)
    TEXT_FIELD_NUMBER: _ClassVar[int]
    text: str
    def __init__(self, text: _Optional[str] = ...) -> None: ...

class ThinkingContent(_message.Message):
    __slots__ = ("thinking", "signature")
    THINKING_FIELD_NUMBER: _ClassVar[int]
    SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    thinking: str
    signature: str
    def __init__(self, thinking: _Optional[str] = ..., signature: _Optional[str] = ...) -> None: ...

class ToolUseContent(_message.Message):
    __slots__ = ("id", "name", "input")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    input: str
    def __init__(self, id: _Optional[str] = ..., name: _Optional[str] = ..., input: _Optional[str] = ...) -> None: ...

class ToolResultContent(_message.Message):
    __slots__ = ("tool_use_id", "tool_name", "output", "is_error")
    TOOL_USE_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_FIELD_NUMBER: _ClassVar[int]
    IS_ERROR_FIELD_NUMBER: _ClassVar[int]
    tool_use_id: str
    tool_name: str
    output: str
    is_error: bool
    def __init__(self, tool_use_id: _Optional[str] = ..., tool_name: _Optional[str] = ..., output: _Optional[str] = ..., is_error: bool = ...) -> None: ...

class HealthCheckRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class HealthCheckResponse(_message.Message):
    __slots__ = ("status", "active_sessions", "uptime_seconds", "version")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_SESSIONS_FIELD_NUMBER: _ClassVar[int]
    UPTIME_SECONDS_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    status: ServiceStatus
    active_sessions: int
    uptime_seconds: int
    version: str
    def __init__(self, status: _Optional[_Union[ServiceStatus, str]] = ..., active_sessions: _Optional[int] = ..., uptime_seconds: _Optional[int] = ..., version: _Optional[str] = ...) -> None: ...
