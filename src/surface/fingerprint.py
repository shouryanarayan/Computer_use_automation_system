"""
Lightweight structural fingerprint of an observed screen, for
per-tenant/version drift detection (REPORT.md "Heterogeneity &
multi-tenant").

Deliberately minimal: a hash of the sorted (role, name) pairs visible
on the page. It is computed and logged on every discovery and replay
step (see observability/logger.py) but does not gate pass/fail in this
prototype - a full drift-management system would store the fingerprint
captured at recording time on the artifact/step and alert (not
necessarily fail) when a tenant's live fingerprint diverges beyond a
threshold. See REPORT.md "Cuts" for what's left unbuilt here.
"""
from __future__ import annotations

import hashlib

from src.surface.base import Observation


def compute_fingerprint(observation: Observation) -> str:
    normalized = sorted(
        f"{c.get('role')}:{c.get('name')}" for c in observation.controls if c.get("name")
    )
    joined = "|".join(normalized)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]
