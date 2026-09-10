#!/usr/bin/env python
"""
Promotes a capability's most recent DRAFT version to APPROVED, making
it the current version replay will use. Stands in for a human
reviewer's sign-off (see docs/capability-lifecycle.md) - a real system
would gate this on passing deterministic replay validation runs first.

    python scripts/promote_artifact.py member.get_savings_balance
"""
from __future__ import annotations

import sys

from common import get_repository

from src.models.capability import CapabilityStatus
from src.registry.capability_registry import CapabilityRegistry


def main(capability_id: str, approved_by: str) -> int:
    repo = get_repository()
    registry = CapabilityRegistry(repo)

    versions = registry.list_versions(capability_id)
    if not versions:
        print(f"No versions found for capability '{capability_id}'", file=sys.stderr)
        return 1

    latest = versions[0]  # list_versions orders by created_at DESC
    print(f"Promoting version {latest['version']} ({latest['capability_version_id']}) status={latest['status']} -> APPROVED")

    registry.promote(latest["capability_version_id"], capability_id, CapabilityStatus.APPROVED, approved_by=approved_by)
    print("Done. This version is now the current replayable version.")
    repo.close()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python scripts/promote_artifact.py <capability_id> [approved_by]", file=sys.stderr)
        sys.exit(1)
    cap_id = sys.argv[1]
    reviewer = sys.argv[2] if len(sys.argv) > 2 else "reviewer@interface.ai"
    sys.exit(main(cap_id, reviewer))
