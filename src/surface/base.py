"""
Surface abstraction: the seam between "how we perceive/act on a
computer surface" and the recorded flow / discovery loop.

Discovery and replay only ever talk to this interface - never to
Playwright, an OS accessibility API, or anything surface-specific
directly. A concrete adapter (PlaywrightAdapter today; a legacy-web or
desktop adapter later) implements it once per surface technology. See
docs/architecture.md "Surface abstraction" for how this is meant to
extend beyond the one surface implemented here.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from src.models.capability import ActionType, Target


@dataclass
class Action:
    type: ActionType
    target: Optional[Target] = None
    value: Optional[str] = None
    url: Optional[str] = None
    # Human-readable name of the target control (e.g. "button 'Search'"),
    # independent of which locator strategy resolves it. Used for
    # policy risk classification and logging even when target is None
    # (e.g. NAVIGATE).
    target_description: str = ""


@dataclass
class ActionResult:
    ok: bool
    message: str = ""


@dataclass
class Observation:
    url: str
    visible_text: str
    accessibility_summary: str  # readable "role 'name'" lines, not raw markup
    controls: list[dict] = field(default_factory=list)  # [{"role":..., "name":...}]
    # Elements with a stable id but no usable accessible role/name of their own
    # (e.g. a plain <td> in a legacy label/value table). Exposed separately so
    # the discovery agent can fall back to a CSS(#id) target deliberately,
    # rather than the observation silently omitting them. See
    # src/discovery/llm_client.py target_css.
    id_elements: list[dict] = field(default_factory=list)  # [{"id":..., "text":...}]
    screenshot_path: Optional[str] = None


class TargetNotFoundError(Exception):
    """No locator in a Target's strategy chain (primary or fallbacks) resolved to an element."""


class Surface(ABC):
    @abstractmethod
    async def navigate(self, url: str) -> None: ...

    @abstractmethod
    async def observe(self) -> Observation: ...

    @abstractmethod
    async def act(self, action: Action) -> ActionResult: ...

    @abstractmethod
    async def is_visible(self, target: Target) -> bool:
        """Whether a target resolves to a currently-visible element, for checkpoint/outcome conditions."""

    @abstractmethod
    async def screenshot(self, path: str) -> str: ...

    @abstractmethod
    async def current_url(self) -> str: ...

    @abstractmethod
    async def close(self) -> None: ...
