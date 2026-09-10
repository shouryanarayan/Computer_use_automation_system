"""
Policy engine: the single choke point every proposed action passes
through before it reaches a surface adapter, for both discovery
(LLM-proposed actions) and replay (artifact-recorded actions).
"the LLM/artifact proposes, policy authorizes."
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse

import yaml

from src.models.capability import RiskClass
from src.policy.risk import classify_action_risk
from src.surface.base import Action

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "policy.yaml"


class PolicyOutcome(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    REQUIRE_CONFIRMATION = "REQUIRE_CONFIRMATION"


@dataclass
class PolicyDecision:
    outcome: PolicyOutcome
    risk_class: RiskClass
    reason: str


class PolicyEngine:
    def __init__(self, policy_path: Path = DEFAULT_POLICY_PATH):
        with open(policy_path) as f:
            self.config = yaml.safe_load(f)
        self.allowed_domains = set(self.config.get("allowed_domains", []))
        self.allowed_action_types = set(self.config.get("allowed_action_types", []))
        self.risk_defaults = self.config.get("risk_defaults", {})
        self.irreversible_keywords = self.config.get("irreversible_name_keywords", [])
        self.irreversible_requires_confirmation = bool(
            self.config.get("irreversible_requires_confirmation", True)
        )
        self.max_steps_per_run = int(self.config.get("max_steps_per_run", 25))
        self.step_timeout_seconds = int(self.config.get("step_timeout_seconds", 15))

    def is_domain_allowed(self, url: str) -> bool:
        netloc = urlparse(url).netloc
        return netloc in self.allowed_domains

    def evaluate(self, action: Action, current_url: str) -> PolicyDecision:
        action_type = action.type.value if hasattr(action.type, "value") else str(action.type)

        if action_type not in self.allowed_action_types:
            return PolicyDecision(
                PolicyOutcome.BLOCK, RiskClass.IRREVERSIBLE, f"action type '{action_type}' is not allowlisted"
            )

        target_url = action.url if action.url else current_url
        if not self.is_domain_allowed(target_url):
            return PolicyDecision(
                PolicyOutcome.BLOCK,
                RiskClass.IRREVERSIBLE,
                f"domain '{urlparse(target_url).netloc}' is not in the allowlist",
            )

        risk = classify_action_risk(
            action_type, action.target_description, self.risk_defaults, self.irreversible_keywords
        )

        if risk == RiskClass.IRREVERSIBLE and self.irreversible_requires_confirmation:
            return PolicyDecision(
                PolicyOutcome.REQUIRE_CONFIRMATION,
                risk,
                f"action targets '{action.target_description}', classified IRREVERSIBLE - requires confirmation",
            )

        return PolicyDecision(PolicyOutcome.ALLOW, risk, "within allowlist and risk policy")
