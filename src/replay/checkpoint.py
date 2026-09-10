"""
Evaluates a Condition (src/models/capability.py) against the live
surface. The same primitive backs two uses that must share behavior:
verifying the capability's declared success_condition, and detecting
a declared business_outcome - both are just "is this assertion about
the current screen true right now."
"""
from __future__ import annotations

import re

from src.models.capability import Condition, ConditionType
from src.surface.base import Observation, Surface


async def check_condition(surface: Surface, observation: Observation, condition: Condition) -> bool:
    if condition.type == ConditionType.TEXT_PRESENT:
        return bool(condition.text) and condition.text in observation.visible_text

    if condition.type == ConditionType.URL_MATCHES:
        return bool(condition.url_pattern) and re.search(condition.url_pattern, observation.url) is not None

    if condition.type == ConditionType.ELEMENT_VISIBLE:
        if condition.target is None:
            raise ValueError("ELEMENT_VISIBLE condition requires a target")
        return await surface.is_visible(condition.target)

    raise ValueError(f"unknown condition type: {condition.type}")
