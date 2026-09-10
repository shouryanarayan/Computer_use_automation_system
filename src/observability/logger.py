"""
Structured run logger: one JSONL file per run under evidence/<scenario>/run.jsonl,
mirrored into the execution_event table when a Repository is attached
(so the same events are both human-readable evidence and queryable
state via the registry).

Every event is redaction-scrubbed on the way out (src/policy/redaction)
- this is the single choke point where discovery/replay/HITL output
touches disk or the DB, so it is the natural place to enforce "never
persist secrets or raw sensitive data into logs."
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.models.execution import EventActor, EventType
from src.policy.redaction import scrub_text
from src.registry.repository import Repository


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunLogger:
    def __init__(self, execution_id: str, evidence_dir: Path, repo: Optional[Repository] = None):
        self.execution_id = execution_id
        self.evidence_dir = evidence_dir
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.evidence_dir / "run.jsonl"
        self.repo = repo

    def _write(
        self,
        actor: EventActor,
        event_type: EventType,
        summary: str,
        detail: dict[str, Any],
        status: str = "OK",
        step_id: Optional[str] = None,
        evidence_uri: Optional[str] = None,
    ) -> None:
        event = {
            "event_id": str(uuid.uuid4()),
            "execution_id": self.execution_id,
            "step_id": step_id,
            "actor": actor.value,
            "event_type": event_type.value,
            "summary": scrub_text(summary),
            "detail": detail,
            "status": status,
            "timestamp": _now_iso(),
            "evidence_uri": evidence_uri,
        }
        with open(self.log_path, "a") as f:
            f.write(json.dumps(event, default=str) + "\n")
        if self.repo:
            self.repo.insert_event(
                event_id=event["event_id"],
                execution_id=self.execution_id,
                step_id=step_id,
                actor=actor.value,
                event_type=event_type.value,
                summary=event["summary"],
                detail=detail,
                status=status,
                timestamp=event["timestamp"],
                evidence_uri=evidence_uri,
            )

    def observe(self, step_id: Optional[str], summary: str, detail: Optional[dict] = None) -> None:
        self._write(EventActor.AUTOMATION, EventType.OBSERVE, summary, detail or {}, step_id=step_id)

    def decide(self, step_id: Optional[str], summary: str, detail: Optional[dict] = None) -> None:
        self._write(EventActor.LLM, EventType.DECIDE, summary, detail or {}, step_id=step_id)

    def act(
        self, step_id: Optional[str], summary: str, detail: Optional[dict] = None, status: str = "OK",
        evidence_uri: Optional[str] = None,
    ) -> None:
        self._write(EventActor.AUTOMATION, EventType.ACT, summary, detail or {}, status=status, step_id=step_id, evidence_uri=evidence_uri)

    def checkpoint(self, step_id: Optional[str], summary: str, status: str, detail: Optional[dict] = None) -> None:
        self._write(EventActor.AUTOMATION, EventType.CHECKPOINT, summary, detail or {}, status=status, step_id=step_id)

    def policy_decision(self, step_id: Optional[str], summary: str, status: str = "OK", detail: Optional[dict] = None) -> None:
        self._write(EventActor.POLICY, EventType.POLICY_DECISION, summary, detail or {}, status=status, step_id=step_id)

    def error(self, step_id: Optional[str], summary: str, detail: Optional[dict] = None, evidence_uri: Optional[str] = None) -> None:
        self._write(EventActor.SYSTEM, EventType.ERROR, summary, detail or {}, status="ERROR", step_id=step_id, evidence_uri=evidence_uri)

    def intervention(self, step_id: Optional[str], summary: str, detail: Optional[dict] = None, actor: EventActor = EventActor.SYSTEM) -> None:
        self._write(actor, EventType.INTERVENTION, summary, detail or {}, step_id=step_id)

    def human_action(self, step_id: Optional[str], summary: str, detail: Optional[dict] = None) -> None:
        self._write(EventActor.HUMAN, EventType.ACT, summary, detail or {}, step_id=step_id)
