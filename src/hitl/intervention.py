"""Helpers for building an InterventionContext from live state - kept separate from
manager.py's orchestration logic so the "what context does a human need" question has one place."""
from __future__ import annotations

from src.models.intervention import InterventionContext
from src.surface.base import Observation


def build_context(
    goal_or_capability: str, current_step_id: str, observation: Observation, screenshot_path: str, message: str
) -> InterventionContext:
    return InterventionContext(
        goal_or_capability=goal_or_capability,
        current_step_id=current_step_id,
        current_url=observation.url,
        screenshot_path=screenshot_path,
        message=message,
    )
