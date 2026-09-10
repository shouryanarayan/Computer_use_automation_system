"""
The control-transfer state machine: who is (or should be) in control
of a live session right now. A tiny, explicit machine on purpose - the
brief calls this out as "the seam" that a full HITL system depends on,
so its transitions are enforced (src/models/intervention.py
ALLOWED_TRANSITIONS), not just implied by code flow.
"""
from __future__ import annotations

from src.models.intervention import ALLOWED_TRANSITIONS, ControlState


class InvalidControlTransition(Exception):
    pass


class ControlStateMachine:
    def __init__(self, initial: ControlState = ControlState.AUTOMATION_CONTROL):
        self.state = initial

    def transition(self, new_state: ControlState) -> None:
        if new_state not in ALLOWED_TRANSITIONS[self.state]:
            raise InvalidControlTransition(f"cannot transition from {self.state} to {new_state}")
        self.state = new_state
