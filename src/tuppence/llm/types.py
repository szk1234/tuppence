"""Provider-neutral chat types and errors."""

from __future__ import annotations

from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field

from tuppence.core.errors import UserFacing

Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]
    # Opaque provider round-trip data (e.g. Gemini thought signatures). Never log it.
    provider_meta: dict[str, Any] | None = Field(default=None, repr=False)


class ImageData(BaseModel):
    """A picture for the `vision` task, base64-encoded."""

    media_type: Literal["image/png", "image/jpeg"]
    data_b64: str


class Message(BaseModel):
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    images: list[ImageData] = Field(default_factory=list)


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class ChatRequest(BaseModel):
    model: str
    messages: list[Message]
    tools: list[ToolSpec] = Field(default_factory=list)
    json_schema: dict[str, Any] | None = None
    schema_name: str = "result"
    max_tokens: int = 4096
    # None: the model's default. Reasoning models reject anything else, so it is sent only
    # when a caller sets it (and never to Anthropic).
    temperature: float | None = None


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class ChatResponse(BaseModel):
    text: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model: str
    finish_reason: str | None = None


class ProviderModel(BaseModel):
    id: str
    display_name: str | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    supports_tools: bool | None = None
    supports_json_schema: bool | None = None
    supports_vision: bool | None = None
    price_in_usd_per_mtok: float | None = None
    price_out_usd_per_mtok: float | None = None


class Provider(Protocol):
    api_style: str
    client: httpx.Client

    def list_models(self) -> list[ProviderModel]: ...
    def chat(self, req: ChatRequest) -> ChatResponse: ...


class LLMError(Exception):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMHTTPError(LLMError):
    def __init__(self, status: int, body: str, retry_after: float | None = None) -> None:
        super().__init__(f"HTTP {status}: {body}", retryable=status == 429 or status >= 500)
        self.status, self.body, self.retry_after = status, body, retry_after


class _Unreachable(UserFacing, LLMError):
    """A failure to get an answer, worded from safe facts only (never the exception text).

    `name` is the connection's name; the client fills it in, as providers only know the host.
    """

    what = ""

    def __init__(self, host: str | None = None, name: str | None = None) -> None:
        super().__init__(self.what, retryable=True)
        self.host, self.name = host, name

    @property
    def subject(self) -> str:
        return self.name or self.host or "the AI server"


class LLMConnectionError(_Unreachable):
    def __str__(self) -> str:
        return f"Couldn't reach {self.subject}. Is it running?"


class LLMTimeout(_Unreachable):
    def __str__(self) -> str:
        return f"{self.subject} took too long to answer."


class LLMRefused(LLMError):
    pass


class LLMBadResponse(LLMError):
    pass


class ReplyFormatError(UserFacing, LLMBadResponse):
    """A model's reply still didn't match the schema after one repair (our own wording)."""


class NoticeRequired(UserFacing, LLMError):
    def __init__(self, message: str, *, connection_id: str | None = None) -> None:
        super().__init__(message)
        self.connection_id = connection_id


class BudgetExceeded(UserFacing, LLMError):
    pass


class ContextTooLarge(UserFacing, LLMError):
    pass


class NoModelConfigured(UserFacing, LLMError):
    pass


class ConnectionChanged(UserFacing, LLMError):
    """The connection was edited while a call was waiting to retry: that model is skipped."""


class AllModelsFailed(UserFacing, LLMError):
    def __init__(self, attempts: list[str]) -> None:
        super().__init__("No AI model could answer: " + "; ".join(attempts))
        self.attempts = attempts


class AllModelsBlocked(UserFacing, LLMError):
    """Every model for the task was stopped by Local only or a local-only pin."""

    def __init__(self, message: str, *, host: str | None = None, pinned: bool = False) -> None:
        super().__init__(message)
        self.host, self.pinned = host, pinned
