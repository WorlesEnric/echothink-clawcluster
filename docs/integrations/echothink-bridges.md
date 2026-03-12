# EchoThink Bridge Integrations

This document is the operator and integrator reference for the four EchoThink-facing bridge services in ClawCluster. These bridges sit between the HiClaw runtime and EchoThink’s systems of record. They normalize inbound work, enforce policy, publish approved outputs, and attach execution evidence back to durable state.

## Table of Contents

- [Overview](#overview)
- [Intake Bridge](#intake-bridge-port-8100)
- [Publisher Bridge](#publisher-bridge-port-8101)
- [Policy Bridge](#policy-bridge-port-8102)
- [Observability Bridge](#observability-bridge-port-8103)
- [Common Bridge Patterns](#common-bridge-patterns)

---

## Overview

ClawCluster uses four dedicated bridge services:

| Bridge | Default port | Primary responsibility | Primary systems touched |
|--------|--------------|------------------------|-------------------------|
| Intake Bridge | `8100` | Normalize inbound human or webhook events into canonical `work_items` | Outline, GitLab, Supabase/Postgres, MinIO, HiClaw Manager |
| Publisher Bridge | `8101` | Write approved outputs back into EchoThink systems | Outline, GitLab, Dify, n8n, Supabase/Postgres, MinIO |
| Policy Bridge | `8102` | Decide whether work may proceed automatically, requires approval, or must be rejected | Supabase/Postgres, Matrix |
| Observability Bridge | `8103` | Link execution runs to Langfuse traces and durable knowledge sync | Supabase/Postgres, Langfuse, Graphiti |

### Why the bridges exist as a separate layer

The bridges are intentionally separate from the HiClaw Manager and Workers.

They exist to provide a stable integration boundary for five reasons:

1. **System-of-record protection.** Outline, GitLab, Supabase, Langfuse, and Graphiti remain authoritative; the runtime does not write to them directly without a controlled bridge.
2. **Canonical data shaping.** Human-authored or webhook-authored inputs are normalized into typed `work_items` before the Manager acts.
3. **Governance.** Approval routing, budget enforcement, and publication policy live in services that can be audited and versioned independently of Worker prompts.
4. **Idempotent external writes.** Outbound publication is recorded through `external_refs` and artifact records so retries do not blindly duplicate work.
5. **Operational simplicity.** The Manager coordinates teams; the bridges own integration logic, upstream protocol quirks, and durable linkage to EchoThink state.

At a high level, the bridge layer looks like this:

```text
EchoThink events / worker requests
        |
        v
  +--------------------+
  |  Bridge Services   |
  |--------------------|
  | intake             |
  | publisher          |
  | policy             |
  | observability      |
  +--------------------+
        |
        v
Supabase + MinIO + Manager + external EchoThink APIs
```

---

## Intake Bridge (port 8100)

The Intake Bridge is the entry point for turning EchoThink-side events into canonical units of work that the HiClaw Manager can route to Workers.

### Purpose

The service converts Outline and GitLab events into normalized `WorkItemCreate` payloads, persists them in `clawcluster.work_items`, stages `spec.md` into the shared MinIO-backed filesystem, and notifies the HiClaw Manager that a work item is ready.

Supported inbound sources are:

- Outline document webhooks
- GitLab issue and merge-request webhooks
- Manual work-item creation through the API

### API endpoints

| Endpoint | Method | Auth model | Purpose |
|----------|--------|------------|---------|
| `/webhooks/outline` | `POST` | HMAC over raw request body | Convert an Outline webhook into a canonical work item |
| `/webhooks/gitlab` | `POST` | HMAC over raw request body | Convert a GitLab webhook into a canonical work item |
| `/work-items` | `POST` | No bearer auth in current code | Create a manual work item directly |
| `/health` | `GET` | None | Check service liveness plus database, Manager, and MinIO reachability |

The service returns:

- `201 Created` when the work item is persisted and both downstream fan-out operations succeed
- `202 Accepted` when persistence succeeds but MinIO staging or Manager notification fails
- `400` for signature failures
- `422` for invalid webhook or normalization failures
- `503` if work-item persistence fails

### HMAC-SHA256 webhook signature validation

The Intake Bridge validates the raw request body with `HMAC-SHA256` using `WORKER_JWT_SECRET` as the shared secret.

Validation logic is effectively:

```text
expected_signature = hex(HMAC_SHA256(WORKER_JWT_SECRET, raw_request_body))
```

The bridge compares the provided signature with the computed digest using constant-time comparison.

| Source | Integration contract to plan for | Headers accepted by the current implementation | Notes |
|--------|----------------------------------|-----------------------------------------------|-------|
| Outline | `X-Outline-Signature` | `X-Outline-Signature-256`, `X-Outline-Signature`, `X-Signature-256` | A `sha256=` prefix is accepted and stripped before comparison |
| GitLab | `X-Gitlab-Token` in some webhook setups | `X-Gitlab-Signature-256`, `X-Gitlab-Signature`, `X-Hub-Signature-256` | The checked-in code validates a digest header, not GitLab’s shared-token header |

> Implementation note: if your upstream sender only emits token-style headers such as `X-Gitlab-Token`, you need an adapter layer such as `n8n` or you need to update the Intake Bridge to support that mode. The current production code expects an HMAC digest.

### WorkItem kinds

The design-facing primary work-item kinds are:

| Kind | Typical source | Purpose |
|------|----------------|---------|
| `workflow.author` | Outline workflow brief, automation request | Build or revise Dify or n8n workflows |
| `code.implement` | Outline spec, GitLab issue | Implement code changes |
| `code.review` | GitLab merge request, review request | Perform code or merge-request review |
| `plan.breakdown` | Outline planning brief | Decompose work into executable tasks |
| `plan.support` | Coordination or follow-up requests | Assist with planning support and execution follow-up |
| `knowledge.sync` | Runbook, documentation, knowledge capture request | Update durable operational knowledge |

The checked-in schema and classifier also support two additional kinds that are useful operationally:

- `plan.status`
- `qa.validate`

Those two extra kinds appear in the `WorkItemKind` enum and the `clawcluster.work_items` schema, even though the high-level design focuses primarily on the six kinds above.

### Canonical work-item flow

The Intake Bridge follows this execution path:

```text
webhook or manual request
    -> FastAPI route
    -> source-specific handler
    -> WorkItemCreate normalization
    -> INSERT into clawcluster.work_items
    -> write spec.md to MinIO
    -> POST work item to HiClaw Manager
```

In more detail:

1. The route receives either an Outline webhook, a GitLab webhook, or a manual `WorkItemCreate` payload.
2. For webhook paths, the route validates the HMAC signature over the raw body.
3. The source-specific handler classifies the work-item kind, derives objective text, extracts acceptance criteria, computes priority and risk, and sets approval policy.
4. The bridge inserts the normalized object into `clawcluster.work_items` in Supabase-backed PostgreSQL.
5. The bridge renders a Markdown spec with the work-item objective, acceptance criteria, and context.
6. The bridge stages that spec into MinIO at:

   ```text
   s3://<MINIO_HICLAW_BUCKET>/hiclaw-storage/shared/tasks/task-<work_item_id>/spec.md
   ```

7. The bridge notifies the HiClaw Manager by `POST`ing the stored work item to:

   ```text
   /work-items/{work_item_id}
   ```

8. The API response includes the stored work item plus per-downstream processing status for storage and Manager delivery.

### Source normalization behavior

The source-specific handlers apply the following normalization logic:

| Source | `source_type` | Key classification rules |
|--------|---------------|--------------------------|
| Outline document webhook | `outline_document` | Title and body are parsed for workflow, planning, knowledge, review, QA, and code keywords |
| GitLab issue webhook | `gitlab_issue` | Labels, title, and description drive kind, priority, and risk classification |
| GitLab merge request webhook | `gitlab_mr` | Always normalizes to `code.review` |
| Manual API request | `manual` | Existing payload is accepted; `source_type` is forcibly reset to `manual` if needed |

### Required environment variables

Use the repository root `.env.example` as the naming contract. The Intake Bridge specifically requires the following values.

| Variable | Required | Purpose | Notes |
|----------|----------|---------|-------|
| `PORT` or `INTAKE_BRIDGE_PORT` | Yes | Listener port | Defaults to `8100` |
| `OUTLINE_URL` | Yes | Outline base URL | Required by service settings, even though webhook normalization does not currently call the Outline API |
| `OUTLINE_API_TOKEN` | Yes | Outline credential | Required by settings validation |
| `GITLAB_URL` | Yes | GitLab base URL | Required by service settings |
| `GITLAB_TOKEN` | Yes | GitLab credential | Required by settings validation |
| `SUPABASE_URL` | Yes | Supabase URL or PostgreSQL DSN | Intake can derive a PostgreSQL DSN from an HTTPS-style Supabase URL |
| `SUPABASE_SERVICE_KEY` or `SUPABASE_ANON_KEY` | Yes | Database password source for DSN derivation | Intake uses this when converting an HTTPS-style Supabase URL into a Postgres DSN |
| `MINIO_ENDPOINT` | Yes | MinIO S3 endpoint | Used for shared-spec staging |
| `MINIO_ACCESS_KEY` | Yes | MinIO access key | Used by the S3 client |
| `MINIO_SECRET_KEY` | Yes | MinIO secret key | Used by the S3 client |
| `MINIO_HICLAW_BUCKET` | Yes | Shared bucket name | Example: `clawcluster-sharedfs` |
| `HICLAW_MANAGER_PORT` or `OPENCLAW_MANAGER_URL` | Yes | Manager notification target | One of the two must be set |
| `CLUSTER_NAME` | Yes | Default workspace and tagging context | Used during normalization |
| `DOMAIN` | Yes | Cluster domain context | Added to `constraints_json` |
| `WORKER_JWT_SECRET` | Yes | Shared webhook HMAC secret | Reused here as the webhook signature secret |

### Health behavior

`GET /health` is dependency-aware. It checks:

- PostgreSQL reachability through the Supabase client
- HiClaw Manager reachability
- MinIO bucket reachability

The route returns `200` only when database and Manager checks are healthy. Storage failure alone degrades the payload but does not change the service’s core dependency logic.

---

## Publisher Bridge (port 8101)

The Publisher Bridge is the controlled outbound writer for ClawCluster. It takes approved artifacts from shared storage and writes them back into EchoThink systems of record.

### Purpose

The service publishes approved results to:

- Outline documents
- GitLab branches and optional merge requests
- Dify workflow imports
- n8n workflow imports

It also records `external_refs`, persists published artifact metadata, and advances the associated `work_item` status.

### Supported publication targets

| Target | Behavior | Typical inputs |
|--------|----------|----------------|
| Outline | Create a new document or update an existing one | Markdown artifact plus document metadata |
| GitLab branch | Create or reuse a branch and commit files | `project_id`, branch metadata, file actions or artifact-derived files |
| GitLab MR | Create branch and commit, then open a merge request | Same as branch publish plus optional MR title/description |
| Dify | Import a workflow definition | JSON workflow artifact |
| n8n | Import a workflow definition | JSON workflow artifact |

All artifacts are read from MinIO via `s3://` URIs. The API rejects non-S3 artifact URIs.

### API endpoints

| Endpoint | Method | Auth model | Purpose |
|----------|--------|------------|---------|
| `/publish` | `POST` | Bearer token equal to `WORKER_JWT_SECRET` | Publish one approved artifact set to one target |
| `/publish/{task_run_id}/status` | `GET` | No bearer auth in current code | Return publish status, known external refs, and recorded artifacts |
| `/health` | `GET` | None | Return service status, DB connection flag, and supported targets |

> Implementation note: some design material refers to `GET /publish/{work_item_id}/status`. The checked-in service actually keys this route by `task_run_id`.

### PublishTarget enum values

The service exposes the following `PublishTarget` enum values:

| Enum value | Meaning |
|------------|---------|
| `outline` | Publish Markdown into Outline |
| `gitlab_branch` | Commit artifacts into a GitLab branch |
| `gitlab_mr` | Commit artifacts into GitLab and open a merge request |
| `dify` | Import a workflow into Dify |
| `n8n` | Import a workflow into n8n |

### Publication workflow

1. The caller sends `POST /publish` with a `work_item_id`, `task_run_id`, `target`, one or more `s3://` artifact URIs, and any target-specific metadata.
2. The service validates the bearer token against `WORKER_JWT_SECRET`.
3. It looks up existing `external_refs` for the work item.
4. If the target already has a recorded external reference, the service returns a skipped, idempotent result.
5. Otherwise, it marks the work item as `publishing`.
6. The service dispatches to the registered publisher implementation for the target.
7. The publisher reads artifacts from MinIO and performs the external write.
8. New external references are merged into `clawcluster.external_refs`.
9. Published artifact metadata is inserted into `clawcluster.artifacts`.
10. The work item is marked `complete` on success or `failed` on exception.

### Target-specific behavior

**Outline**

- Selects a Markdown artifact, preferring `.md` or `.markdown`
- Uses `/api/documents.create` when `metadata.document_id` is absent
- Uses `/api/documents.update` when `metadata.document_id` is present
- Supports `collection_id`, `parent_document_id`, `title`, and `publish`
- Records `outline_doc_id` in `external_refs`

**GitLab**

- Requires `metadata.project_id`
- Defaults `base_branch` to `main`
- Defaults `branch_name` to `clawcluster/<work_item_id>/<task_run_id>`
- Defaults the commit message to `Publish approved output for <work_item_id>`
- Supports three ways to define commit content:
  - explicit `metadata.commit_actions`
  - explicit `metadata.files`
  - implicit artifact mapping from MinIO files or a JSON action manifest
- For `gitlab_mr`, optionally creates an MR with `mr_title`, `mr_description`, `remove_source_branch`, and `squash`
- Records `gitlab_project_id`, and for MR publishes also records `gitlab_mr_iid`

**Dify and n8n**

- Select a JSON artifact, preferring `.json`
- Support `artifact_uri`, `endpoint_path`, `http_method`, `request_body`, `embed_artifact`, and `workflow_id_path`
- Default endpoints are:

  | Target | Default import endpoint |
  |--------|-------------------------|
  | Dify | `/v1/workflows/import` |
  | n8n | `/api/v1/workflows` |

- Record `dify_workflow_id` or `n8n_workflow_id` in `external_refs`

### Idempotency and `external_refs`

The Publisher Bridge treats `clawcluster.external_refs` as the idempotency ledger for outbound publication.

The flow is:

1. Read the existing `external_refs` row for the `work_item_id`.
2. Map the requested target to the reference field that proves publication already happened.
3. If that field is present, return `status="skipped"` and `idempotent=true`.
4. If the publish succeeds, merge new references into the same row with an upsert.

Target-to-ref mapping in the current implementation is:

| Target | Ref field used for idempotency |
|--------|--------------------------------|
| `outline` | `outline_doc_id` |
| `gitlab_branch` | `gitlab_project_id` |
| `gitlab_mr` | `gitlab_mr_iid` |
| `dify` | `dify_workflow_id` |
| `n8n` | `n8n_workflow_id` |

The merge operation uses `INSERT ... ON CONFLICT (work_item_id) DO UPDATE` with `COALESCE` so new non-null refs overwrite empties without erasing older refs.

```sql
INSERT INTO clawcluster.external_refs (work_item_id, ...)
VALUES (...)
ON CONFLICT (work_item_id)
DO UPDATE SET
  column = COALESCE(EXCLUDED.column, clawcluster.external_refs.column),
  updated_at = now();
```

This pattern matters operationally:

- retries are safe when the target already has a durable external ref
- partial publication can be resumed without overwriting unrelated refs
- one work item can accumulate refs for Outline, GitLab, Dify, n8n, and Matrix over time

> Important caveat: for `gitlab_branch`, the current idempotency check uses `gitlab_project_id`, which is coarser than branch identity. In practice, once a GitLab project ref exists for the work item, branch publication is considered already done.

### Required environment variables

| Variable | Required | Purpose | Notes |
|----------|----------|---------|-------|
| `PORT` | Yes | Listener port | Defaults to `8101` |
| `OUTLINE_URL` | Yes | Outline base URL | Used by the Outline publisher |
| `OUTLINE_API_TOKEN` | Yes | Outline credential | Sent as bearer auth |
| `GITLAB_URL` | Yes | GitLab base URL | Used by `python-gitlab` |
| `GITLAB_TOKEN` | Yes | GitLab credential | Used by `python-gitlab` |
| `DIFY_URL` | Yes | Dify base URL | Used by the Dify publisher |
| `DIFY_API_KEY` | Yes | Dify credential | Sent as bearer auth |
| `N8N_URL` | Yes | n8n base URL | Used by the n8n publisher |
| `N8N_API_KEY` | Yes | n8n credential | Sent as `X-N8N-API-KEY` |
| `SUPABASE_URL` | Yes | PostgreSQL DSN for `asyncpg` | The current publisher code passes this value directly into `asyncpg.create_pool()` |
| `SUPABASE_SERVICE_KEY` | Yes | Environment-contract parity and future use | Loaded by settings even though the current DB client does not consume it directly |
| `MINIO_ENDPOINT` | Yes | MinIO S3 endpoint | Used by the artifact store |
| `MINIO_ACCESS_KEY` | Yes | MinIO access key | Used by the S3 client |
| `MINIO_SECRET_KEY` | Yes | MinIO secret key | Used by the S3 client |
| `MINIO_HICLAW_BUCKET` | Yes | Default artifact bucket | Used when an `s3://` URI omits the bucket |
| `WORKER_JWT_SECRET` | Yes | Shared bearer token | Required for `POST /publish` |

---

## Policy Bridge (port 8102)

The Policy Bridge decides whether work may proceed automatically, must wait for human approval, or must be rejected because of budget or concurrency policy.

### Purpose

The service evaluates two independent policy dimensions:

1. **Approval routing** based on the work item’s `approval_policy` and `risk_level`
2. **Budget enforcement** based on daily spend, per-task cost, token limits, and active concurrency

When a human decision is required and a Matrix room is available, the bridge posts a Matrix message so the room becomes the live approval surface while Supabase remains the durable approval ledger.

### API endpoints

| Logical operation | Actual implementation path | Method | Auth model | Purpose |
|-------------------|----------------------------|--------|------------|---------|
| Evaluate work | `/policy/evaluate` | `POST` | Bearer token equal to `WORKER_JWT_SECRET` | Return `approved`, `pending_approval`, or `rejected` |
| Approve request | `/policy/approve` | `POST` | Bearer token equal to `WORKER_JWT_SECRET` | Record an approval decision |
| Reject request | `/policy/reject` | `POST` | Bearer token equal to `WORKER_JWT_SECRET` | Record a rejection decision |
| List pending approvals | `/policy/pending` | `GET` | No bearer auth in current code | Query the queue of unresolved approvals |
| Health | `/health` | `GET` | None | Simple service liveness response |

> Implementation note: some design material abbreviates these as `POST /evaluate`, `POST /approve`, and `GET /pending`. The checked-in API uses a `/policy` prefix.

### Approval matrix

The approval decision matrix is encoded in the `ApprovalPolicy._is_auto_approved()` logic.

| `approval_policy` | `risk_level=low` | `risk_level=medium` | `risk_level=high` | `risk_level=critical` |
|-------------------|------------------|---------------------|-------------------|-----------------------|
| `none` | auto | auto | auto | auto |
| `low` | auto | auto | human | human |
| `medium` | auto | human | human | human |
| `high` | human | human | human | human |
| `critical` | human | human | human | human |

Interpretation rules:

- `auto` means the Policy Bridge allows work to continue without creating an approval record
- `human` means the Policy Bridge creates a row in `clawcluster.approvals` and returns `decision="pending_approval"`
- budget violations are evaluated first and can reject work before approval routing happens

### Budget scopes and enforcement

Budget policies are evaluated across four scopes:

| Scope | Source field | Meaning |
|-------|--------------|---------|
| `global` | constant scope id `global` | Cluster-wide policy |
| `workspace` | `workspace_id` | Workspace-specific limits |
| `agent_profile` | `agent_profile_id` | Role- or profile-specific limits |
| `work_item_kind` | `work_item_kind` | Policy by normalized work type |

For each applicable scope, the bridge checks:

- `daily_cost_limit_usd`
- `per_task_cost_limit_usd`
- `token_limit_per_task`
- `concurrency_limit`

The evaluation model is:

- **daily spend** is computed from `clawcluster.task_runs.cost_usd` for the current UTC day
- **active concurrency** counts task runs whose status is `pending` or `running`
- **budget exceeded** becomes true if daily cost, per-task cost, or token limits are violated
- **concurrency blocked** becomes true if active tasks meet or exceed the configured limit

The service returns all violated policies plus a per-scope snapshot so callers can explain exactly why work was blocked.

### Matrix notification for human approvals

When a request requires human approval and `matrix_room_id` is present, the Policy Bridge attempts to notify the room by issuing:

```text
PUT /_matrix/client/v3/rooms/{room_id}/send/m.room.message/{txn_id}
```

Requirements for Matrix notification to succeed:

- `matrix_room_id` is present in the policy-evaluation request
- `MATRIX_ACCESS_TOKEN` is configured
- `MATRIX_HOMESERVER_URL` points to the Tuwunel homeserver

The Matrix message body includes:

- `work_item_id`
- `risk_level`
- `approval_policy`
- `requested_by`
- generated `approval_id`
- optional Matrix server name from `TUWUNEL_SERVER_NAME`

If Matrix notification fails, the approval row still exists in Supabase. The service logs the failure but does not roll back the approval request.

### Required environment variables

| Variable | Required | Purpose | Notes |
|----------|----------|---------|-------|
| `PORT` | Yes | Listener port | Defaults to `8102` |
| `WORKER_JWT_SECRET` | Yes | Shared bearer token | Required by the protected mutation endpoints |
| `SUPABASE_URL` or `SUPABASE_DB_DSN` | Yes | PostgreSQL DSN source | If `SUPABASE_URL` is not already a PostgreSQL DSN, set `SUPABASE_DB_DSN` |
| `MATRIX_HOMESERVER_URL` | Recommended | Matrix client base URL | Defaults to `http://tuwunel.clawcluster.svc:6167` |
| `MATRIX_ACCESS_TOKEN` | Recommended | Matrix credential for approval notifications | Required for room notification |
| `TUWUNEL_SERVER_NAME` | Optional | Included in approval messages | Helps operators correlate Matrix homeserver context |
| `SUPABASE_SERVICE_KEY` | Optional | Loaded for env-contract parity | The current DB client uses a DSN rather than the service key |

---

## Observability Bridge (port 8103)

The Observability Bridge links task runs to Langfuse traces and optionally mirrors durable execution outcomes into Graphiti.

### Purpose

This service makes task execution attributable across systems. It connects:

- `clawcluster.task_runs`
- Langfuse traces and cost/token metrics
- Graphiti knowledge-sync events

The bridge is the place where a task run becomes observably complete, not just operationally finished inside a Worker.

### API endpoints

| Endpoint | Method | Auth model | Purpose |
|----------|--------|------------|---------|
| `/trace/link` | `POST` | Bearer token equal to `WORKER_JWT_SECRET` | Store a Langfuse trace id on a task run |
| `/trace/sync` | `POST` | Bearer token equal to `WORKER_JWT_SECRET` | Fetch Langfuse metrics and write them back to the task run |
| `/event/complete` | `POST` | Bearer token equal to `WORKER_JWT_SECRET` | Mark the task run complete and optionally sync Langfuse + Graphiti |
| `/health` | `GET` | None | Simple service liveness response |

### What gets recorded per task

The Observability Bridge persists or returns the following execution fields:

| Signal | Stored or emitted as | Notes |
|--------|----------------------|-------|
| `trace_id` | `clawcluster.task_runs.langfuse_trace_id` | Linked explicitly or inferred from an existing task run |
| `cost_usd` | `clawcluster.task_runs.cost_usd` | Pulled from Langfuse when available |
| `token_count` | `clawcluster.task_runs.token_count` | Pulled from Langfuse when available |
| task completion status | `clawcluster.task_runs.status` | Set from `TaskCompleteEvent.status` |
| end timestamp | `clawcluster.task_runs.ended_at` | Uses supplied `completed_at` or current UTC time |
| `result_summary` | `clawcluster.task_runs.result_summary` | Stored during completion |
| `error_message` | `clawcluster.task_runs.error_message` | Stored during failure or error completion |
| Graphiti sync intent | response booleans | Returned as `graphiti_sync_requested` and `graphiti_sync_completed` |

### Trace linking and synchronization behavior

**`POST /trace/link`**

- Updates `langfuse_trace_id` for a known `task_run_id`
- Returns the updated task-run state
- Returns `404` if the task run does not exist

**`POST /trace/sync`**

- Accepts either an explicit `trace_id` or an existing linked trace on the task run
- Calls Langfuse at:

  ```text
  GET <LANGFUSE_URL>/api/public/traces/{trace_id}
  Authorization: Bearer <LANGFUSE_SECRET_KEY>
  ```

- Extracts cost and token values from common Langfuse-style fields such as `totalCost`, `costUsd`, `usage.totalCost`, `totalTokens`, and `tokenCount`
- Updates the task run with whichever metrics are available

**`POST /event/complete`**

This is the end-of-run integration hook.

The route:

1. marks the task run complete, failed, or cancelled
2. stores `result_summary`, `error_message`, `ended_at`, and optional `trace_id`
3. attempts a best-effort Langfuse metric sync if a trace is known
4. optionally posts a Graphiti sync event when `sync_graphiti=true` and `GRAPHITI_URL` is configured

The Graphiti sync request includes:

- `task_run_id`
- `work_item_id`
- task status
- `trace_id`
- `result_summary`
- arbitrary `metadata`

Failures in Langfuse sync or Graphiti sync are logged and surfaced in response flags, but they do not erase the underlying task completion record.

### Required environment variables

| Variable | Required | Purpose | Notes |
|----------|----------|---------|-------|
| `PORT` | Yes | Listener port | Defaults to `8103` |
| `LANGFUSE_URL` | Yes | Langfuse base URL | Used for trace lookup |
| `LANGFUSE_SECRET_KEY` | Yes | Langfuse API secret | Sent as bearer auth |
| `WORKER_JWT_SECRET` | Yes | Shared bearer token | Required by all mutation endpoints |
| `SUPABASE_URL` or `SUPABASE_DB_DSN` | Yes | PostgreSQL DSN source | If `SUPABASE_URL` is not a PostgreSQL DSN, provide `SUPABASE_DB_DSN` |
| `GRAPHITI_URL` | Optional | Graphiti sync target | If absent, Graphiti sync is disabled |
| `SUPABASE_SERVICE_KEY` | Optional | Env-contract parity | Loaded by settings but not required by the DB client |

---

## Common bridge patterns

Although each bridge owns a different integration boundary, the services share a set of implementation patterns.

### Authentication model

| Pattern | Current implementation |
|---------|------------------------|
| Internal bridge-to-worker auth | Publisher, Policy, and Observability protect mutation endpoints with `Authorization: Bearer <WORKER_JWT_SECRET>` |
| Webhook ingress auth | Intake uses HMAC body signatures instead of bearer auth |
| Health endpoints | All four bridges expose unauthenticated `GET /health` routes |

The current code compares bearer tokens directly to `WORKER_JWT_SECRET`; it does not parse or validate a JWT structure.

### Structured JSON logging

All four bridges emit structured JSON logs to stdout.

Common characteristics include:

- JSON payloads rather than plaintext log lines
- explicit service or logger names
- exception serialization on failure paths
- request or context metadata added through `extra` fields

The Publisher Bridge additionally attaches a correlation id and returns it in `X-Request-ID`.

### PostgreSQL access through `asyncpg`

All four bridges talk directly to PostgreSQL using small `asyncpg` pools.

Common traits:

- typical pool sizing is `min_size=1`, `max_size=5`
- operational state is persisted in the `clawcluster` schema
- JSON and JSONB values are used for flexible metadata such as `constraints_json`, `evidence_json`, and artifact metadata

There is one important DSN difference:

| Service | DSN behavior |
|---------|--------------|
| Intake | Can derive a PostgreSQL DSN from an HTTPS-style `SUPABASE_URL` |
| Publisher | Expects `SUPABASE_URL` itself to be a PostgreSQL DSN for `asyncpg` |
| Policy | Accepts a PostgreSQL `SUPABASE_URL` or a fallback `SUPABASE_DB_DSN` |
| Observability | Accepts a PostgreSQL `SUPABASE_URL` or a fallback `SUPABASE_DB_DSN` |

### Pydantic-based configuration

The bridge layer uses Pydantic v2-style configuration patterns throughout, but the implementations are not identical.

| Service | Configuration style |
|---------|---------------------|
| Intake | `pydantic-settings` v2 `BaseSettings` |
| Publisher | `pydantic-settings` v2 `BaseSettings` |
| Policy | Pydantic v2 `BaseModel` populated from `python-dotenv` and `os.getenv()` |
| Observability | Pydantic v2 `BaseModel` populated from `python-dotenv` and `os.getenv()` |

Operationally, you should still treat the bridge layer as a single env-driven configuration surface rooted in the repository’s `.env.example` contract.
