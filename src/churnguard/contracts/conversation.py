"""Payloads for the Conversation agent: transcript in, structured signals out."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Speaker = Literal["customer", "agent"]
SignalStrength = Literal["low", "medium", "high"]
ClaimUnit = Literal["per_line", "total", "ambiguous"]
FigurePrecision = Literal["exact", "approximate"]


class TranscriptTurn(BaseModel):
    """A single utterance in the live call transcript."""

    model_config = ConfigDict(extra="forbid")

    ts: datetime
    speaker: Speaker
    text: str


class Intent(BaseModel):
    """A customer or agent intent detected in the transcript window."""

    model_config = ConfigDict(extra="forbid")

    type: str
    confidence: float = Field(ge=0.0, le=1.0)
    scope: str | None
    span_refs: list[str]


class ChurnSignal(BaseModel):
    """An indicator that the customer is at risk of cancelling."""

    model_config = ConfigDict(extra="forbid")

    signal: str
    strength: SignalStrength


class CompetitorClaim(BaseModel):
    """A price or offer the customer says a competitor is providing."""

    model_config = ConfigDict(extra="forbid")

    carrier: str
    price: float = Field(ge=0.0)
    unit: ClaimUnit
    source: str
    span_refs: list[str]


class StatedFigure(BaseModel):
    """A number the customer stated about their own account (bill, usage, etc.)."""

    model_config = ConfigDict(extra="forbid")

    field: str
    value: float
    precision: FigurePrecision


class Concern(BaseModel):
    """An unresolved issue the customer raised."""

    model_config = ConfigDict(extra="forbid")

    issue: str
    line_ref: str | None
    location: str | None
    since: datetime | None
    customer_flagged_separate: bool


class ConversationSignals(BaseModel):
    """Structured output of the Conversation agent for one transcript window."""

    model_config = ConfigDict(extra="forbid")

    intents: list[Intent]
    churn_signals: list[ChurnSignal]
    competitor_claims: list[CompetitorClaim]
    customer_stated_figures: list[StatedFigure]
    unresolved_concerns: list[Concern]
    sentiment_trajectory: str
    verification_tasks: list[str]


class ConversationInput(BaseModel):
    """Request payload for the Conversation agent."""

    model_config = ConfigDict(extra="forbid")

    call_id: str
    transcript_window: list[TranscriptTurn]
    window_start_ts: datetime
    prior_signals: ConversationSignals | None
    locale: str
    guardrail_flags: list[str]
