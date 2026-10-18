"""Responsibilities: Validate autonomous facial behavior profiles and their approved input.
Implementation: Reject extra fields and coercion, bound local facial behavior ranges, and keep
request correlation and plan lifetime outside model-authored output. Recorded clips are fallback
only; accepted model profiles own idle, listening, thinking and speaking expression behavior.
The native speech solver retains exclusive control of spoken mouth and jaw movement.
Related Modules: presentation.service requests these plans; presentation.views validates HTTP data.

Declaration Index:
- PresentationQuestion: Bound the approved question subset available to the presentation model.
- PresentationRequest: Validate version-two identity and an optional approved question.
- PresentationRequest.canonical_uuid: Normalize valid UUID strings without accepting other types.
- FaceBehaviorProfile: Bound facial timing plus optional local head and audio-emphasis tendencies.
- FaceBehaviorProfile.coherent_ranges: Reject unusable intervals instead of repairing model output.
- StateProfiles: Require complete autonomous behavior for all four presentation states.
- ExpressionPlan: Validate model-owned profiles without timed speech cues or raw-curve controls.

Variable Index:
- ExpressionName: Allowlisted semantic intents interpreted by the UE presentation layer.
"""

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ExpressionName = Literal["neutral", "attentive", "thoughtful", "friendly", "emphasis"]


class PresentationQuestion(BaseModel):
    """Accept existing question metadata only, with no resume, answer or evaluation fields."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    question_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=1200)
    question_type: str = Field(default="", max_length=80)
    intent: str = Field(default="", max_length=400)
    dialogue_action: str = Field(default="", max_length=80)


class PresentationRequest(BaseModel):
    """Keep presentation identity independent; null question enables idle before the interview."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    version: Literal[2]
    presentation_id: str = Field(min_length=1, max_length=36)
    generation: int = Field(ge=1, le=2147483647)
    question: PresentationQuestion | None = None

    @field_validator("presentation_id")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        """Require a textual UUID and return its canonical form for response correlation."""
        return str(UUID(value))


class FaceBehaviorProfile(BaseModel):
    """Control face behavior through bounded ranges, never per-frame or arbitrary curves.

    The native controller samples blink, gaze and expression timing locally. Warmth is a small
    closed-mouth cue in quiet states and an upper-face cue during speech; the native speech mask
    preserves audio's exclusive ownership of mouth and jaw, regardless of the profile values.
    The original facial fields remain mandatory. Optional head/audio tendencies default to zero
    so an existing thirteen-field profile remains valid and gains no unsolicited head movement.
    These values describe strength/probability only; UE selects gestures, timing, angle limits and
    coordinated head/neck control locally, without additional model calls.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    expression: ExpressionName
    intensity: float = Field(ge=0.0, le=0.35, allow_inf_nan=False)
    variation: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    blink_min_ms: int = Field(ge=1800, le=9000)
    blink_max_ms: int = Field(ge=2500, le=12000)
    blink_duration_ms: int = Field(ge=120, le=250)
    gaze_amplitude: float = Field(ge=0.0, le=0.15, allow_inf_nan=False)
    gaze_hold_min_ms: int = Field(ge=1000, le=4500)
    gaze_hold_max_ms: int = Field(ge=1800, le=6500)
    eye_contact: float = Field(ge=0.65, le=1.0, allow_inf_nan=False)
    warmth: float = Field(ge=0.0, le=0.15, allow_inf_nan=False)
    motion_min_ms: int = Field(ge=2500, le=8000)
    motion_max_ms: int = Field(ge=4000, le=16000)
    head_motion_strength: float = Field(default=0.0, ge=0.0, le=1.0, allow_inf_nan=False)
    head_motion_probability: float = Field(default=0.0, ge=0.0, le=1.0, allow_inf_nan=False)
    audio_emphasis_strength: float = Field(default=0.0, ge=0.0, le=1.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def coherent_ranges(self) -> Self:
        """Require useful blink, gaze and motion sampling windows without silently fixing ranges."""
        if self.blink_max_ms < self.blink_min_ms + 500:
            raise ValueError("Blink maximum must exceed minimum by at least 500 ms.")
        if self.gaze_hold_max_ms < self.gaze_hold_min_ms + 300:
            raise ValueError("Gaze maximum must exceed minimum by at least 300 ms.")
        if self.motion_max_ms < self.motion_min_ms + 500:
            raise ValueError("Motion maximum must exceed minimum by at least 500 ms.")
        return self


class StateProfiles(BaseModel):
    """Provide four states' primary expression controller; clips activate only on fallback."""

    model_config = ConfigDict(extra="forbid", strict=True)
    idle: FaceBehaviorProfile
    listening: FaceBehaviorProfile
    thinking: FaceBehaviorProfile
    speaking: FaceBehaviorProfile


class ExpressionPlan(BaseModel):
    """Accept face profiles only; phonemes, correlation and expiry remain native/service-owned."""

    model_config = ConfigDict(extra="forbid", strict=True)
    states: StateProfiles
