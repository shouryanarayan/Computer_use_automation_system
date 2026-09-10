"""
Thin persistence layer over SQLite. Deliberately not an ORM: the
domain objects are already typed Pydantic models (src/models/*), so
this module's job is just to serialize/deserialize them to/from rows -
using sqlite3 directly keeps that mapping visible and avoids a second,
parallel set of ORM model classes.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "db" / "schema.sql"
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "db" / "registry.db"


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    conn = get_connection(db_path)
    try:
        with open(SCHEMA_PATH) as f:
            conn.executescript(f.read())
        conn.commit()
    finally:
        conn.close()


class Repository:
    """One instance wraps one SQLite connection for the process lifetime."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        init_db(db_path)
        self._conn = get_connection(db_path)

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    # -- capability ---------------------------------------------------

    def upsert_capability(self, capability_id: str, name: str, vendor_family: str, description: str = "") -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO capability (capability_id, name, vendor_family, description)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(capability_id) DO UPDATE SET
                    name=excluded.name, vendor_family=excluded.vendor_family, description=excluded.description
                """,
                (capability_id, name, vendor_family, description),
            )

    # -- capability_version --------------------------------------------

    def insert_capability_version(
        self,
        capability_version_id: str,
        capability_id: str,
        version: str,
        artifact_json: str,
        status: str,
        created_at: str,
        is_current: bool = False,
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO capability_version
                    (capability_version_id, capability_id, version, artifact_json, status, is_current, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (capability_version_id, capability_id, version, artifact_json, status, int(is_current), created_at),
            )

    def set_current_version(self, capability_id: str, capability_version_id: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE capability_version SET is_current = 0 WHERE capability_id = ?",
                (capability_id,),
            )
            cur.execute(
                "UPDATE capability_version SET is_current = 1 WHERE capability_version_id = ?",
                (capability_version_id,),
            )

    def update_version_status(
        self, capability_version_id: str, status: str, approved_by: Optional[str] = None, approved_at: Optional[str] = None
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE capability_version SET status = ?, approved_by = COALESCE(?, approved_by), "
                "approved_at = COALESCE(?, approved_at) WHERE capability_version_id = ?",
                (status, approved_by, approved_at, capability_version_id),
            )

    def get_current_version_row(self, capability_id: str) -> Optional[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM capability_version WHERE capability_id = ? AND is_current = 1",
            (capability_id,),
        )
        return cur.fetchone()

    def get_approved_current_version_row(self, capability_id: str) -> Optional[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM capability_version WHERE capability_id = ? AND is_current = 1 "
            "AND status IN ('APPROVED', 'ACTIVE')",
            (capability_id,),
        )
        return cur.fetchone()

    def list_capability_versions(self, capability_id: str) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM capability_version WHERE capability_id = ? ORDER BY created_at DESC",
            (capability_id,),
        )
        return cur.fetchall()

    def list_capabilities(self) -> list[sqlite3.Row]:
        cur = self._conn.execute("SELECT * FROM capability")
        return cur.fetchall()

    # -- execution -------------------------------------------------------

    def insert_execution(
        self,
        execution_id: str,
        mode: str,
        capability_id: Optional[str],
        capability_version_id: Optional[str],
        tenant_id: str,
        status: str,
        goal: Optional[str],
        input_params_json: str,
        started_at: str,
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO execution
                    (execution_id, mode, capability_id, capability_version_id, tenant_id,
                     status, goal, input_params_json, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id, mode, capability_id, capability_version_id, tenant_id,
                    status, goal, input_params_json, started_at,
                ),
            )

    def update_execution_status(
        self, execution_id: str, status: str, completed_at: Optional[str] = None, last_checkpoint: Optional[str] = None
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE execution SET status = ?, completed_at = COALESCE(?, completed_at), "
                "last_checkpoint = COALESCE(?, last_checkpoint) WHERE execution_id = ?",
                (status, completed_at, last_checkpoint, execution_id),
            )

    def get_execution_row(self, execution_id: str) -> Optional[sqlite3.Row]:
        cur = self._conn.execute("SELECT * FROM execution WHERE execution_id = ?", (execution_id,))
        return cur.fetchone()

    def list_executions(self, capability_id: Optional[str] = None) -> list[sqlite3.Row]:
        if capability_id:
            cur = self._conn.execute(
                "SELECT * FROM execution WHERE capability_id = ? ORDER BY started_at DESC", (capability_id,)
            )
        else:
            cur = self._conn.execute("SELECT * FROM execution ORDER BY started_at DESC")
        return cur.fetchall()

    # -- execution_event --------------------------------------------------

    def insert_event(
        self,
        event_id: str,
        execution_id: str,
        step_id: Optional[str],
        actor: str,
        event_type: str,
        summary: str,
        detail: dict,
        status: str,
        timestamp: str,
        evidence_uri: Optional[str],
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO execution_event
                    (event_id, execution_id, step_id, actor, event_type, summary, detail_json,
                     status, timestamp, evidence_uri)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, execution_id, step_id, actor, event_type, summary,
                    json.dumps(detail), status, timestamp, evidence_uri,
                ),
            )

    def list_events(self, execution_id: str) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM execution_event WHERE execution_id = ? ORDER BY timestamp ASC", (execution_id,)
        )
        return cur.fetchall()

    # -- intervention ------------------------------------------------------

    def insert_intervention(
        self,
        intervention_id: str,
        execution_id: str,
        capability_id: Optional[str],
        reason: str,
        context_json: str,
        control_state: str,
        requested_at: str,
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO intervention
                    (intervention_id, execution_id, capability_id, reason, context_json, control_state, requested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (intervention_id, execution_id, capability_id, reason, context_json, control_state, requested_at),
            )

    def update_intervention(
        self,
        intervention_id: str,
        control_state: Optional[str] = None,
        claimed_by: Optional[str] = None,
        taken_at: Optional[str] = None,
        operator_actions_json: Optional[str] = None,
        resolved_at: Optional[str] = None,
        resolution: Optional[str] = None,
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                UPDATE intervention SET
                    control_state = COALESCE(?, control_state),
                    claimed_by = COALESCE(?, claimed_by),
                    taken_at = COALESCE(?, taken_at),
                    operator_actions_json = COALESCE(?, operator_actions_json),
                    resolved_at = COALESCE(?, resolved_at),
                    resolution = COALESCE(?, resolution)
                WHERE intervention_id = ?
                """,
                (control_state, claimed_by, taken_at, operator_actions_json, resolved_at, resolution, intervention_id),
            )

    def get_intervention_row(self, intervention_id: str) -> Optional[sqlite3.Row]:
        cur = self._conn.execute("SELECT * FROM intervention WHERE intervention_id = ?", (intervention_id,))
        return cur.fetchone()

    def list_interventions(self, execution_id: Optional[str] = None) -> list[sqlite3.Row]:
        if execution_id:
            cur = self._conn.execute(
                "SELECT * FROM intervention WHERE execution_id = ? ORDER BY requested_at DESC", (execution_id,)
            )
        else:
            cur = self._conn.execute("SELECT * FROM intervention ORDER BY requested_at DESC")
        return cur.fetchall()
