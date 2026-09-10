"""
Capability artifact schema.

This is the contract between the discovery agent (which produces it)
and the replay engine (which consumes it) - the reusable, typed,
versioned description of a UI flow that an AI agent can invoke by
name. It is deliberately surface-neutral: nothing here is a CSS
selector or a Playwright call. A target is described the way a human
operator would describe it ("the button called Search"), so the same
artifact shape could in principle be driven by a different surface
adapter (legacy web, desktop/accessibility API) without changing the
schema - see docs/architecture.md.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class CapabilityStatus(str, Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class RiskClass(str, Enum):
    READ_ONLY = "READ_ONLY"
    REVERSIBLE = "REVERSIBLE"
    IRREVERSIBLE = "IRREVERSIBLE"


class ParamType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"


class LocatorStrategy(str, Enum):
    """
    How a target control is found on the live surface, in descending
    order of preference when used as a fallback chain. ROLE (an
    accessibility-tree role + accessible name) is preferred because it
    survives markup/CSS changes and is available on both web and
    desktop surfaces. TEXT and CSS are fallbacks for surfaces where
    accessible names are missing or unreliable, which is common on
    legacy server-rendered apps.
    """

    ROLE = "role"  # accessibility role + accessible name
    LABEL = "label"  # associated <label for> text
    TEXT = "text"  # visible text content / proximity
    CSS = "css"  # last-resort raw selector


class TargetLocator(BaseModel):
    strategy: LocatorStrategy
    role: Optional[str] = None
    name: Optional[str] = None
    label: Optional[str] = None
    text: Optional[str] = None
    css: Optional[str] = None

    @model_validator(mode="after")
    def _require_matching_field(self) -> "TargetLocator":
        required = {
            LocatorStrategy.ROLE: "role",
            LocatorStrategy.LABEL: "label",
            LocatorStrategy.TEXT: "text",
            LocatorStrategy.CSS: "css",
        }[self.strategy]
        if getattr(self, required) is None:
            raise ValueError(
                f"locator strategy '{self.strategy}' requires field '{required}'"
            )
        return self


class Target(BaseModel):
    """A target control, expressed as a primary locator plus ordered fallbacks."""

    primary: TargetLocator
    fallbacks: list[TargetLocator] = Field(default_factory=list)

    def strategies_in_order(self) -> list[TargetLocator]:
        return [self.primary, *self.fallbacks]


class ActionType(str, Enum):
    TYPE = "type"
    ACTIVATE = "activate"  # click / press
    READ = "read"  # extract text into an output
    NAVIGATE = "navigate"
    WAIT_FOR = "wait_for"


class Step(BaseModel):
    id: str
    action: ActionType
    description: str = ""
    target: Optional[Target] = None
    # literal value or a "{{param_name}}" template resolved from inputs
    value: Optional[str] = None
    # for READ steps: which declared output this step populates
    output_key: Optional[str] = None
    # for NAVIGATE: relative or absolute URL (surface-resolved)
    url: Optional[str] = None


class InputParam(BaseModel):
    type: ParamType
    required: bool = True
    sensitive: bool = False
    description: str = ""


class OutputSpec(BaseModel):
    type: ParamType
    description: str = ""


class ConditionType(str, Enum):
    ELEMENT_VISIBLE = "element_visible"
    TEXT_PRESENT = "text_present"
    URL_MATCHES = "url_matches"


class Condition(BaseModel):
    """A checkable assertion about the current surface state."""

    type: ConditionType
    target: Optional[Target] = None
    text: Optional[str] = None
    url_pattern: Optional[str] = None


class BusinessOutcome(BaseModel):
    """
    A named, expected non-success result the caller needs to know
    about (e.g. "no such member"). Detected the same way a checkpoint
    is - by a Condition - so replay can distinguish "goal not met
    because of a bug" from "goal not met because of a legitimate
    answer."
    """

    code: str
    description: str
    detect: Condition


class TargetApplication(BaseModel):
    application: str
    vendor_family: str
    supported_versions: str = "*"
    base_url: str


class CapabilityArtifact(BaseModel):
    schema_version: str = "1.0"
    capability_id: str
    version: str
    status: CapabilityStatus = CapabilityStatus.DRAFT

    goal_template: str = Field(
        description="Natural-language goal this capability was discovered from, "
        "with parameter placeholders, e.g. 'Get the savings balance for member {{member_id}}'."
    )

    target: TargetApplication
    inputs: dict[str, InputParam]
    outputs: dict[str, OutputSpec]
    steps: list[Step]
    business_outcomes: list[BusinessOutcome] = Field(default_factory=list)
    success_condition: Condition
    risk_class: RiskClass = RiskClass.READ_ONLY

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str = "discovery_agent"
    discovery_execution_id: Optional[str] = None

    model_config = {"use_enum_values": False}
