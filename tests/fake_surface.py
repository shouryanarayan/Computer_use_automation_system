"""
A small in-memory, scripted Surface implementation used by the replay
tests. Deliberately not Playwright-backed: these tests exist to verify
the executor's own logic (business-outcome detection, recoverable-
interstitial recovery, hard-failure classification, HITL handoff)
fast and deterministically, independent of a real browser. The live
mock bank app + PlaywrightAdapter are exercised separately by the
discovery/replay scripts and are what produced the /evidence/ runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Union

from src.models.capability import ActionType, LocatorStrategy, Target
from src.surface.base import Action, ActionResult, Observation, Surface, TargetNotFoundError


@dataclass
class FakeScreen:
    url: str
    text: str
    controls: list[dict] = field(default_factory=list)  # [{"role", "name"} or {"css", "value"}]


TransitionFn = Callable[[dict[str, str]], str]


class FakeSurface(Surface):
    def __init__(self, screens: dict[str, FakeScreen], transitions: dict[tuple, Union[str, TransitionFn]], start: str):
        self.screens = screens
        self.transitions = transitions
        self.state = start
        self.typed: dict[str, str] = {}

    async def navigate(self, url: str) -> None:
        pass  # start state is fixed at construction time

    async def current_url(self) -> str:
        return self.screens[self.state].url

    async def observe(self) -> Observation:
        s = self.screens[self.state]
        return Observation(url=s.url, visible_text=s.text, accessibility_summary="", controls=s.controls)

    def _control_key(self, target: Target) -> tuple:
        p = target.primary
        if p.strategy == LocatorStrategy.CSS:
            return ("css", p.css)
        return ("role", p.role, p.name)

    def _find_control(self, target: Target) -> dict | None:
        key = self._control_key(target)
        for c in self.screens[self.state].controls:
            if key[0] == "css" and c.get("css") == key[1]:
                return c
            if key[0] == "role" and c.get("role") == key[1] and c.get("name") == key[2]:
                return c
        return None

    async def is_visible(self, target: Target) -> bool:
        return self._find_control(target) is not None

    async def act(self, action: Action) -> ActionResult:
        if action.type == ActionType.NAVIGATE:
            return ActionResult(True, "navigated")

        if action.target is None:
            raise ValueError("action requires a target")

        control = self._find_control(action.target)
        if control is None:
            raise TargetNotFoundError(f"no control for {action.target_description} in state '{self.state}'")

        key = self._control_key(action.target)

        if action.type == ActionType.TYPE:
            typed_key = key[1] if key[0] == "css" else key[2]
            self.typed[typed_key] = action.value or ""
            return ActionResult(True, "typed")

        if action.type == ActionType.READ:
            return ActionResult(True, control.get("value", control.get("name", "")))

        if action.type == ActionType.ACTIVATE:
            trans_key = (self.state, *key)
            if trans_key not in self.transitions:
                raise TargetNotFoundError(f"no scripted transition for {trans_key}")
            next_state = self.transitions[trans_key]
            if callable(next_state):
                next_state = next_state(self.typed)
            self.state = next_state
            return ActionResult(True, f"activated -> {self.state}")

        return ActionResult(True, "")

    async def screenshot(self, path: str) -> str:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"fake-screenshot")
        return path

    async def close(self) -> None:
        pass
