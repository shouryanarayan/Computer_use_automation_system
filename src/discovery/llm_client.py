"""
Anthropic Claude client for the discovery agent's "decide" step.

Uses forced tool-use (tool_choice) so every model turn returns one
structured action rather than free text - this is the seam that keeps
the LLM's output surface-neutral (it names a role/accessible-name, not
a CSS selector or coordinate) so the same decision shape can later be
replayed by any Surface implementation.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

import anthropic

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

_TOOL_SCHEMA = {
    "name": "agent_action",
    "description": "The single next action to take on the current screen, or a decision to finish or ask for human help.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reasoning": {
                "type": "string",
                "description": "One or two sentences on why this action moves toward the goal.",
            },
            "action": {
                "type": "string",
                "enum": ["type", "activate", "read", "navigate", "wait_for", "request_human", "finish"],
            },
            "target_role": {
                "type": "string",
                "description": "Accessibility role of the target control, e.g. 'textbox', 'button'. Required for type/activate/read/wait_for.",
            },
            "target_name": {
                "type": "string",
                "description": "Accessible name of the target control, exactly as shown in the observation. Required for type/activate/read/wait_for, unless target_css is used instead.",
            },
            "target_css": {
                "type": "string",
                "description": "CSS selector (e.g. '#savings-balance'), used INSTEAD of target_role/target_name only for elements listed under 'Elements with a stable id' in the observation - i.e. a value with no accessible role/name of its own.",
            },
            "value": {
                "type": "string",
                "description": "Text to type, for action=type.",
            },
            "output_key": {
                "type": "string",
                "description": "For action=read: the output variable name this value should be recorded under, e.g. 'savings_balance'.",
            },
            "url": {
                "type": "string",
                "description": "Absolute URL to navigate to, for action=navigate.",
            },
            "outputs": {
                "type": "object",
                "description": "For action=finish: the final key/value outputs extracted during the run.",
            },
            "reason": {
                "type": "string",
                "description": "For action=request_human: why the agent cannot safely proceed.",
            },
        },
        "required": ["reasoning", "action"],
    },
}

SYSTEM_PROMPT = """You are a computer-use agent operating a back-office banking application \
on behalf of an authorized automation system. You interact with the application exactly the way \
a trained human operator would: by reading the visible controls and text, then acting on one \
control at a time.

Rules:
- Take exactly ONE action per turn, always via the agent_action tool.
- Refer to controls only by their accessibility role and accessible name, exactly as given in the \
observation (e.g. role 'button', name 'Search'). Never invent a control that isn't listed.
- If the screen shows a dismissible notice/interstitial unrelated to the goal, dismiss it first.
- Use action=read to extract a value the goal asks for, and record it under an appropriate output_key.
- Most controls have a usable accessible role + name - reference those with target_role/target_name. \
If the value you need is a plain data cell with no role/name of its own (common in legacy label/value \
tables), the observation will list it under "Elements with a stable id"; for exactly those, use \
target_css="#id" instead of target_role/target_name.
- Use action=finish only once the goal is fully satisfied; include every requested output in `outputs`.
- If the screen is one you do not recognize and cannot safely proceed (e.g. an unexpected \
verification/approval prompt outside the normal flow), use action=request_human with a clear reason \
rather than guessing.
- Never take an action outside the current application."""


@dataclass
class AgentDecision:
    reasoning: str
    action: str
    target_role: Optional[str] = None
    target_name: Optional[str] = None
    target_css: Optional[str] = None
    value: Optional[str] = None
    output_key: Optional[str] = None
    url: Optional[str] = None
    outputs: dict[str, Any] = field(default_factory=dict)
    reason: Optional[str] = None


class LLMClient:
    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        self.client = anthropic.Anthropic()

    def decide(self, goal: str, observation_text: str, history_summary: str, max_tokens: int = 1024) -> AgentDecision:
        user_message = (
            f"GOAL:\n{goal}\n\n"
            f"STEPS TAKEN SO FAR:\n{history_summary or '(none yet)'}\n\n"
            f"CURRENT SCREEN:\n{observation_text}\n\n"
            "What is the single next action?"
        )
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            tools=[_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "agent_action"},
            messages=[{"role": "user", "content": user_message}],
        )
        for block in response.content:
            if block.type == "tool_use":
                data = block.input
                return AgentDecision(
                    reasoning=data.get("reasoning", ""),
                    action=data["action"],
                    target_role=data.get("target_role"),
                    target_name=data.get("target_name"),
                    target_css=data.get("target_css"),
                    value=data.get("value"),
                    output_key=data.get("output_key"),
                    url=data.get("url"),
                    outputs=data.get("outputs", {}) or {},
                    reason=data.get("reason"),
                )
        raise RuntimeError("model response contained no tool_use block")
