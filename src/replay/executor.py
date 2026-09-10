"""
Deterministic replay engine: the production execution path an AI
agent's capability invocation actually runs. No LLM in the decision
loop - every action comes straight from the artifact's recorded steps.

Per-step handling order (see REPORT.md "Determinism & error handling"):
  1. Business outcome check - a declared, legitimate non-success
     answer (not found / locked / invalid input). Short-circuits with
     BUSINESS_OUTCOME, not an error.
  2. Attempt the step's action.
  3. On a target-not-found: check for a known recoverable interstitial
     and auto-dismiss it (bounded retries), or escalate to HITL if one
     is configured, or fail hard with enough detail to debug.
  4. After all steps: verify the declared success_condition before
     returning outputs - a click "succeeding" is not proof we reached
     the expected state.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from src.hitl.manager import HITLManager, OperatorHandler
from src.models.capability import ActionType, CapabilityArtifact, OutputSpec, ParamType, Step
from src.models.intervention import InterventionReason
from src.models.result import ErrorCode, ErrorDetail, ExecutionResult, ExecutionStatus
from src.observability.evidence import capture_failure_evidence
from src.observability.logger import RunLogger
from src.policy.engine import PolicyEngine, PolicyOutcome
from src.policy.redaction import redact_params
from src.replay.checkpoint import check_condition
from src.replay.error_classifier import DEFAULT_RECOVERABLE_PATTERNS, RecoverablePattern, detect_business_outcome, detect_recoverable_pattern
from src.replay.retry_policy import MAX_RECOVERY_ATTEMPTS_PER_STEP
from src.surface.base import Action, Surface, TargetNotFoundError

_PARAM_RE = re.compile(r"\{\{(\w+)\}\}")


class InputValidationError(Exception):
    pass


class PolicyBlockedError(Exception):
    pass


def resolve_value(template: Optional[str], inputs: dict[str, Any]) -> Optional[str]:
    if template is None:
        return None

    def _sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in inputs:
            raise InputValidationError(f"step references undeclared input '{key}'")
        return str(inputs[key])

    return _PARAM_RE.sub(_sub, template)


def validate_inputs(artifact: CapabilityArtifact, raw_inputs: dict[str, Any]) -> dict[str, Any]:
    validated: dict[str, Any] = {}
    for name, spec in artifact.inputs.items():
        if name not in raw_inputs:
            if spec.required:
                raise InputValidationError(f"missing required input '{name}'")
            continue
        value = raw_inputs[name]
        try:
            if spec.type == ParamType.INTEGER:
                value = int(value)
            elif spec.type == ParamType.DECIMAL:
                value = float(value)
            elif spec.type == ParamType.BOOLEAN:
                value = bool(value)
            else:
                value = str(value)
        except (TypeError, ValueError) as e:
            raise InputValidationError(f"input '{name}' expected type {spec.type.value}: {e}") from e
        validated[name] = value
    return validated


def coerce_output(raw_text: str, spec: OutputSpec) -> Any:
    if spec.type == ParamType.DECIMAL:
        cleaned = re.sub(r"[^0-9.\-]", "", raw_text)
        return float(cleaned) if cleaned else None
    if spec.type == ParamType.INTEGER:
        cleaned = re.sub(r"[^0-9\-]", "", raw_text)
        return int(cleaned) if cleaned else None
    if spec.type == ParamType.BOOLEAN:
        return raw_text.strip().lower() in ("true", "yes", "1")
    return raw_text.strip()


def _target_description(step: Step) -> str:
    if step.target is None:
        return step.url or ""
    p = step.target.primary
    if p.css:
        return f"css '{p.css}'"
    return f"{p.role or p.label or p.text} '{p.name or p.label or p.text or ''}'"


class ReplayExecutor:
    def __init__(
        self,
        surface: Surface,
        policy: PolicyEngine,
        logger: RunLogger,
        evidence_scenario: str,
        recoverable_patterns: Optional[list[RecoverablePattern]] = None,
        hitl: Optional[HITLManager] = None,
        operator_handler: Optional[OperatorHandler] = None,
    ):
        self.surface = surface
        self.policy = policy
        self.logger = logger
        self.evidence_scenario = evidence_scenario
        self.recoverable_patterns = recoverable_patterns if recoverable_patterns is not None else DEFAULT_RECOVERABLE_PATTERNS
        self.hitl = hitl
        self.operator_handler = operator_handler

    async def run(self, artifact: CapabilityArtifact, raw_inputs: dict[str, Any], execution_id: Optional[str] = None) -> ExecutionResult:
        execution_id = execution_id or str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)

        try:
            inputs = validate_inputs(artifact, raw_inputs)
        except InputValidationError as e:
            return ExecutionResult(
                execution_id=execution_id, capability_id=artifact.capability_id, version=artifact.version,
                status=ExecutionStatus.HARD_FAILURE,
                error=ErrorDetail(code=ErrorCode.VALIDATION_ERROR, expected="valid, complete input parameters", observed=str(e)),
                started_at=started_at, completed_at=datetime.now(timezone.utc),
            )

        redacted = redact_params(inputs, {n for n, s in artifact.inputs.items() if s.sensitive})
        self.logger.observe(None, f"starting replay of {artifact.capability_id}@{artifact.version}", detail={"inputs": redacted})

        base_url = artifact.target.base_url
        await self.surface.navigate(base_url)

        outputs: dict[str, Any] = {}

        for step in artifact.steps:
            observation = await self.surface.observe()

            outcome = await detect_business_outcome(self.surface, observation, artifact.business_outcomes)
            if outcome is not None:
                self.logger.checkpoint(step.id, f"business outcome detected: {outcome.code}", status="BUSINESS_OUTCOME")
                return ExecutionResult(
                    execution_id=execution_id, capability_id=artifact.capability_id, version=artifact.version,
                    status=ExecutionStatus.BUSINESS_OUTCOME, outcome_code=outcome.code,
                    started_at=started_at, completed_at=datetime.now(timezone.utc),
                )

            step_result = await self._execute_step(step, inputs, execution_id, artifact, observation)
            if isinstance(step_result, ExecutionResult):
                return step_result

            if step.action == ActionType.READ and step.output_key:
                outputs[step.output_key] = coerce_output(step_result, artifact.outputs[step.output_key])

        final_observation = await self.surface.observe()
        checkpoint_ok = await check_condition(self.surface, final_observation, artifact.success_condition)
        if not checkpoint_ok:
            evidence = await capture_failure_evidence(self.surface, self.evidence_scenario, "checkpoint_failed")
            self.logger.checkpoint(None, "success_condition not met after all steps", status="ERROR", detail=evidence)
            return ExecutionResult(
                execution_id=execution_id, capability_id=artifact.capability_id, version=artifact.version,
                status=ExecutionStatus.HARD_FAILURE,
                error=ErrorDetail(
                    code=ErrorCode.CHECKPOINT_NOT_MET,
                    expected=f"success_condition '{artifact.success_condition.type.value}' to hold",
                    observed=f"not satisfied at url {final_observation.url}",
                ),
                evidence_reference=evidence["screenshot"],
                started_at=started_at, completed_at=datetime.now(timezone.utc),
            )

        self.logger.checkpoint(None, "success_condition verified", status="OK")
        return ExecutionResult(
            execution_id=execution_id, capability_id=artifact.capability_id, version=artifact.version,
            status=ExecutionStatus.SUCCESS, outputs=outputs, last_verified_checkpoint=artifact.success_condition.type.value,
            started_at=started_at, completed_at=datetime.now(timezone.utc),
        )

    async def _execute_step(
        self, step: Step, inputs: dict[str, Any], execution_id: str, artifact: CapabilityArtifact, observation
    ):
        attempts = 0
        while True:
            action = self._build_action(step, inputs)
            current_url = await self.surface.current_url()
            decision = self.policy.evaluate(action, current_url)
            self.logger.policy_decision(step.id, decision.reason, status=decision.outcome.value, detail={"risk_class": decision.risk_class.value})

            if decision.outcome == PolicyOutcome.BLOCK:
                raise PolicyBlockedError(decision.reason)

            needs_escalation = decision.outcome == PolicyOutcome.REQUIRE_CONFIRMATION
            target_missing = False

            if not needs_escalation:
                try:
                    result = await self.surface.act(action)
                    self.logger.act(step.id, result.message, status="OK" if result.ok else "ERROR")
                    return result.message
                except TargetNotFoundError:
                    target_missing = True

            fresh_observation = await self.surface.observe()

            outcome = await detect_business_outcome(self.surface, fresh_observation, artifact.business_outcomes)
            if outcome is not None:
                self.logger.checkpoint(step.id, f"business outcome detected: {outcome.code}", status="BUSINESS_OUTCOME")
                return ExecutionResult(
                    execution_id=execution_id, capability_id=artifact.capability_id, version=artifact.version,
                    status=ExecutionStatus.BUSINESS_OUTCOME, outcome_code=outcome.code,
                    completed_at=datetime.now(timezone.utc),
                )

            if target_missing and attempts < MAX_RECOVERY_ATTEMPTS_PER_STEP:
                pattern = await detect_recoverable_pattern(self.surface, fresh_observation, self.recoverable_patterns)
                if pattern is not None:
                    attempts += 1
                    self.logger.act(step.id, f"recoverable condition '{pattern.name}' detected, auto-dismissing", status="RECOVERABLE")
                    recovery_action = Action(
                        type=pattern.recovery.action, target=pattern.recovery.target, target_description=pattern.name
                    )
                    await self.surface.act(recovery_action)
                    continue  # retry the original step

            if self.hitl is not None and self.operator_handler is not None:
                reason = InterventionReason.POLICY_BLOCKED if needs_escalation else InterventionReason.STUCK_UNKNOWN_STATE
                message = decision.reason if needs_escalation else f"no known recovery for the current screen at step '{step.id}'"
                intervention = await self.hitl.escalate(
                    execution_id, artifact.capability_id, step.id, reason, message, self.operator_handler
                )
                # verify we can now resolve the step's own target before resuming automation
                still_missing = False
                try:
                    probe_action = self._build_action(step, inputs)
                    if probe_action.target is not None:
                        still_missing = not await self.surface.is_visible(probe_action.target)
                except Exception:  # noqa: BLE001
                    still_missing = True

                if still_missing:
                    self.hitl.keep_with_human()
                    evidence = await capture_failure_evidence(self.surface, self.evidence_scenario, f"{step.id}_unresolved_after_handoff")
                    return ExecutionResult(
                        execution_id=execution_id, capability_id=artifact.capability_id, version=artifact.version,
                        status=ExecutionStatus.ESCALATED, intervention_id=intervention.intervention_id,
                        evidence_reference=evidence["screenshot"], completed_at=datetime.now(timezone.utc),
                    )

                self.hitl.confirm_resume()
                self.logger.checkpoint(step.id, "post-handoff checkpoint verified, resuming automation", status="OK")
                continue

            evidence = await capture_failure_evidence(self.surface, self.evidence_scenario, f"{step.id}_target_not_found")
            self.logger.error(step.id, f"target not found and no recovery available: {_target_description(step)}", detail=evidence)
            return ExecutionResult(
                execution_id=execution_id, capability_id=artifact.capability_id, version=artifact.version,
                status=ExecutionStatus.HARD_FAILURE,
                error=ErrorDetail(
                    code=ErrorCode.TARGET_NOT_FOUND if target_missing else ErrorCode.POLICY_BLOCKED,
                    failed_step=step.id,
                    expected=f"control {_target_description(step)} to be present",
                    observed=f"not found at url {fresh_observation.url}" if target_missing else decision.reason,
                ),
                evidence_reference=evidence["screenshot"],
                completed_at=datetime.now(timezone.utc),
            )

    def _build_action(self, step: Step, inputs: dict[str, Any]) -> Action:
        if step.action == ActionType.NAVIGATE:
            return Action(type=ActionType.NAVIGATE, url=step.url, target_description=step.url or "")
        value = resolve_value(step.value, inputs)
        return Action(type=step.action, target=step.target, value=value, target_description=_target_description(step))
