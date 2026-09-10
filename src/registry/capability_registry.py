"""
Domain-level API over the registry DB. This is what discovery, replay,
and the capability-interface layer actually call - they never touch
Repository/sqlite directly. Responsible for the DRAFT -> VALIDATED ->
APPROVED -> ACTIVE -> RETIRED lifecycle (see docs/capability-lifecycle.md)
and for resolving "the artifact a replay should use" independently of
how it's stored.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from src.models.capability import CapabilityArtifact, CapabilityStatus
from src.registry.repository import Repository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CapabilityNotFoundError(Exception):
    pass


class CapabilityRegistry:
    def __init__(self, repo: Repository):
        self.repo = repo

    def save_draft(self, artifact: CapabilityArtifact) -> str:
        """
        Persist a newly-discovered artifact as a new DRAFT version.
        Does not make it current/replayable - see promote().
        """
        self.repo.upsert_capability(
            capability_id=artifact.capability_id,
            name=artifact.capability_id,
            vendor_family=artifact.target.vendor_family,
            description=artifact.goal_template,
        )
        capability_version_id = str(uuid.uuid4())
        self.repo.insert_capability_version(
            capability_version_id=capability_version_id,
            capability_id=artifact.capability_id,
            version=artifact.version,
            artifact_json=artifact.model_dump_json(),
            status=CapabilityStatus.DRAFT.value,
            created_at=_now(),
            is_current=False,
        )
        return capability_version_id

    def promote(
        self, capability_version_id: str, capability_id: str, new_status: CapabilityStatus, approved_by: Optional[str] = None
    ) -> None:
        """
        Move a version forward in its lifecycle. Promoting to APPROVED
        or ACTIVE also makes it the current version for the capability
        - i.e. the one get_replayable_artifact() will return. This is
        a deliberate, separate step from discovery: an LLM producing a
        working draft does not by itself make it safe for unattended
        replay (see REPORT.md "Safety").
        """
        approved_at = _now() if new_status in (CapabilityStatus.APPROVED, CapabilityStatus.ACTIVE) else None
        self.repo.update_version_status(
            capability_version_id, new_status.value, approved_by=approved_by, approved_at=approved_at
        )
        if new_status in (CapabilityStatus.APPROVED, CapabilityStatus.ACTIVE):
            self.repo.set_current_version(capability_id, capability_version_id)

    @staticmethod
    def _artifact_from_row(row) -> CapabilityArtifact:
        # The DB's status column, not the embedded JSON, is authoritative for lifecycle
        # state: promote() only updates the column, so the serialized artifact_json can
        # otherwise go stale (still reading DRAFT after a promotion to APPROVED).
        artifact = CapabilityArtifact.model_validate_json(row["artifact_json"])
        return artifact.model_copy(update={"status": CapabilityStatus(row["status"])})

    def get_replayable_artifact(self, capability_id: str) -> CapabilityArtifact:
        """The artifact an AI agent's replay call would actually invoke: the current APPROVED/ACTIVE version."""
        row = self.repo.get_approved_current_version_row(capability_id)
        if row is None:
            raise CapabilityNotFoundError(
                f"No approved, current version of capability '{capability_id}' found."
            )
        return self._artifact_from_row(row)

    def get_version(self, capability_version_id: str) -> Optional[CapabilityArtifact]:
        cur = self.repo._conn.execute(
            "SELECT * FROM capability_version WHERE capability_version_id = ?", (capability_version_id,)
        )
        row = cur.fetchone()
        return self._artifact_from_row(row) if row else None

    def list_versions(self, capability_id: str) -> list[dict]:
        return [dict(row) for row in self.repo.list_capability_versions(capability_id)]
