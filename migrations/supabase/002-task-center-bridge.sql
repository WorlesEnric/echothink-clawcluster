CREATE TABLE IF NOT EXISTS clawcluster.task_center_refs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dispatch_id text NOT NULL UNIQUE,
    task_id text NOT NULL,
    task_node_id text,
    workspace_id text NOT NULL,
    work_item_id text NOT NULL UNIQUE REFERENCES clawcluster.work_items(id) ON DELETE CASCADE,
    state text NOT NULL DEFAULT 'accepted',
    dispatch_payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    bridge_response_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    sync_state_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT task_center_refs_dispatch_payload_json_type_check CHECK (jsonb_typeof(dispatch_payload_json) = 'object'),
    CONSTRAINT task_center_refs_bridge_response_json_type_check CHECK (jsonb_typeof(bridge_response_json) = 'object'),
    CONSTRAINT task_center_refs_sync_state_json_type_check CHECK (jsonb_typeof(sync_state_json) = 'object')
);

COMMENT ON TABLE clawcluster.task_center_refs IS 'Correlation rows linking Task Center dispatch ids to ClawCluster work items.';
COMMENT ON COLUMN clawcluster.task_center_refs.dispatch_id IS 'Canonical Task Center dispatch identifier used for idempotency.';
COMMENT ON COLUMN clawcluster.task_center_refs.task_id IS 'Canonical Task Center task identifier associated with the dispatch.';
COMMENT ON COLUMN clawcluster.task_center_refs.task_node_id IS 'Optional Task Center DAG node identifier when the dispatch targets a subnode.';
COMMENT ON COLUMN clawcluster.task_center_refs.workspace_id IS 'Task Center workspace identifier associated with the dispatch.';
COMMENT ON COLUMN clawcluster.task_center_refs.work_item_id IS 'Linked ClawCluster work item created for this dispatch.';
COMMENT ON COLUMN clawcluster.task_center_refs.state IS 'Bridge-local lifecycle state for the correlated dispatch.';
COMMENT ON COLUMN clawcluster.task_center_refs.dispatch_payload_json IS 'Original canonical Task Center dispatch payload.';
COMMENT ON COLUMN clawcluster.task_center_refs.bridge_response_json IS 'Most recent bridge intake response payload.';
COMMENT ON COLUMN clawcluster.task_center_refs.sync_state_json IS 'Bridge-managed delivery state for callback reconciliation and deduplication.';

CREATE TABLE IF NOT EXISTS clawcluster.task_center_outbox (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dispatch_id text NOT NULL REFERENCES clawcluster.task_center_refs(dispatch_id) ON DELETE CASCADE,
    event_type text NOT NULL,
    payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    dedupe_key text NOT NULL UNIQUE,
    delivery_status text NOT NULL DEFAULT 'pending',
    retry_count integer NOT NULL DEFAULT 0,
    last_attempt_at timestamptz,
    delivered_at timestamptz,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT task_center_outbox_payload_json_type_check CHECK (jsonb_typeof(payload_json) = 'object'),
    CONSTRAINT task_center_outbox_delivery_status_check CHECK (delivery_status IN ('pending', 'delivered', 'failed', 'dead_letter'))
);

COMMENT ON TABLE clawcluster.task_center_outbox IS 'Durable callback outbox for Task Center bridge events.';
COMMENT ON COLUMN clawcluster.task_center_outbox.dispatch_id IS 'Correlated Task Center dispatch identifier for the callback.';
COMMENT ON COLUMN clawcluster.task_center_outbox.event_type IS 'Task Center callback event type.';
COMMENT ON COLUMN clawcluster.task_center_outbox.payload_json IS 'Serialized callback payload.';
COMMENT ON COLUMN clawcluster.task_center_outbox.dedupe_key IS 'Unique callback dedupe key used for idempotent enqueueing.';

CREATE INDEX IF NOT EXISTS task_center_refs_work_item_id_idx
    ON clawcluster.task_center_refs (work_item_id);

CREATE INDEX IF NOT EXISTS task_center_outbox_delivery_status_idx
    ON clawcluster.task_center_outbox (delivery_status, created_at);
