"""Policy engine: allowlist enforcement and risk-based confirmation gating."""
from __future__ import annotations

from src.models.capability import ActionType, LocatorStrategy, Target, TargetLocator
from src.policy.engine import PolicyEngine, PolicyOutcome
from src.surface.base import Action


def test_allowed_domain_and_read_action_is_allowed():
    policy = PolicyEngine()
    action = Action(
        type=ActionType.READ,
        target=Target(primary=TargetLocator(strategy=LocatorStrategy.ROLE, role="cell", name="Savings")),
        target_description="cell 'Savings'",
    )
    decision = policy.evaluate(action, current_url="http://127.0.0.1:8000/member/12345")
    assert decision.outcome == PolicyOutcome.ALLOW


def test_navigate_outside_allowlist_is_blocked():
    policy = PolicyEngine()
    action = Action(type=ActionType.NAVIGATE, url="https://evil.example.com/", target_description="https://evil.example.com/")
    decision = policy.evaluate(action, current_url="http://127.0.0.1:8000/")
    assert decision.outcome == PolicyOutcome.BLOCK


def test_disallowed_action_type_is_blocked():
    policy = PolicyEngine()
    policy.allowed_action_types = {"read"}  # simulate a tighter config
    action = Action(
        type=ActionType.ACTIVATE,
        target=Target(primary=TargetLocator(strategy=LocatorStrategy.ROLE, role="button", name="Search")),
        target_description="button 'Search'",
    )
    decision = policy.evaluate(action, current_url="http://127.0.0.1:8000/")
    assert decision.outcome == PolicyOutcome.BLOCK


def test_irreversible_keyword_requires_confirmation():
    policy = PolicyEngine()
    action = Action(
        type=ActionType.ACTIVATE,
        target=Target(primary=TargetLocator(strategy=LocatorStrategy.ROLE, role="button", name="Confirm Transfer")),
        target_description="button 'Confirm Transfer'",
    )
    decision = policy.evaluate(action, current_url="http://127.0.0.1:8000/")
    assert decision.outcome == PolicyOutcome.REQUIRE_CONFIRMATION
    assert decision.risk_class.value == "IRREVERSIBLE"


def test_ordinary_click_does_not_require_confirmation():
    policy = PolicyEngine()
    action = Action(
        type=ActionType.ACTIVATE,
        target=Target(primary=TargetLocator(strategy=LocatorStrategy.ROLE, role="button", name="Search")),
        target_description="button 'Search'",
    )
    decision = policy.evaluate(action, current_url="http://127.0.0.1:8000/")
    assert decision.outcome == PolicyOutcome.ALLOW
