"""Validates the capability artifact schema's own invariants."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models.capability import (
    ActionType,
    CapabilityArtifact,
    Condition,
    ConditionType,
    InputParam,
    LocatorStrategy,
    OutputSpec,
    ParamType,
    Step,
    Target,
    TargetApplication,
    TargetLocator,
)


def test_role_locator_requires_role_field():
    with pytest.raises(ValidationError):
        TargetLocator(strategy=LocatorStrategy.ROLE, name="Search")  # missing role=


def test_css_locator_requires_css_field():
    with pytest.raises(ValidationError):
        TargetLocator(strategy=LocatorStrategy.CSS)


def test_valid_locator_constructs():
    loc = TargetLocator(strategy=LocatorStrategy.ROLE, role="button", name="Search")
    assert loc.role == "button"


def test_target_strategies_in_order_includes_fallbacks():
    target = Target(
        primary=TargetLocator(strategy=LocatorStrategy.ROLE, role="button", name="Search"),
        fallbacks=[TargetLocator(strategy=LocatorStrategy.CSS, css="#search-btn")],
    )
    order = target.strategies_in_order()
    assert len(order) == 2
    assert order[0].strategy == LocatorStrategy.ROLE
    assert order[1].strategy == LocatorStrategy.CSS


def _minimal_artifact(**overrides) -> CapabilityArtifact:
    defaults = dict(
        capability_id="member.get_savings_balance",
        version="1.0",
        goal_template="Get the savings balance for member {{member_id}}",
        target=TargetApplication(application="mock_core_banking", vendor_family="demo_vendor", base_url="http://127.0.0.1:8000"),
        inputs={"member_id": InputParam(type=ParamType.STRING, required=True, sensitive=True)},
        outputs={"savings_balance": OutputSpec(type=ParamType.DECIMAL)},
        steps=[Step(id="s0", action=ActionType.READ, target=Target(primary=TargetLocator(strategy=LocatorStrategy.CSS, css="#savings-balance")), output_key="savings_balance")],
        success_condition=Condition(type=ConditionType.TEXT_PRESENT, text="Savings"),
    )
    defaults.update(overrides)
    return CapabilityArtifact(**defaults)


def test_minimal_artifact_round_trips_through_json():
    artifact = _minimal_artifact()
    dumped = artifact.model_dump_json()
    restored = CapabilityArtifact.model_validate_json(dumped)
    assert restored == artifact


def test_artifact_defaults_to_draft_status_and_read_only_risk():
    artifact = _minimal_artifact()
    assert artifact.status.value == "DRAFT"
    assert artifact.risk_class.value == "READ_ONLY"


def test_artifact_rejects_missing_required_field():
    with pytest.raises(ValidationError):
        CapabilityArtifact(capability_id="x", version="1.0")
