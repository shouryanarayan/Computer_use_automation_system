"""
Execution-run models: the record of one discovery or replay run, and
the append-only event log entries within it. These back the
EXECUTION / EXECUTION_EVENT tables in the registry DB (db/schema.sql)
and are also what observability/logger.py writes to evidence/*.jsonl.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ExecutionMode(str, Enum):
    DISCOVERY = "DISCOVERY"
    REPLAY = "REPLAY"


class EventActor(str, Enum):
    AUTOMATION = "AUTOMATION"
    LLM = "LLM"
    POLICY = "POLICY"
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"


class EventType(str, Enum):
    OBSERVE = "OBSERVE"
    DECIDE = "DECIDE"
    ACT = "ACT"
    CHECKPOINT = "CHECKPOINT"
    POLICY_DECISION = "POLICY_DECISION"
    ERROR = "ERROR"
    INTERVENTION = "INTERVENTION"


class ExecutionEvent(BaseModel):
    event_id: str
    execution_id: str
    step_id: Optional[str] = None
    actor: EventActor
    event_type: EventType
    summary: str
    detail: dict[str, Any] = Field(default_factory=dict)
    status: str = "OK"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    evidence_uri: Optional[str] = None


class Execution(BaseModel):
    execution_id: str
    mode: ExecutionMode
    capability_id: Optional[str] = None
    capability_version: Optional[str] = None
    tenant_id: str = "default"
    status: str = "RUNNING"
    goal: Optional[str] = None
    input_params: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    last_checkpoint: Optional[str] = None
