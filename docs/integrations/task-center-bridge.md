# Task Center Bridge Design

## 1. Purpose

This document defines the ClawCluster-side bridge that connects EchoThink Task Center to the HiClaw-based execution plane.

Task Center owns the canonical task and operations ledger. ClawCluster owns supervised agent execution. The Task Center bridge is the contract between those two worlds.

## 2. Why a Dedicated Bridge Is Needed

The existing bridge layer is close to the right shape, but it does not yet provide a stable Task Center-facing contract.

- `intake-bridge` is source-oriented. It expects webhooks or manual `WorkItemCreate` payloads, not a canonical dispatched DAG node from Task Center.
- `policy-bridge` is worker-oriented. It evaluates runtime approvals and budgets after work is already in motion.
- `publisher-bridge` is output-oriented. It writes approved artifacts back to Outline, GitLab, Dify, and n8n.
- `observability-bridge` is completion-oriented. It attaches trace data and completion state to `task_runs`.

Task Center needs one stable surface that can:

- accept a canonical dispatch from `echothink-infra`;
- correlate it to ClawCluster runtime objects;
- fan into the existing bridges and Manager;
- emit structured execution events back to Task Center.

## 3. Placement in the ClawCluster Topology

```text
Task Center -> Hatchet workflow -> task-center-bridge -> intake-bridge -> HiClaw Manager
                                                        |                |
                                                        |                v
                                                        |             Workers
                                                        |
               +----------------------+-----------------+-------------------+
               |                      |                                     |
         policy-bridge         publisher-bridge                   observability-bridge
               |                      |                                     |
               +----------------------+-----------------+-------------------+
                                                        |
                                                        v
                                              task-center callbacks
```

The bridge should live in `echothink-clawcluster` because it is the runtime adapter between Task Center and the existing Manager-plus-bridges execution layer.

## 4. Responsibilities

The Task Center bridge should do five things.

### 4.1 Accept dispatches from Task Center

It receives a canonical dispatch request for an executable task node.

### 4.2 Materialize runtime-native state

It converts that request into:

- a ClawCluster `work_item`;
- shared task spec and context in MinIO;
- a correlation record between Task Center ids and ClawCluster ids.

### 4.3 Coordinate the existing bridge layer

It delegates runtime work to the existing bridge services instead of reimplementing them:

- use `intake-bridge` semantics for canonical `work_item` creation;
- use `policy-bridge` for runtime approvals and budget checks;
- use `publisher-bridge` for approved outbound writes;
- use `observability-bridge` for task completion, trace sync, and Graphiti sync.

### 4.4 Mirror runtime state back to Task Center

It sends structured callbacks for accepted, started, blocked, approval-required, artifact-produced, published, completed, failed, and cancelled states.

### 4.5 Keep Matrix and Task Center in their proper roles

Task Center remains the canonical discussion and planning surface.

Matrix remains the live execution room.

The bridge should sync summaries and decision points between them, not attempt to make both surfaces identical.

## 5. Non-Goals

The Task Center bridge should not:

- replace the HiClaw Manager;
- become a second planner for task DAG structure;
- own long-form human discussion history instead of Task Center;
- publish directly to external systems without using `publisher-bridge`;
- bypass the existing approval or observability contracts.

## 6. Suggested Schema Additions

The current `clawcluster` schema tracks `work_items`, `task_runs`, `approvals`, `artifacts`, and `external_refs`, but it does not yet have a dedicated correlation layer for Task Center.

### 6.1 `clawcluster.task_center_refs`

Recommended fields:

| Field | Purpose |
|------|---------|
| `id` | Primary key |
| `task_id` | Canonical Task Center task id |
| `task_node_id` | Optional DAG node id when the task is part of a compositional DAG |
| `dispatch_id` | Stable idempotency key for one dispatch request |
| `work_item_id` | Linked ClawCluster `work_item` |
| `latest_task_run_id` | Most recent runtime execution attempt |
| `matrix_room_id` | Execution room currently associated with this dispatch |
| `state` | Bridge-local correlated state |
| `created_at` | Creation time |
| `updated_at` | Last update time |

This table should be the first place operators look when tracing one task across the Task Center and ClawCluster boundary.

### 6.2 `clawcluster.task_center_outbox`

Recommended fields:

| Field | Purpose |
|------|---------|
| `id` | Primary key |
| `dispatch_id` | Correlated dispatch |
| `event_type` | Callback type |
| `payload_json` | Callback payload |
| `delivery_status` | `pending`, `delivered`, `failed`, `dead_letter` |
| `retry_count` | Delivery retry counter |
| `last_attempt_at` | Last delivery attempt time |
| `delivered_at` | Successful delivery time |

This table makes outbound callback delivery durable instead of trusting in-memory retries.

## 7. Canonical Object Mapping

The bridge should map objects as follows.

| Task Center concept | ClawCluster concept | Notes |
|---------------------|---------------------|-------|
| `taskcenter.tasks` row | `clawcluster.work_items` row | One executable task or task node becomes one runtime work item |
| Task Center dispatch | `task_center_refs.dispatch_id` | Stable idempotency and correlation key |
| Dispatch attempt | `clawcluster.task_runs` row | A dispatch may have multiple runtime attempts over time |
| Linked entities | `source_ref`, `constraints_json`, `external_refs` | Canonical entity refs and display aliases are summarized for runtime use |
| Acceptance specs | `acceptance_criteria` + validation artifacts | Keep runtime checks close to the work item |
| Approval decision | `clawcluster.approvals` row | Runtime approval remains a Policy Bridge concern |
| Execution room | `matrix_room_id` and `external_refs.matrix_room_id` | Matrix is the live room, Task Center stores mirrored summaries |

## 8. Suggested API Surface

The Task Center bridge should expose a Task Center-facing API rather than forcing Infra to call internal bridge endpoints directly.

### 8.1 `POST /task-center/dispatches`

Create or idempotently accept a dispatch.

Dispatch payloads should be entity-first. Task Center should resolve user-selected names and identifiers into canonical entity ids before dispatch whenever possible, then send both those ids and the human-visible display refs that workers need for runtime context.

Recommended request fields:

- `dispatch_id`
- `task_id`
- `task_node_id`
- `workspace_id`
- `objective`
- `summary`
- `execution_kind`
- `preferred_worker_family`
- `entity_refs`
- `entity_display_refs`
- `acceptance_specs`
- `approval_policy`
- `risk_level`
- `requested_by`
- `spec_uri`
- `artifacts_prefix`
- `context_json`

Recommended response fields:

- `dispatch_id`
- `accepted`
- `work_item_id`
- `status`
- `correlation_ref`

### 8.2 `GET /task-center/dispatches/{dispatch_id}`

Return the current correlated execution status.

Recommended response fields:

- `dispatch_id`
- `task_id`
- `task_node_id`
- `work_item_id`
- `task_run_id`
- `status`
- `matrix_room_id`
- `approval_state`
- `artifacts`
- `external_refs`

### 8.3 `POST /task-center/dispatches/{dispatch_id}/cancel`

Request cancellation of a running or pending dispatch.

### 8.4 `POST /task-center/dispatches/{dispatch_id}/resume`

Resume a dispatch that was intentionally paused or is waiting on a recoverable gate.

### 8.5 `POST /task-center/approvals/{approval_id}/decision`

Forward a human decision from Task Center back into ClawCluster.

Recommended fields:

- `decision`
- `decided_by`
- `notes`
- `evidence_json`

The bridge should then call the internal `policy-bridge` approval endpoint rather than letting Task Center talk to Policy Bridge directly.

## 9. Outbound Callback Contract

The bridge should deliver execution updates back to Task Center through a single callback sink such as `TASK_CENTER_CALLBACK_URL`.

Recommended callback event types:

- `dispatch.accepted`
- `dispatch.room_ready`
- `dispatch.started`
- `dispatch.progress`
- `dispatch.awaiting_approval`
- `dispatch.artifact`
- `dispatch.publish_state`
- `dispatch.completed`
- `dispatch.failed`
- `dispatch.cancelled`

Every callback payload should include at least:

- `dispatch_id`
- `task_id`
- `task_node_id`
- `work_item_id`
- `task_run_id` when known
- `workspace_id`
- `occurred_at`
- `status`
- `summary`
- `correlation_ref`
- `entity_refs`

## 10. Runtime Execution Path

The happy path should be:

1. Task Center resolves the task's linked entities through its alias and observation index, then schedules a runnable node through Hatchet.
2. Hatchet calls `POST /task-center/dispatches` on the ClawCluster bridge.
3. The bridge deduplicates by `dispatch_id`.
4. The bridge translates the payload into a canonical `work_item`, carries forward the entity refs and display aliases, and records `task_center_refs`.
5. The bridge stages or verifies runtime spec material in shared storage.
6. The bridge notifies the HiClaw Manager of the new work item.
7. The Manager assigns the work item to the appropriate worker family and creates or reuses a Matrix room.
8. The bridge emits `dispatch.accepted` and `dispatch.room_ready` callbacks.
9. During execution, the bridge relays progress, approvals, artifacts, and publish state back to Task Center.
10. On completion, the bridge relays final status, trace refs, artifacts, and publish refs back to Task Center.

## 11. Worker Family and Work Item Mapping

Task Center should not need to know detailed worker runtime logic. It should only choose an execution family hint.

Recommended mapping:

| Execution family | ClawCluster `work_item.kind` | Preferred worker family |
|------------------|------------------------------|-------------------------|
| `planner` | `plan.breakdown`, `plan.support`, `plan.status` | `planner-worker` |
| `workflow` | `workflow.author` | `workflow-worker` |
| `coding` | `code.implement` | `coding-worker` |
| `qa` | `code.review`, `qa.validate` | `qa-worker` |
| `knowledge` | `knowledge.sync` | `knowledge-worker` |

The Manager should retain the right to override the worker choice when runtime conditions demand it.

## 12. Approval and Discussion Model

The bridge should preserve a two-surface collaboration model.

### 12.1 Task Center

Task Center is the canonical place for:

- task definition;
- linked component context;
- explicit human-agent discussion over the task;
- human acceptance or business-level approval decisions.

### 12.2 Matrix

Matrix is the canonical place for:

- live worker coordination;
- interruption and escalation;
- time-sensitive operational supervision.

### 12.3 Bridge synchronization policy

The bridge should mirror only structured execution signals back to Task Center, for example:

- room created;
- worker asked a blocking question;
- approval required;
- summary of progress step;
- summary of final result.

It should not attempt to copy every raw Matrix message into Task Center.

## 13. Security and Idempotency

The bridge should enforce:

- private east-west network access only;
- service-to-service bearer auth or mTLS between Task Center, Hatchet, and ClawCluster;
- `dispatch_id` idempotency for every create request;
- durable outbox delivery for callbacks;
- explicit correlation ids in logs and payloads.

Task Center should never need direct credentials for Matrix, Manager internals, or worker secrets.

## 14. Failure Handling

Important failure rules should be:

- if dispatch acceptance succeeds but Manager notification fails, return accepted-but-degraded status and keep retrying through the bridge outbox/reconciler;
- if runtime approval is required, keep the dispatch alive in `awaiting_approval` rather than failing it;
- if Task Center callbacks fail, preserve them in `task_center_outbox` for retry;
- if ClawCluster loses room or worker state, the bridge should still be able to reconstruct status from `work_items`, `task_runs`, `approvals`, and `artifacts`;
- if Task Center retries the same dispatch, the bridge must return the existing correlated state rather than create duplicate work items.

## 15. Recommended v1 Implementation Strategy

The cleanest v1 approach is to add a new `task-center-bridge` service in `echothink-clawcluster` rather than overloading `intake-bridge`.

That new service should:

- import or share the existing `WorkItemCreate` model and normalization helpers where useful;
- write the Task Center correlation rows;
- call internal bridge or Manager APIs rather than duplicating their logic;
- own the Task Center callback contract and delivery outbox.

This keeps the existing bridges focused on their current responsibilities while giving Task Center one explicit runtime integration surface.

## 16. Final Boundary

The intended split is:

- Task Center says what work exists, why it exists, what it is linked to, and what success means.
- Hatchet says when that work should be executed and retried.
- The Task Center bridge says how a canonical task becomes a ClawCluster runtime job.
- ClawCluster says how specialized workers collaborate to complete that job safely.

That boundary keeps execution disciplined without forcing `echothink-clawcluster` to become the canonical project-planning database.
