"""
Formats a raw Observation into the text the LLM sees. Pattern-based
redaction (SSN/card/credential shapes) is applied even to model input,
as a safety net for data that isn't the task's declared parameters -
but ordinary UI content (names, balances) necessarily reaches the
model, since reading it is the task. See REPORT.md "Safety" for the
reasoning.
"""
from __future__ import annotations

from src.policy.redaction import scrub_text
from src.surface.base import Observation


def format_observation_for_llm(observation: Observation, max_text_chars: int = 2000) -> str:
    text = scrub_text(observation.visible_text)[:max_text_chars]
    controls = scrub_text(observation.accessibility_summary) or "(no interactive controls detected)"

    id_section = ""
    if observation.id_elements:
        lines = [f"  #{e['id']} -> \"{scrub_text(e['text'])}\"" for e in observation.id_elements]
        id_section = (
            "\nElements with a stable id but no accessible role/name of their own "
            "(e.g. a plain data cell in a legacy table) - use target_css=\"#id\" for these, "
            "never target_role/target_name:\n" + "\n".join(lines) + "\n"
        )

    return (
        f"Current URL: {observation.url}\n\n"
        f"Visible controls (role 'accessible name'):\n{controls}\n"
        f"{id_section}\n"
        f"Visible page text:\n{text}"
    )
