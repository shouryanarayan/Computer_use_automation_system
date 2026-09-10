"""
Redaction utilities. Two layers, per the brief's "never persist
secrets or raw sensitive data into artifacts or logs":

  1. Declared-sensitive redaction: capability inputs marked
     `sensitive: true` (e.g. member_id counts as PII in this domain)
     are masked by key before any log/DB write, regardless of content.
  2. Pattern-based scrubbing: a secondary net over free-text captured
     from the live surface (accessibility tree dumps, visible text),
     which may contain regulated data the artifact schema never
     declared as a parameter. Catches obvious shapes (SSN, card
     number, credential-looking key=value pairs) even if we didn't
     anticipate them.

Neither layer is a substitute for the other: (1) is precise but only
covers what the schema already knows about; (2) is a blunt fallback
for what it doesn't.
"""
from __future__ import annotations

import re
from typing import Any

REDACTED = "***REDACTED***"

_PATTERNS = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN
    re.compile(r"\b(?:\d[ -]*?){13,19}\b"),  # card-number-shaped digit runs
    re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|token)\s*[:=]\s*\S+"),
]


def redact_params(params: dict[str, Any], sensitive_keys: set[str]) -> dict[str, Any]:
    """Mask declared-sensitive keys in a shallow params dict before logging/persisting."""
    return {k: (REDACTED if k in sensitive_keys else v) for k, v in params.items()}


def scrub_text(text: str) -> str:
    """Best-effort pattern scrub of free text pulled from a live surface."""
    if not text:
        return text
    scrubbed = text
    for pattern in _PATTERNS:
        scrubbed = pattern.sub(REDACTED, scrubbed)
    return scrubbed


def scrub_observation(observation: dict[str, Any]) -> dict[str, Any]:
    """Apply scrub_text to the free-text fields of an observation dict before logging."""
    result = dict(observation)
    if "visible_text" in result and isinstance(result["visible_text"], str):
        result["visible_text"] = scrub_text(result["visible_text"])
    if "accessibility_tree" in result and isinstance(result["accessibility_tree"], str):
        result["accessibility_tree"] = scrub_text(result["accessibility_tree"])
    return result
