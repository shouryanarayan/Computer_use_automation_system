-- Registry / execution-history schema.
--
-- Engine: SQLite (single file, zero external services - see REPORT.md
-- "Architecture" for why). The shape mirrors a normal Postgres design
-- deliberately: CAPABILITY_VERSION is a versioned/append-mostly table
-- (a new row per version, is_current marks the active one) and
-- EXECUTION_EVENT is a pure append-only audit log. Swapping the
-- engine for Postgres in production is a driver change, not a schema
-- change.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS capability (
    capability_id   TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    vendor_family   TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS capability_version (
    capability_version_id  TEXT PRIMARY KEY,
    capability_id           TEXT NOT NULL REFERENCES capability(capability_id),
    version                 TEXT NOT NULL,
    artifact_json            TEXT NOT NULL,
    status                   TEXT NOT NULL DEFAULT 'DRAFT',
    is_current               INTEGER NOT NULL DEFAULT 0,
    created_at               TEXT NOT NULL,
    approved_by              TEXT,
    approved_at              TEXT,
    UNIQUE (capability_id, version)
);

CREATE TABLE IF NOT EXISTS execution (
    execution_id            TEXT PRIMARY KEY,
    mode                     TEXT NOT NULL,           -- DISCOVERY | REPLAY
    capability_id            TEXT REFERENCES capability(capability_id),
    capability_version_id    TEXT REFERENCES capability_version(capability_version_id),
    tenant_id                TEXT NOT NULL DEFAULT 'default',
    status                   TEXT NOT NULL DEFAULT 'RUNNING',
    goal                     TEXT,
    input_params_json        TEXT NOT NULL DEFAULT '{}',  -- sensitive values redacted before write
    started_at               TEXT NOT NULL,
    completed_at             TEXT,
    last_checkpoint          TEXT
);

CREATE TABLE IF NOT EXISTS execution_event (
    event_id        TEXT PRIMARY KEY,
    execution_id    TEXT NOT NULL REFERENCES execution(execution_id),
    step_id         TEXT,
    actor           TEXT NOT NULL,   -- AUTOMATION | LLM | POLICY | HUMAN | SYSTEM
    event_type      TEXT NOT NULL,   -- OBSERVE | DECIDE | ACT | CHECKPOINT | POLICY_DECISION | ERROR | INTERVENTION
    summary         TEXT NOT NULL,
    detail_json     TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'OK',
    timestamp       TEXT NOT NULL,
    evidence_uri    TEXT
);

CREATE INDEX IF NOT EXISTS idx_execution_event_execution_id
    ON execution_event(execution_id);

CREATE TABLE IF NOT EXISTS intervention (
    intervention_id     TEXT PRIMARY KEY,
    execution_id        TEXT NOT NULL REFERENCES execution(execution_id),
    capability_id       TEXT REFERENCES capability(capability_id),
    reason               TEXT NOT NULL,
    context_json          TEXT NOT NULL,
    control_state         TEXT NOT NULL,
    requested_at           TEXT NOT NULL,
    claimed_by             TEXT,
    taken_at               TEXT,
    operator_actions_json  TEXT NOT NULL DEFAULT '[]',
    resolved_at             TEXT,
    resolution              TEXT
);

CREATE INDEX IF NOT EXISTS idx_intervention_execution_id
    ON intervention(execution_id);
