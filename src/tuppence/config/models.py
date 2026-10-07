"""Agent manifests: every knob an open-source user may want to turn (spec §11.2)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Task = Literal["read", "categorise", "review", "research", "coach", "report", "vision"]


class _Strict(BaseModel):
    # Budgets and thresholds are runaway-loop caps: inf/NaN would silently disable them.
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ModelRef(_Strict):
    connection_id: str
    model_id: str


class Budgets(_Strict):
    max_llm_calls: int = Field(ge=0)
    max_tokens: int = Field(ge=0)
    max_gbp: float = Field(ge=0)
    max_seconds: float = Field(gt=0)


class QuestionLimits(_Strict):
    max_open: int = Field(ge=0)
    cooldown_days: int = Field(ge=0)


class AgentManifest(_Strict):
    name: str
    description: str
    enabled: bool = True
    task: Task | None = None
    model_chain: list[ModelRef] = Field(default_factory=list)
    budgets: Budgets
    thresholds: dict[str, float] = Field(default_factory=dict)
    limits: dict[str, int] = Field(default_factory=dict)
    questions: QuestionLimits | None = None
    triggers: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    tone: Literal["plain", "detailed"] = "plain"
    prompt_template: str | None = None
