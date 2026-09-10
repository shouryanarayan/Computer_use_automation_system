"""
Orchestrates a single escalation: pause automation, expose the live
session to an operator, record what they do, signal resume - all
against the SAME Surface/session the automation was using, never a
fresh one.

The "operator" is an injected async callback (`operator_handler`)
rather than a networked console: the brief explicitly allows mocking
the operator UI as long as the handoff mechanism and control-transfer
model are real. What's real here: the state machine, the same-session
action execution (the operator's clicks go through the identical
Surface.act() automation uses, just logged as actor=HUMAN), the
evidence capture, and the DB/audit trail. See REPORT.md "Escalation &
handoff" for the seam a real operator console would fill in.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from src.hitl.control_state import ControlStateMachine
from src.hitl.intervention import build_context
from src.models.intervention import ControlState, Intervention, InterventionReason, OperatorAction
from src.observability.evidence import screenshot_dir
from src.observability.logger import RunLogger
from src.registry.repository import Repository
from src.surface.base import Action, Surface

OperatorHandler = Callable[["HITLManager", Intervention], Awaitable[None]]


class HITLManager:
    def __init__(self, surface: Surface, repo: Repository, logger: RunLogger, evidence_scenario: str):
        self.surface = surface
        self.repo = repo
        self.logger = logger
        self.evidence_scenario = evidence_scenario
        self.csm = ControlStateMachine()

    async def escalate(
        self,
        execution_id: str,
        capability_id: Optional[str],
        step_id: str,
        reason: InterventionReason,
        message: str,
        operator_handler: OperatorHandler,
        claimed_by: str = "operator@bank.example",
    ) -> Intervention:
        self.csm.transition(ControlState.PAUSE_REQUESTED)

        observation = await self.surface.observe()
        shot_path = screenshot_dir(self.evidence_scenario) / f"intervention_{step_id}.png"
        await self.surface.screenshot(str(shot_path))

        intervention = Intervention(
            intervention_id=str(uuid.uuid4()),
            execution_id=execution_id,
            capability_id=capability_id,
            reason=reason,
            context=build_context(capability_id or "discovery", step_id, observation, str(shot_path), message),
            control_state=ControlState.PAUSE_REQUESTED,
        )
        self.repo.insert_intervention(
            intervention_id=intervention.intervention_id,
            execution_id=execution_id,
            capability_id=capability_id,
            reason=reason.value,
            context_json=intervention.context.model_dump_json(),
            control_state=ControlState.PAUSE_REQUESTED.value,
            requested_at=intervention.requested_at.isoformat(),
        )
        self.logger.intervention(
            step_id, f"escalating to human: {message}",
            detail={"reason": reason.value, "screenshot": str(shot_path), "intervention_id": intervention.intervention_id},
        )

        self.csm.transition(ControlState.INTERVENTION_WAIT)
        self.repo.update_intervention(intervention.intervention_id, control_state=ControlState.INTERVENTION_WAIT.value)

        self.csm.transition(ControlState.HUMAN_CONTROL)
        intervention.claimed_by = claimed_by
        intervention.taken_at = datetime.now(timezone.utc)
        self.repo.update_intervention(
            intervention.intervention_id,
            control_state=ControlState.HUMAN_CONTROL.value,
            claimed_by=claimed_by,
            taken_at=intervention.taken_at.isoformat(),
        )
        self.logger.human_action(step_id, f"operator '{claimed_by}' took control of the live session")

        await operator_handler(self, intervention)

        self.csm.transition(ControlState.RESUME_REQUESTED)
        intervention.resolved_at = datetime.now(timezone.utc)
        intervention.resolution = "resumed"
        self.repo.update_intervention(
            intervention.intervention_id,
            control_state=ControlState.RESUME_REQUESTED.value,
            operator_actions_json=json.dumps([a.model_dump(mode="json") for a in intervention.operator_actions]),
            resolved_at=intervention.resolved_at.isoformat(),
            resolution=intervention.resolution,
        )
        self.logger.intervention(
            step_id, "operator released control, requesting resume",
            detail={"actions_taken": len(intervention.operator_actions)},
        )
        return intervention

    async def perform_operator_action(self, intervention: Intervention, action: Action, step_id: str) -> None:
        """The operator's own action, executed through the same Surface as automation - just logged as HUMAN."""
        result = await self.surface.act(action)
        self.logger.human_action(step_id, result.message, detail={"target": action.target_description})
        intervention.operator_actions.append(
            OperatorAction(action=action.type.value, target_description=action.target_description, value=action.value)
        )

    def confirm_resume(self) -> None:
        """Called by the executor once it has verified the post-handoff checkpoint and control returns to automation."""
        self.csm.transition(ControlState.AUTOMATION_CONTROL)

    def keep_with_human(self) -> None:
        """Called when post-handoff verification fails - control stays with the human rather than resuming automation."""
        self.csm.transition(ControlState.HUMAN_CONTROL)
