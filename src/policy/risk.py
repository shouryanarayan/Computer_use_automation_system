"""Risk classification for proposed actions - see config/policy.yaml."""
from __future__ import annotations

from src.models.capability import RiskClass


def classify_action_risk(
    action_type: str,
    target_name: str | None,
    risk_defaults: dict[str, str],
    irreversible_keywords: list[str],
) -> RiskClass:
    """
    Default risk class for an action, upgraded to IRREVERSIBLE if the
    target control's accessible name matches a known irreversible
    keyword (e.g. "Submit", "Delete", "Approve"). Conservative by
    design: an unrecognized action_type defaults to REVERSIBLE, never
    READ_ONLY.
    """
    base = risk_defaults.get(action_type, "REVERSIBLE")
    risk = RiskClass(base)

    if target_name:
        lowered = target_name.lower()
        if any(keyword.lower() in lowered for keyword in irreversible_keywords):
            return RiskClass.IRREVERSIBLE

    return risk
