"""
Human-in-the-loop control-transfer model.

ControlState is the state machine that answers "who is (or should be)
in control of the live session right now" - the seam the brief asks
for explicitly. An Intervention is the record of one escalation: why
automation stopped, what a human did about it, and how control moved.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ControlState(str, Enum):
    AUTOMATION_CONTROL = "AUTOMATION_CONTROL"
    PAUSE_REQUESTED = "PAUSE_REQUESTED"
    INTERVENTION_WAIT = "INTERVENTION_WAIT"
    HUMAN_CONTROL = "HUMAN_CONTROL"
    RESUME_REQUESTED = "RESUME_REQUESTED"


# Allowed transitions, enforced by hitl/control_state.py
ALLOWED_TRANSITIONS: dict[ControlState, set[ControlState]] = {
    ControlState.AUTOMATION_CONTROL: {ControlState.PAUSE_REQUESTED},
    ControlState.PAUSE_REQUESTED: {ControlState.INTERVENTION_WAIT},
    ControlState.INTERVENTION_WAIT: {ControlState.HUMAN_CONTROL},
    ControlState.HUMAN_CONTROL: {ControlState.RESUME_REQUESTED},
    ControlState.RESUME_REQUESTED: {
        ControlState.AUTOMATION_CONTROL,  # checkpoint verified -> resume
        ControlState.HUMAN_CONTROL,  # checkpoint failed -> stay with human
    },
}


class InterventionReason(str, Enum):
    STUCK_UNKNOWN_STATE = "STUCK_UNKNOWN_STATE"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    MAX_STEPS_EXCEEDED = "MAX_STEPS_EXCEEDED"
    HARD_FAILURE = "HARD_FAILURE"


class InterventionContext(BaseModel):
    """Everything a human operator needs to act, per the brief's requirement."""

    goal_or_capability: str
    current_step_id: Optional[str] = None
    current_url: str
    screenshot_path: Optional[str] = None
    message: str


class OperatorAction(BaseModel):
    """One action the human performed while in HUMAN_CONTROL, for the audit trail."""

    action: str
    target_description: str
    value: Optional[str] = None
    performed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Intervention(BaseModel):
    intervention_id: str
    execution_id: str
    capability_id: Optional[str] = None
    reason: InterventionReason
    context: InterventionContext
    control_state: ControlState = ControlState.PAUSE_REQUESTED

    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    claimed_by: Optional[str] = None
    taken_at: Optional[datetime] = None
    operator_actions: list[OperatorAction] = Field(default_factory=list)
    resolved_at: Optional[datetime] = None
    resolution: Optional[str] = None  # e.g. "resumed", "aborted"
