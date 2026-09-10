"""
Discovery agent: the genuine LLM-driven observe -> decide -> act loop
required by the brief (3.1). Every action proposed by the model passes
through the policy engine before it reaches the surface - "the LLM
proposes, policy authorizes" - and every step is logged as evidence.

This loop's raw output (StepRecord list) is intentionally *not* the
reusable artifact. src/discovery/artifact_builder.py is a separate,
deliberate conversion step - see that module's docstring for why.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.discovery.llm_client import AgentDecision, LLMClient
from src.discovery.observer import format_observation_for_llm
from src.models.capability import ActionType, LocatorStrategy, Target, TargetLocator
from src.observability.logger import RunLogger
from src.policy.engine import PolicyEngine, PolicyOutcome
from src.surface.base import Action, Surface
from src.surface.fingerprint import compute_fingerprint


class DiscoveryStuckError(Exception):
    def __init__(self, reason: str, current_url: str = ""):
        self.reason = reason
        self.current_url = current_url
        super().__init__(reason)


class PolicyBlockedError(Exception):
    pass


@dataclass
class StepRecord:
    id: str
    action: str
    target_role: Optional[str] = None
    target_name: Optional[str] = None
    target_css: Optional[str] = None
    value: Optional[str] = None
    output_key: Optional[str] = None
    extracted_value: Optional[str] = None
    url: Optional[str] = None


@dataclass
class DiscoveryResult:
    success: bool
    outputs: dict[str, Any] = field(default_factory=dict)
    steps: list[StepRecord] = field(default_factory=list)
    final_url: str = ""


def _target_from_decision(decision: AgentDecision) -> Target:
    if decision.target_css:
        return Target(primary=TargetLocator(strategy=LocatorStrategy.CSS, css=decision.target_css))
    return Target(primary=TargetLocator(strategy=LocatorStrategy.ROLE, role=decision.target_role, name=decision.target_name))


class DiscoveryAgent:
    def __init__(
        self,
        surface: Surface,
        llm_client: LLMClient,
        policy: PolicyEngine,
        logger: RunLogger,
        max_steps: Optional[int] = None,
        screenshot_dir: Optional[Path] = None,
    ):
        self.surface = surface
        self.llm = llm_client
        self.policy = policy
        self.logger = logger
        self.max_steps = max_steps or policy.max_steps_per_run
        self.screenshot_dir = screenshot_dir

    async def run(self, goal: str, start_url: str) -> DiscoveryResult:
        await self.surface.navigate(start_url)
        steps: list[StepRecord] = []
        history_lines: list[str] = []

        for i in range(self.max_steps):
            step_id = f"s{i}"
            observation = await self.surface.observe()
            fingerprint = compute_fingerprint(observation)
            self.logger.observe(
                step_id, f"observed {observation.url}",
                detail={"fingerprint": fingerprint, "num_controls": len(observation.controls)},
            )

            decision = self.llm.decide(
                goal=goal,
                observation_text=format_observation_for_llm(observation),
                history_summary="\n".join(history_lines[-10:]),
            )
            self.logger.decide(
                step_id, decision.reasoning,
                detail={"action": decision.action, "target_role": decision.target_role, "target_name": decision.target_name},
            )

            if decision.action == "finish":
                self.logger.checkpoint(step_id, "goal declared met by model", status="OK", detail={"outputs": decision.outputs})
                return DiscoveryResult(success=True, outputs=decision.outputs, steps=steps, final_url=observation.url)

            if decision.action == "request_human":
                self.logger.intervention(step_id, f"model requested human help: {decision.reason}")
                raise DiscoveryStuckError(decision.reason or "model requested human assistance", observation.url)

            action = self._build_action(decision)
            policy_decision = self.policy.evaluate(action, observation.url)
            self.logger.policy_decision(
                step_id, policy_decision.reason, status=policy_decision.outcome.value,
                detail={"risk_class": policy_decision.risk_class.value},
            )

            if policy_decision.outcome == PolicyOutcome.BLOCK:
                raise PolicyBlockedError(policy_decision.reason)
            if policy_decision.outcome == PolicyOutcome.REQUIRE_CONFIRMATION:
                raise DiscoveryStuckError(
                    f"action requires human confirmation: {policy_decision.reason}", observation.url
                )

            result = await self.surface.act(action)
            evidence_uri = None
            if self.screenshot_dir is not None:
                evidence_uri = str(self.screenshot_dir / f"{step_id}.png")
                await self.surface.screenshot(evidence_uri)
            self.logger.act(step_id, result.message, status="OK" if result.ok else "ERROR", evidence_uri=evidence_uri)

            record = StepRecord(
                id=step_id,
                action=decision.action,
                target_role=decision.target_role,
                target_name=decision.target_name,
                target_css=decision.target_css,
                value=decision.value,
                output_key=decision.output_key,
                extracted_value=result.message if decision.action == "read" else None,
                url=decision.url,
            )
            steps.append(record)
            history_lines.append(f"{step_id}: {decision.action} -> {result.message}")

        raise DiscoveryStuckError("max steps exceeded without the model declaring the goal met")

    def _build_action(self, decision: AgentDecision) -> Action:
        if decision.action == "navigate":
            return Action(type=ActionType.NAVIGATE, url=decision.url, target_description=decision.url or "")

        target_desc = f"css '{decision.target_css}'" if decision.target_css else f"{decision.target_role} '{decision.target_name}'"
        target = _target_from_decision(decision)
        action_type = ActionType(decision.action)
        return Action(type=action_type, target=target, value=decision.value, target_description=target_desc)
