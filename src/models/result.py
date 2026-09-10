"""
Result contract returned by the replay engine (and, for the
discovery run, by the discovery agent).

Three shapes are deliberately kept distinct rather than collapsed into
a boolean success/fail, per the brief's error taxonomy:

  SUCCESS         - goal met, declared outputs populated.
  BUSINESS_OUTCOME- a legitimate, named non-success answer (e.g. member
                     not found). Not an error - the caller needs this
                     value, not a retry.
  HARD_FAILURE    - replay could not proceed and could not classify the
                     state as a known business outcome; carries enough
                     detail (step, expected, observed) to debug.
  ESCALATED       - execution was handed to a human operator; carries
                     the intervention id so the caller can track it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ExecutionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    BUSINESS_OUTCOME = "BUSINESS_OUTCOME"
    HARD_FAILURE = "HARD_FAILURE"
    ESCALATED = "ESCALATED"


class ErrorCode(str, Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    UNKNOWN_STATE = "UNKNOWN_STATE"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    CHECKPOINT_NOT_MET = "CHECKPOINT_NOT_MET"
    TIMEOUT = "TIMEOUT"
    POLICY_BLOCKED = "POLICY_BLOCKED"


class ErrorDetail(BaseModel):
    code: ErrorCode
    failed_step: Optional[str] = None
    expected: str
    observed: str


class ExecutionResult(BaseModel):
    execution_id: str
    capability_id: str
    version: str
    status: ExecutionStatus

    outputs: dict[str, Any] = Field(default_factory=dict)
    outcome_code: Optional[str] = None  # set when status == BUSINESS_OUTCOME
    error: Optional[ErrorDetail] = None  # set when status == HARD_FAILURE
    intervention_id: Optional[str] = None  # set when status == ESCALATED

    last_verified_checkpoint: Optional[str] = None
    evidence_reference: Optional[str] = None

    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
