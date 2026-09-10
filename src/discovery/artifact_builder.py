"""
Converts a successful discovery run into a reusable capability
artifact (src/models/capability.py).

This is deliberately a separate step from the discovery loop itself,
not something the LLM emits directly: the raw transcript is a series
of concrete actions against one concrete input (e.g. typing the literal
string "12345"). The artifact needs to be a *parameterized* capability
- "12345" becomes "{{member_id}}" - with an explicitly authored output
contract (types the LLM's free-form `outputs` dict doesn't carry) and
a success condition/business-outcome set that a reviewer chooses
deliberately rather than the model inferring implicitly. Decoupling
artifact-from-transcript is what keeps the artifact reviewable by a
human independent of trusting the model's judgment about its own run.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.discovery.agent import DiscoveryResult
from src.models.capability import (
    ActionType,
    BusinessOutcome,
    CapabilityArtifact,
    CapabilityStatus,
    Condition,
    InputParam,
    LocatorStrategy,
    OutputSpec,
    RiskClass,
    Step,
    Target,
    TargetApplication,
    TargetLocator,
)


@dataclass
class ParamHint:
    """Maps a declared input parameter to the concrete value used during this discovery run."""

    value: str
    spec: InputParam


class ArtifactBuildError(Exception):
    pass


def build_artifact(
    capability_id: str,
    version: str,
    goal_template: str,
    discovery_result: DiscoveryResult,
    param_hints: dict[str, ParamHint],
    outputs: dict[str, OutputSpec],
    target_app: TargetApplication,
    success_condition: Condition,
    business_outcomes: list[BusinessOutcome],
    risk_class: RiskClass,
    discovery_execution_id: str,
) -> CapabilityArtifact:
    if not discovery_result.success:
        raise ArtifactBuildError("cannot build an artifact from a failed discovery run")

    declared_output_keys = set(outputs.keys())
    produced_output_keys = {r.output_key for r in discovery_result.steps if r.action == "read" and r.output_key}
    missing = declared_output_keys - produced_output_keys
    if missing:
        raise ArtifactBuildError(
            f"declared outputs {missing} were never populated by a 'read' step in the discovery run"
        )

    steps: list[Step] = []
    for record in discovery_result.steps:
        if record.action == "navigate":
            steps.append(
                Step(id=record.id, action=ActionType.NAVIGATE, url=record.url, description=f"navigate to {record.url}")
            )
            continue

        if record.target_css:
            target = Target(primary=TargetLocator(strategy=LocatorStrategy.CSS, css=record.target_css))
            target_desc = f"css '{record.target_css}'"
        else:
            target = Target(
                primary=TargetLocator(strategy=LocatorStrategy.ROLE, role=record.target_role, name=record.target_name)
            )
            target_desc = f"{record.target_role} '{record.target_name}'"

        value = record.value
        for name, hint in param_hints.items():
            if value is not None and value == hint.value:
                value = "{{" + name + "}}"
                break

        steps.append(
            Step(
                id=record.id,
                action=ActionType(record.action),
                target=target,
                value=value,
                output_key=record.output_key,
                description=f"{record.action} {target_desc}",
            )
        )

    return CapabilityArtifact(
        capability_id=capability_id,
        version=version,
        status=CapabilityStatus.DRAFT,
        goal_template=goal_template,
        target=target_app,
        inputs={name: hint.spec for name, hint in param_hints.items()},
        outputs=outputs,
        steps=steps,
        business_outcomes=business_outcomes,
        success_condition=success_condition,
        risk_class=risk_class,
        discovery_execution_id=discovery_execution_id,
    )
