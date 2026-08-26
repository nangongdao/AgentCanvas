-- Redacted SQLite dump matching the real pre-Alembic AgentCanvas schema.
-- All business values and credentials are deterministic test placeholders.
PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;

CREATE TABLE workflows (
    id VARCHAR(32) NOT NULL,
    name VARCHAR(200) NOT NULL,
    description TEXT NOT NULL,
    dsl_json JSON NOT NULL,
    version INTEGER NOT NULL,
    is_archived BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE model_configs (
    id VARCHAR(64) NOT NULL,
    name VARCHAR(120) NOT NULL,
    provider VARCHAR(64) NOT NULL,
    model_name VARCHAR(120) NOT NULL,
    base_url VARCHAR(500),
    api_key_encrypted TEXT,
    params_json JSON NOT NULL,
    kind VARCHAR(32) NOT NULL,
    is_default BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE mcp_servers (
    id VARCHAR(64) NOT NULL,
    name VARCHAR(120) NOT NULL,
    transport VARCHAR(32) NOT NULL,
    command VARCHAR(500),
    args_json JSON NOT NULL,
    env_json JSON NOT NULL,
    url VARCHAR(500),
    headers_json JSON NOT NULL,
    enabled BOOLEAN NOT NULL,
    tools_cache_json JSON NOT NULL,
    tools_cached_at DATETIME,
    last_status VARCHAR(300),
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE executions (
    id VARCHAR(32) NOT NULL,
    workflow_id VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    input_json JSON NOT NULL,
    output_json JSON,
    error TEXT,
    thread_id VARCHAR(32) NOT NULL,
    session_id VARCHAR(32),
    started_at DATETIME NOT NULL,
    finished_at DATETIME,
    PRIMARY KEY (id),
    FOREIGN KEY(workflow_id) REFERENCES workflows (id)
);

CREATE TABLE execution_events (
    id INTEGER NOT NULL,
    execution_id VARCHAR(32) NOT NULL,
    seq INTEGER NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    node_id VARCHAR(64),
    payload_json JSON NOT NULL,
    ts DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_exec_seq UNIQUE (execution_id, seq),
    FOREIGN KEY(execution_id) REFERENCES executions (id)
);

CREATE INDEX ix_executions_status ON executions (status);
CREATE INDEX ix_executions_workflow_id ON executions (workflow_id);
CREATE INDEX ix_execution_events_execution_id ON execution_events (execution_id);

INSERT INTO workflows VALUES (
    'redacted-workflow', 'Redacted workflow', 'Sanitized production-shape record',
    '{"version":"1.0","name":"Redacted workflow","nodes":[],"edges":[]}',
    3, 0, '2026-01-01 00:00:00', '2026-01-02 00:00:00'
);
INSERT INTO model_configs VALUES (
    'redacted-model', 'Redacted model', 'openai_compatible', 'redacted-model-name',
    'https://example.invalid/v1', NULL, '{"temperature":0.2}', 'chat', 1,
    '2026-01-01 00:00:00'
);
INSERT INTO mcp_servers VALUES (
    'redacted-mcp', 'Redacted MCP', 'sse', NULL, '[]', '{}',
    'https://example.invalid/sse', '{}', 0, '[]', NULL, 'disconnected',
    '2026-01-01 00:00:00'
);
INSERT INTO executions VALUES (
    'redacted-execution', 'redacted-workflow', 'succeeded', '{"prompt":"redacted"}',
    '{"answer":"redacted"}', NULL, 'redacted-thread', 'redacted-session',
    '2026-01-02 00:00:00', '2026-01-02 00:00:01'
);
INSERT INTO execution_events VALUES (
    1, 'redacted-execution', 1, 'workflow_finished', NULL,
    '{"output":{"answer":"redacted"}}', '2026-01-02 00:00:01'
);

COMMIT;
