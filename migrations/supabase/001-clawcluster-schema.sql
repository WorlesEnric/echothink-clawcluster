\set ON_ERROR_STOP on
\c supabase

BEGIN;

-- ============================================================================
-- ClawCluster initial schema migration for EchoThink.
--
-- This migration creates the dedicated `clawcluster` schema used by the
-- HiClaw manager/worker topology, including operational tables, integrity
-- constraints, indexes, row-level security enablement, timestamp triggers,
-- and seed data for default agent profiles, skills, and budget policy.
--
-- Assumptions:
-- - The target database is the shared PostgreSQL 16 `supabase` database.
-- - Standard Supabase extensions are already installed, including pgcrypto for
--   `gen_random_uuid()`.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS clawcluster;

COMMENT ON SCHEMA clawcluster IS 'ClawCluster operational schema for EchoThink agent workforce orchestration.';

SET search_path = clawcluster, public, extensions;

CREATE TABLE clawcluster.agent_profiles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    role text NOT NULL,
    default_worker_type text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    approval_class text NOT NULL DEFAULT 'medium',
    higress_consumer_id text,
    storage_prefix text,
    metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_profiles_name_key UNIQUE (name),
    CONSTRAINT agent_profiles_role_check CHECK (
        role IN ('manager', 'planner-worker', 'workflow-worker', 'coding-worker', 'qa-worker', 'knowledge-worker')
    ),
    CONSTRAINT agent_profiles_approval_class_check CHECK (
        approval_class IN ('low', 'medium', 'high', 'critical')
    ),
    CONSTRAINT agent_profiles_metadata_json_type_check CHECK (jsonb_typeof(metadata_json) = 'object')
);

COMMENT ON TABLE clawcluster.agent_profiles IS 'Catalog of manager and worker agent profiles available to ClawCluster.';
COMMENT ON COLUMN clawcluster.agent_profiles.id IS 'Primary key for the agent profile.';
COMMENT ON COLUMN clawcluster.agent_profiles.name IS 'Stable unique profile name used by orchestration and routing.';
COMMENT ON COLUMN clawcluster.agent_profiles.role IS 'Logical role of the agent profile within the HiClaw topology.';
COMMENT ON COLUMN clawcluster.agent_profiles.default_worker_type IS 'Default worker type or runtime label assigned to this profile.';
COMMENT ON COLUMN clawcluster.agent_profiles.enabled IS 'Whether the agent profile can receive new assignments.';
COMMENT ON COLUMN clawcluster.agent_profiles.approval_class IS 'Default approval sensitivity used when the profile executes work.';
COMMENT ON COLUMN clawcluster.agent_profiles.higress_consumer_id IS 'Optional Higress consumer identifier used for gateway credentials.';
COMMENT ON COLUMN clawcluster.agent_profiles.storage_prefix IS 'Optional object storage prefix reserved for profile outputs.';
COMMENT ON COLUMN clawcluster.agent_profiles.metadata_json IS 'Arbitrary profile metadata for runtime capabilities and annotations.';
COMMENT ON COLUMN clawcluster.agent_profiles.created_at IS 'Timestamp when the profile row was created.';
COMMENT ON COLUMN clawcluster.agent_profiles.updated_at IS 'Timestamp when the profile row was last updated.';

CREATE TABLE clawcluster.skill_definitions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    version text NOT NULL DEFAULT '0.1.0',
    category text NOT NULL,
    runtime_class text NOT NULL,
    description text,
    input_schema jsonb NOT NULL DEFAULT '[]'::jsonb,
    output_schema jsonb NOT NULL DEFAULT '[]'::jsonb,
    required_tools jsonb NOT NULL DEFAULT '[]'::jsonb,
    required_mcp_services jsonb NOT NULL DEFAULT '[]'::jsonb,
    guardrails_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT skill_definitions_name_version_key UNIQUE (name, version),
    CONSTRAINT skill_definitions_category_check CHECK (category IN ('doc', 'plan', 'graph', 'workflow', 'code', 'qa')),
    CONSTRAINT skill_definitions_runtime_class_check CHECK (
        runtime_class IN ('worker-container', 'manager-cli', 'manager-container')
    ),
    CONSTRAINT skill_definitions_input_schema_type_check CHECK (jsonb_typeof(input_schema) = 'array'),
    CONSTRAINT skill_definitions_output_schema_type_check CHECK (jsonb_typeof(output_schema) = 'array'),
    CONSTRAINT skill_definitions_required_tools_type_check CHECK (jsonb_typeof(required_tools) = 'array'),
    CONSTRAINT skill_definitions_required_mcp_services_type_check CHECK (jsonb_typeof(required_mcp_services) = 'array'),
    CONSTRAINT skill_definitions_guardrails_json_type_check CHECK (jsonb_typeof(guardrails_json) = 'object')
);

COMMENT ON TABLE clawcluster.skill_definitions IS 'Versioned skill registry describing executable capabilities for agents and managers.';
COMMENT ON COLUMN clawcluster.skill_definitions.id IS 'Primary key for the skill definition.';
COMMENT ON COLUMN clawcluster.skill_definitions.name IS 'Stable skill name used by planners and runtimes.';
COMMENT ON COLUMN clawcluster.skill_definitions.version IS 'Semantic skill version identifier.';
COMMENT ON COLUMN clawcluster.skill_definitions.category IS 'Functional category for the skill.';
COMMENT ON COLUMN clawcluster.skill_definitions.runtime_class IS 'Execution environment required by the skill.';
COMMENT ON COLUMN clawcluster.skill_definitions.description IS 'Human-readable summary of the skill behavior.';
COMMENT ON COLUMN clawcluster.skill_definitions.input_schema IS 'JSON schema fragments describing expected skill inputs.';
COMMENT ON COLUMN clawcluster.skill_definitions.output_schema IS 'JSON schema fragments describing expected skill outputs.';
COMMENT ON COLUMN clawcluster.skill_definitions.required_tools IS 'List of tool identifiers that must be available during execution.';
COMMENT ON COLUMN clawcluster.skill_definitions.required_mcp_services IS 'List of MCP services required by the skill.';
COMMENT ON COLUMN clawcluster.skill_definitions.guardrails_json IS 'Guardrail configuration applied when invoking the skill.';
COMMENT ON COLUMN clawcluster.skill_definitions.enabled IS 'Whether the skill is available for scheduling.';
COMMENT ON COLUMN clawcluster.skill_definitions.created_at IS 'Timestamp when the skill definition was created.';
COMMENT ON COLUMN clawcluster.skill_definitions.updated_at IS 'Timestamp when the skill definition was last updated.';

CREATE TABLE clawcluster.agent_skill_bindings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_profile_id uuid NOT NULL REFERENCES clawcluster.agent_profiles(id) ON DELETE CASCADE,
    skill_definition_id uuid NOT NULL REFERENCES clawcluster.skill_definitions(id) ON DELETE CASCADE,
    priority integer NOT NULL DEFAULT 100,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT agent_skill_bindings_agent_profile_id_skill_definition_id_key UNIQUE (agent_profile_id, skill_definition_id)
);

COMMENT ON TABLE clawcluster.agent_skill_bindings IS 'Many-to-many bindings between agent profiles and the skills they can execute.';
COMMENT ON COLUMN clawcluster.agent_skill_bindings.id IS 'Primary key for the agent-to-skill binding.';
COMMENT ON COLUMN clawcluster.agent_skill_bindings.agent_profile_id IS 'Agent profile attached to the skill.';
COMMENT ON COLUMN clawcluster.agent_skill_bindings.skill_definition_id IS 'Skill definition attached to the agent profile.';
COMMENT ON COLUMN clawcluster.agent_skill_bindings.priority IS 'Relative execution preference for the bound skill, where lower numbers are higher priority.';
COMMENT ON COLUMN clawcluster.agent_skill_bindings.enabled IS 'Whether the binding is active for scheduling.';
COMMENT ON COLUMN clawcluster.agent_skill_bindings.created_at IS 'Timestamp when the binding was created.';

CREATE TABLE clawcluster.work_items (
    id text PRIMARY KEY DEFAULT ('wi_' || replace(gen_random_uuid()::text, '-', '')),
    workspace_id text NOT NULL,
    kind text NOT NULL,
    source_type text NOT NULL,
    source_ref text,
    objective text NOT NULL,
    acceptance_criteria jsonb NOT NULL DEFAULT '[]'::jsonb,
    constraints_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'pending',
    priority integer NOT NULL DEFAULT 50,
    risk_level text NOT NULL DEFAULT 'medium',
    approval_policy text NOT NULL DEFAULT 'medium',
    requested_by text NOT NULL,
    assigned_agent_profile_id uuid REFERENCES clawcluster.agent_profiles(id),
    matrix_room_id text,
    hiclaw_worker_id uuid,
    blocked_reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT work_items_kind_check CHECK (
        kind IN ('code.implement', 'code.review', 'workflow.author', 'plan.breakdown', 'plan.support', 'plan.status', 'knowledge.sync', 'qa.validate')
    ),
    CONSTRAINT work_items_source_type_check CHECK (
        source_type IN ('outline_document', 'gitlab_issue', 'gitlab_mr', 'manual', 'hatchet_trigger')
    ),
    CONSTRAINT work_items_acceptance_criteria_type_check CHECK (jsonb_typeof(acceptance_criteria) = 'array'),
    CONSTRAINT work_items_constraints_json_type_check CHECK (jsonb_typeof(constraints_json) = 'object'),
    CONSTRAINT work_items_status_check CHECK (
        status IN ('pending', 'assigned', 'in_progress', 'blocked', 'awaiting_approval', 'approved', 'publishing', 'complete', 'failed', 'cancelled')
    ),
    CONSTRAINT work_items_priority_check CHECK (priority BETWEEN 1 AND 100),
    CONSTRAINT work_items_risk_level_check CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    CONSTRAINT work_items_approval_policy_check CHECK (approval_policy IN ('none', 'low', 'medium', 'high', 'critical'))
);

COMMENT ON TABLE clawcluster.work_items IS 'Top-level units of work queued and executed through ClawCluster.';
COMMENT ON COLUMN clawcluster.work_items.id IS 'Primary identifier for the work item with a wi_ prefix.';
COMMENT ON COLUMN clawcluster.work_items.workspace_id IS 'Workspace or tenant identifier that owns the work item.';
COMMENT ON COLUMN clawcluster.work_items.kind IS 'Canonical work item kind used for routing and policy decisions.';
COMMENT ON COLUMN clawcluster.work_items.source_type IS 'Origin of the work request.';
COMMENT ON COLUMN clawcluster.work_items.source_ref IS 'Opaque reference to the upstream source object.';
COMMENT ON COLUMN clawcluster.work_items.objective IS 'Natural-language objective the assigned agent must satisfy.';
COMMENT ON COLUMN clawcluster.work_items.acceptance_criteria IS 'Ordered list of acceptance criteria for successful completion.';
COMMENT ON COLUMN clawcluster.work_items.constraints_json IS 'Execution constraints, context, and policy hints for the work item.';
COMMENT ON COLUMN clawcluster.work_items.status IS 'Current lifecycle state of the work item.';
COMMENT ON COLUMN clawcluster.work_items.priority IS 'Relative scheduling priority from 1 to 100, where higher values indicate higher urgency.';
COMMENT ON COLUMN clawcluster.work_items.risk_level IS 'Risk classification for the work item outcome.';
COMMENT ON COLUMN clawcluster.work_items.approval_policy IS 'Approval gate severity required before publishing or completion.';
COMMENT ON COLUMN clawcluster.work_items.requested_by IS 'Identifier of the user, system, or service that requested the work item.';
COMMENT ON COLUMN clawcluster.work_items.assigned_agent_profile_id IS 'Agent profile currently assigned to execute the work item.';
COMMENT ON COLUMN clawcluster.work_items.matrix_room_id IS 'Matrix room associated with collaboration for this work item.';
COMMENT ON COLUMN clawcluster.work_items.hiclaw_worker_id IS 'HiClaw worker currently handling the work item, when assigned.';
COMMENT ON COLUMN clawcluster.work_items.blocked_reason IS 'Reason the work item cannot progress when in a blocked state.';
COMMENT ON COLUMN clawcluster.work_items.created_at IS 'Timestamp when the work item was created.';
COMMENT ON COLUMN clawcluster.work_items.updated_at IS 'Timestamp when the work item was last updated.';

CREATE TABLE clawcluster.task_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    work_item_id text NOT NULL REFERENCES clawcluster.work_items(id) ON DELETE CASCADE,
    agent_profile_id uuid REFERENCES clawcluster.agent_profiles(id),
    skill_definition_id uuid REFERENCES clawcluster.skill_definitions(id),
    status text NOT NULL DEFAULT 'pending',
    started_at timestamptz,
    ended_at timestamptz,
    langfuse_trace_id text,
    cost_usd numeric(10,6),
    token_count integer,
    error_message text,
    result_summary text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT task_runs_status_check CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')),
    CONSTRAINT task_runs_cost_usd_nonnegative_check CHECK (cost_usd IS NULL OR cost_usd >= 0),
    CONSTRAINT task_runs_token_count_nonnegative_check CHECK (token_count IS NULL OR token_count >= 0),
    CONSTRAINT task_runs_temporal_order_check CHECK (ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at)
);

COMMENT ON TABLE clawcluster.task_runs IS 'Execution attempts for work items, including cost and traceability metadata.';
COMMENT ON COLUMN clawcluster.task_runs.id IS 'Primary key for the task run.';
COMMENT ON COLUMN clawcluster.task_runs.work_item_id IS 'Owning work item for this execution attempt.';
COMMENT ON COLUMN clawcluster.task_runs.agent_profile_id IS 'Agent profile used to execute the task run.';
COMMENT ON COLUMN clawcluster.task_runs.skill_definition_id IS 'Skill definition invoked during the task run.';
COMMENT ON COLUMN clawcluster.task_runs.status IS 'Execution status of the task run.';
COMMENT ON COLUMN clawcluster.task_runs.started_at IS 'Timestamp when execution started.';
COMMENT ON COLUMN clawcluster.task_runs.ended_at IS 'Timestamp when execution ended.';
COMMENT ON COLUMN clawcluster.task_runs.langfuse_trace_id IS 'External Langfuse trace identifier associated with the run.';
COMMENT ON COLUMN clawcluster.task_runs.cost_usd IS 'Observed execution cost in USD.';
COMMENT ON COLUMN clawcluster.task_runs.token_count IS 'Total token usage for the run, if tracked.';
COMMENT ON COLUMN clawcluster.task_runs.error_message IS 'Error summary recorded when execution fails.';
COMMENT ON COLUMN clawcluster.task_runs.result_summary IS 'Human-readable summary of the run output.';
COMMENT ON COLUMN clawcluster.task_runs.created_at IS 'Timestamp when the task run row was created.';
COMMENT ON COLUMN clawcluster.task_runs.updated_at IS 'Timestamp when the task run row was last updated.';

CREATE TABLE clawcluster.approvals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    work_item_id text NOT NULL REFERENCES clawcluster.work_items(id) ON DELETE CASCADE,
    task_run_id uuid REFERENCES clawcluster.task_runs(id),
    gate_name text NOT NULL,
    requested_from text NOT NULL,
    decision text,
    decided_at timestamptz,
    decided_by text,
    evidence_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT approvals_decision_check CHECK (decision IN ('pending', 'approved', 'rejected', 'auto_approved')),
    CONSTRAINT approvals_evidence_json_type_check CHECK (jsonb_typeof(evidence_json) = 'object'),
    CONSTRAINT approvals_decision_timestamp_check CHECK (decided_at IS NULL OR decision IS NOT NULL)
);

COMMENT ON TABLE clawcluster.approvals IS 'Approval gate requests and decisions associated with work items and task runs.';
COMMENT ON COLUMN clawcluster.approvals.id IS 'Primary key for the approval record.';
COMMENT ON COLUMN clawcluster.approvals.work_item_id IS 'Work item that requires approval.';
COMMENT ON COLUMN clawcluster.approvals.task_run_id IS 'Task run that generated the approval request, when applicable.';
COMMENT ON COLUMN clawcluster.approvals.gate_name IS 'Logical approval gate identifier.';
COMMENT ON COLUMN clawcluster.approvals.requested_from IS 'Reviewer, group, or system from which approval was requested.';
COMMENT ON COLUMN clawcluster.approvals.decision IS 'Recorded approval decision.';
COMMENT ON COLUMN clawcluster.approvals.decided_at IS 'Timestamp when the decision was made.';
COMMENT ON COLUMN clawcluster.approvals.decided_by IS 'Actor that issued the decision.';
COMMENT ON COLUMN clawcluster.approvals.evidence_json IS 'Structured evidence captured for the approval decision.';
COMMENT ON COLUMN clawcluster.approvals.notes IS 'Free-form notes recorded with the approval request or decision.';
COMMENT ON COLUMN clawcluster.approvals.created_at IS 'Timestamp when the approval row was created.';
COMMENT ON COLUMN clawcluster.approvals.updated_at IS 'Timestamp when the approval row was last updated.';

CREATE TABLE clawcluster.artifacts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_run_id uuid NOT NULL REFERENCES clawcluster.task_runs(id) ON DELETE CASCADE,
    kind text NOT NULL,
    uri text NOT NULL,
    checksum text,
    size_bytes bigint,
    metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT artifacts_kind_check CHECK (
        kind IN ('patch', 'branch_ref', 'mr_ref', 'workflow_draft', 'outline_draft', 'test_report', 'plan_breakdown', 'knowledge_episode', 'log')
    ),
    CONSTRAINT artifacts_size_bytes_nonnegative_check CHECK (size_bytes IS NULL OR size_bytes >= 0),
    CONSTRAINT artifacts_metadata_json_type_check CHECK (jsonb_typeof(metadata_json) = 'object')
);

COMMENT ON TABLE clawcluster.artifacts IS 'Durable references to outputs produced by task runs.';
COMMENT ON COLUMN clawcluster.artifacts.id IS 'Primary key for the artifact.';
COMMENT ON COLUMN clawcluster.artifacts.task_run_id IS 'Task run that produced the artifact.';
COMMENT ON COLUMN clawcluster.artifacts.kind IS 'Artifact type used for downstream handling and publishing.';
COMMENT ON COLUMN clawcluster.artifacts.uri IS 'Canonical URI pointing to the stored artifact.';
COMMENT ON COLUMN clawcluster.artifacts.checksum IS 'Optional checksum for integrity verification.';
COMMENT ON COLUMN clawcluster.artifacts.size_bytes IS 'Optional artifact size in bytes.';
COMMENT ON COLUMN clawcluster.artifacts.metadata_json IS 'Structured metadata about the artifact payload.';
COMMENT ON COLUMN clawcluster.artifacts.created_at IS 'Timestamp when the artifact row was created.';

CREATE TABLE clawcluster.hiclaw_workers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    worker_name text NOT NULL,
    agent_profile_id uuid NOT NULL REFERENCES clawcluster.agent_profiles(id),
    matrix_user_id text,
    matrix_room_id text,
    higress_consumer_id text NOT NULL,
    storage_prefix text NOT NULL,
    runtime text NOT NULL DEFAULT 'openclaw',
    status text NOT NULL DEFAULT 'idle',
    last_heartbeat_at timestamptz,
    current_work_item_id text REFERENCES clawcluster.work_items(id),
    container_id text,
    host_node text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT hiclaw_workers_worker_name_key UNIQUE (worker_name),
    CONSTRAINT hiclaw_workers_runtime_check CHECK (runtime IN ('openclaw', 'custom')),
    CONSTRAINT hiclaw_workers_status_check CHECK (status IN ('idle', 'active', 'stopped', 'error'))
);

COMMENT ON TABLE clawcluster.hiclaw_workers IS 'Registered HiClaw worker instances available to execute ClawCluster work items.';
COMMENT ON COLUMN clawcluster.hiclaw_workers.id IS 'Primary key for the worker instance.';
COMMENT ON COLUMN clawcluster.hiclaw_workers.worker_name IS 'Stable unique runtime name for the worker.';
COMMENT ON COLUMN clawcluster.hiclaw_workers.agent_profile_id IS 'Agent profile implemented by the worker instance.';
COMMENT ON COLUMN clawcluster.hiclaw_workers.matrix_user_id IS 'Matrix user identifier used by the worker.';
COMMENT ON COLUMN clawcluster.hiclaw_workers.matrix_room_id IS 'Default Matrix room associated with the worker.';
COMMENT ON COLUMN clawcluster.hiclaw_workers.higress_consumer_id IS 'Higress consumer identifier provisioned for the worker.';
COMMENT ON COLUMN clawcluster.hiclaw_workers.storage_prefix IS 'Object storage prefix assigned to the worker.';
COMMENT ON COLUMN clawcluster.hiclaw_workers.runtime IS 'Worker runtime implementation class.';
COMMENT ON COLUMN clawcluster.hiclaw_workers.status IS 'Current operational status of the worker.';
COMMENT ON COLUMN clawcluster.hiclaw_workers.last_heartbeat_at IS 'Timestamp of the last observed heartbeat from the worker.';
COMMENT ON COLUMN clawcluster.hiclaw_workers.current_work_item_id IS 'Work item currently assigned to the worker, if any.';
COMMENT ON COLUMN clawcluster.hiclaw_workers.container_id IS 'Backing container identifier for the worker runtime.';
COMMENT ON COLUMN clawcluster.hiclaw_workers.host_node IS 'Host node or machine running the worker.';
COMMENT ON COLUMN clawcluster.hiclaw_workers.created_at IS 'Timestamp when the worker row was created.';
COMMENT ON COLUMN clawcluster.hiclaw_workers.updated_at IS 'Timestamp when the worker row was last updated.';

ALTER TABLE clawcluster.work_items
    ADD CONSTRAINT work_items_hiclaw_worker_id_fkey
    FOREIGN KEY (hiclaw_worker_id)
    REFERENCES clawcluster.hiclaw_workers(id);

CREATE TABLE clawcluster.external_refs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    work_item_id text NOT NULL REFERENCES clawcluster.work_items(id) ON DELETE CASCADE,
    outline_doc_id text,
    gitlab_project_id text,
    gitlab_issue_iid integer,
    gitlab_mr_iid integer,
    dify_workflow_id text,
    n8n_workflow_id text,
    matrix_room_id text,
    hatchet_workflow_run_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT external_refs_work_item_id_key UNIQUE (work_item_id)
);

COMMENT ON TABLE clawcluster.external_refs IS 'External system references linked one-to-one with a work item.';
COMMENT ON COLUMN clawcluster.external_refs.id IS 'Primary key for the external reference record.';
COMMENT ON COLUMN clawcluster.external_refs.work_item_id IS 'Owning work item for the external references.';
COMMENT ON COLUMN clawcluster.external_refs.outline_doc_id IS 'Outline document identifier associated with the work item.';
COMMENT ON COLUMN clawcluster.external_refs.gitlab_project_id IS 'GitLab project identifier used for issue or MR linkage.';
COMMENT ON COLUMN clawcluster.external_refs.gitlab_issue_iid IS 'GitLab issue IID associated with the work item.';
COMMENT ON COLUMN clawcluster.external_refs.gitlab_mr_iid IS 'GitLab merge request IID associated with the work item.';
COMMENT ON COLUMN clawcluster.external_refs.dify_workflow_id IS 'Dify workflow identifier associated with the work item.';
COMMENT ON COLUMN clawcluster.external_refs.n8n_workflow_id IS 'n8n workflow identifier associated with the work item.';
COMMENT ON COLUMN clawcluster.external_refs.matrix_room_id IS 'Matrix room identifier associated with the work item.';
COMMENT ON COLUMN clawcluster.external_refs.hatchet_workflow_run_id IS 'Hatchet workflow run identifier associated with the work item.';
COMMENT ON COLUMN clawcluster.external_refs.created_at IS 'Timestamp when the external reference row was created.';
COMMENT ON COLUMN clawcluster.external_refs.updated_at IS 'Timestamp when the external reference row was last updated.';

CREATE TABLE clawcluster.budget_policies (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_type text NOT NULL,
    scope_id text NOT NULL DEFAULT 'global',
    daily_cost_limit_usd numeric(10,4) NOT NULL DEFAULT 50.0000,
    per_task_cost_limit_usd numeric(10,4) NOT NULL DEFAULT 10.0000,
    token_limit_per_task integer NOT NULL DEFAULT 500000,
    concurrency_limit integer NOT NULL DEFAULT 5,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT budget_policies_scope_type_scope_id_key UNIQUE (scope_type, scope_id),
    CONSTRAINT budget_policies_scope_type_check CHECK (
        scope_type IN ('global', 'workspace', 'agent_profile', 'work_item_kind')
    ),
    CONSTRAINT budget_policies_daily_cost_limit_nonnegative_check CHECK (daily_cost_limit_usd >= 0),
    CONSTRAINT budget_policies_per_task_cost_limit_nonnegative_check CHECK (per_task_cost_limit_usd >= 0),
    CONSTRAINT budget_policies_token_limit_per_task_positive_check CHECK (token_limit_per_task > 0),
    CONSTRAINT budget_policies_concurrency_limit_positive_check CHECK (concurrency_limit > 0)
);

COMMENT ON TABLE clawcluster.budget_policies IS 'Budget and concurrency controls applied by scope across ClawCluster.';
COMMENT ON COLUMN clawcluster.budget_policies.id IS 'Primary key for the budget policy.';
COMMENT ON COLUMN clawcluster.budget_policies.scope_type IS 'Dimension across which the budget policy is applied.';
COMMENT ON COLUMN clawcluster.budget_policies.scope_id IS 'Identifier within the scope type, or global for the default policy.';
COMMENT ON COLUMN clawcluster.budget_policies.daily_cost_limit_usd IS 'Maximum daily cost allowed for the policy scope in USD.';
COMMENT ON COLUMN clawcluster.budget_policies.per_task_cost_limit_usd IS 'Maximum cost allowed per task run in USD.';
COMMENT ON COLUMN clawcluster.budget_policies.token_limit_per_task IS 'Maximum token usage allowed per task run.';
COMMENT ON COLUMN clawcluster.budget_policies.concurrency_limit IS 'Maximum number of concurrent tasks allowed for the policy scope.';
COMMENT ON COLUMN clawcluster.budget_policies.enabled IS 'Whether the budget policy is active.';
COMMENT ON COLUMN clawcluster.budget_policies.created_at IS 'Timestamp when the budget policy row was created.';
COMMENT ON COLUMN clawcluster.budget_policies.updated_at IS 'Timestamp when the budget policy row was last updated.';

CREATE OR REPLACE FUNCTION clawcluster.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION clawcluster.set_updated_at() IS 'Trigger function that refreshes updated_at before each row update.';

CREATE TRIGGER set_agent_profiles_updated_at
BEFORE UPDATE ON clawcluster.agent_profiles
FOR EACH ROW
EXECUTE FUNCTION clawcluster.set_updated_at();

CREATE TRIGGER set_skill_definitions_updated_at
BEFORE UPDATE ON clawcluster.skill_definitions
FOR EACH ROW
EXECUTE FUNCTION clawcluster.set_updated_at();

CREATE TRIGGER set_work_items_updated_at
BEFORE UPDATE ON clawcluster.work_items
FOR EACH ROW
EXECUTE FUNCTION clawcluster.set_updated_at();

CREATE TRIGGER set_task_runs_updated_at
BEFORE UPDATE ON clawcluster.task_runs
FOR EACH ROW
EXECUTE FUNCTION clawcluster.set_updated_at();

CREATE TRIGGER set_approvals_updated_at
BEFORE UPDATE ON clawcluster.approvals
FOR EACH ROW
EXECUTE FUNCTION clawcluster.set_updated_at();

CREATE TRIGGER set_hiclaw_workers_updated_at
BEFORE UPDATE ON clawcluster.hiclaw_workers
FOR EACH ROW
EXECUTE FUNCTION clawcluster.set_updated_at();

CREATE TRIGGER set_external_refs_updated_at
BEFORE UPDATE ON clawcluster.external_refs
FOR EACH ROW
EXECUTE FUNCTION clawcluster.set_updated_at();

CREATE TRIGGER set_budget_policies_updated_at
BEFORE UPDATE ON clawcluster.budget_policies
FOR EACH ROW
EXECUTE FUNCTION clawcluster.set_updated_at();

CREATE INDEX work_items_workspace_id_idx ON clawcluster.work_items (workspace_id);
CREATE INDEX work_items_status_idx ON clawcluster.work_items (status);
CREATE INDEX work_items_kind_idx ON clawcluster.work_items (kind);
CREATE INDEX task_runs_work_item_id_idx ON clawcluster.task_runs (work_item_id);
CREATE INDEX task_runs_status_idx ON clawcluster.task_runs (status);
CREATE INDEX task_runs_langfuse_trace_id_idx ON clawcluster.task_runs (langfuse_trace_id);
CREATE INDEX approvals_work_item_id_idx ON clawcluster.approvals (work_item_id);
CREATE INDEX approvals_decision_idx ON clawcluster.approvals (decision);
CREATE INDEX artifacts_task_run_id_idx ON clawcluster.artifacts (task_run_id);
CREATE INDEX hiclaw_workers_status_idx ON clawcluster.hiclaw_workers (status);
CREATE INDEX hiclaw_workers_agent_profile_id_idx ON clawcluster.hiclaw_workers (agent_profile_id);

ALTER TABLE clawcluster.agent_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE clawcluster.skill_definitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE clawcluster.agent_skill_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE clawcluster.work_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE clawcluster.task_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE clawcluster.approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE clawcluster.artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE clawcluster.hiclaw_workers ENABLE ROW LEVEL SECURITY;
ALTER TABLE clawcluster.external_refs ENABLE ROW LEVEL SECURITY;
ALTER TABLE clawcluster.budget_policies ENABLE ROW LEVEL SECURITY;

INSERT INTO clawcluster.agent_profiles (
    name,
    role,
    default_worker_type,
    approval_class,
    storage_prefix,
    metadata_json
)
VALUES
    ('manager', 'manager', 'manager', 'high', 'agents/manager', '{"seeded": true}'::jsonb),
    ('planner-worker', 'planner-worker', 'planner-worker', 'medium', 'agents/planner-worker', '{"seeded": true}'::jsonb),
    ('workflow-worker', 'workflow-worker', 'workflow-worker', 'medium', 'agents/workflow-worker', '{"seeded": true}'::jsonb),
    ('coding-worker', 'coding-worker', 'coding-worker', 'medium', 'agents/coding-worker', '{"seeded": true}'::jsonb),
    ('qa-worker', 'qa-worker', 'qa-worker', 'high', 'agents/qa-worker', '{"seeded": true}'::jsonb),
    ('knowledge-worker', 'knowledge-worker', 'knowledge-worker', 'medium', 'agents/knowledge-worker', '{"seeded": true}'::jsonb);

INSERT INTO clawcluster.skill_definitions (
    name,
    version,
    category,
    runtime_class,
    description
)
VALUES
    ('doc.outline.read', '0.1.0', 'doc', 'manager-cli', 'Read and normalize source context from an Outline document.'),
    ('doc.outline.write_draft', '0.1.0', 'doc', 'worker-container', 'Draft structured updates back into Outline documents.'),
    ('plan.breakdown', '0.1.0', 'plan', 'worker-container', 'Break high-level objectives into actionable work items and execution steps.'),
    ('plan.status_summarize', '0.1.0', 'plan', 'manager-cli', 'Summarize current work status for managers and stakeholders.'),
    ('graph.search', '0.1.0', 'graph', 'worker-container', 'Search the knowledge graph for relevant context and prior work.'),
    ('graph.sync_episode', '0.1.0', 'graph', 'worker-container', 'Write completed task knowledge back as a graph episode.'),
    ('workflow.dify.build', '0.1.0', 'workflow', 'worker-container', 'Author or update Dify workflow drafts from requirements.'),
    ('workflow.n8n.build', '0.1.0', 'workflow', 'worker-container', 'Author or update n8n workflow drafts from requirements.'),
    ('workflow.publish_draft', '0.1.0', 'workflow', 'manager-container', 'Publish an approved workflow draft into its target system.'),
    ('code.repo.implement', '0.1.0', 'code', 'worker-container', 'Implement requested repository changes and produce patch artifacts.'),
    ('code.repo.review', '0.1.0', 'code', 'worker-container', 'Review repository changes for correctness, risk, and completeness.'),
    ('code.repo.fix_from_review', '0.1.0', 'code', 'worker-container', 'Apply revisions based on code review findings.'),
    ('qa.run_validation', '0.1.0', 'qa', 'worker-container', 'Execute validation checks and summarize test outcomes.');

INSERT INTO clawcluster.budget_policies (
    scope_type,
    scope_id,
    daily_cost_limit_usd,
    per_task_cost_limit_usd,
    token_limit_per_task,
    concurrency_limit,
    enabled
)
VALUES
    ('global', 'global', 50.0000, 10.0000, 500000, 5, true);

COMMIT;
