"""
Business-outcome and recoverable-condition detection.

The DEFAULT_RECOVERABLE_PATTERNS list is deliberately app-specific and
small: a real deployment would keep a library per vendor
family/app rather than one global list - see REPORT.md
"Heterogeneity & multi-tenant". Business outcomes come from the
artifact itself (they're part of the reviewed contract); recoverable
patterns are infrastructure-level and shared across capabilities on
the same app.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.models.capability import (
    ActionType,
    BusinessOutcome,
    Condition,
    ConditionType,
    LocatorStrategy,
    Step,
    Target,
    TargetLocator,
)
from src.replay.checkpoint import check_condition
from src.surface.base import Observation, Surface


@dataclass
class RecoverablePattern:
    name: str
    detect: Condition
    recovery: Step


DEFAULT_RECOVERABLE_PATTERNS: list[RecoverablePattern] = [
    RecoverablePattern(
        name="system_notice_interstitial",
        detect=Condition(type=ConditionType.TEXT_PRESENT, text="System Notice"),
        recovery=Step(
            id="_recover_dismiss_notice",
            action=ActionType.ACTIVATE,
            target=Target(primary=TargetLocator(strategy=LocatorStrategy.ROLE, role="button", name="OK")),
            description="dismiss known system notice interstitial",
        ),
    ),
]


async def detect_business_outcome(
    surface: Surface, observation: Observation, outcomes: list[BusinessOutcome]
) -> Optional[BusinessOutcome]:
    for outcome in outcomes:
        if await check_condition(surface, observation, outcome.detect):
            return outcome
    return None


async def detect_recoverable_pattern(
    surface: Surface, observation: Observation, patterns: list[RecoverablePattern]
) -> Optional[RecoverablePattern]:
    for pattern in patterns:
        if await check_condition(surface, observation, pattern.detect):
            return pattern
    return None
