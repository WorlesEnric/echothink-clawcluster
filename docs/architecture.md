# ClawCluster Architecture

## Introduction and Motivation

ClawCluster is EchoThink's agent execution plane. EchoThink already has durable systems for documentation, code review, task state, knowledge, storage, and observability. What it needs in addition is a runtime that can take a normalized work request, form a supervised agent team around it, execute safely, and hand the result back into those systems. That is the job of ClawCluster.

The core implementation choice is deliberate: ClawCluster is built on HiClaw rather than as a custom orchestration mesh. HiClaw contributes the hardest production-grade pieces of the problem: a Manager-plus-Workers topology, Matrix-native human visibility, Higress-based credential brokerage, and a shared-storage operating model. EchoThink remains the system of record.

That relationship to EchoThink is fundamental. ClawCluster does not replace Outline, GitLab, Supabase, Hatchet, Graphiti, Langfuse, or MinIO. Instead, it coordinates work across them. ClawCluster turns those inputs into structured, governed execution.

HiClaw is a good fit because EchoThink wants bounded workers, audited delegation, real-time human intervention, and strong least-privilege controls. HiClaw already assumes that shape. ClawCluster therefore becomes:

```text
+ HiClaw team architecture
+ OpenClaw runtime inside manager and workers
+ EchoThink bridge services, policy, and publication logic
+ EchoThink systems of record for durable state
```

## High-Level Topology

ClawCluster runs in a separate cluster from the main EchoThink infra stack. EchoThink owns durable project truth. ClawCluster owns active team coordination and execution state.

```text
+------------------------------------------------------------------+
|                    EchoThink Infra Cluster                       |
|------------------------------------------------------------------|
| Outline | GitLab | Supabase/Postgres | Hatchet | Graphiti        |
| LiteLLM | Langfuse | MinIO | Dify | n8n                         |
+-------------------------------+----------------------------------+
                                |
                    private ingress / VPN / mTLS
                                |
+------------------------------------------------------------------+
|                 ClawCluster (HiClaw-Based) Cluster               |
|------------------------------------------------------------------|
| Higress Gateway  -> agent auth, MCP, LLM routing                |
| Tuwunel Matrix + Element Web -> communication and supervision   |
| HiClaw Manager -> coordination, lifecycle, heartbeat            |
| Worker Containers -> planner, workflow, coding, qa, knowledge   |
| Bridge Services -> intake, publisher, policy, observability     |
| Shared Storage -> MinIO-backed specs, artifacts, results        |
+------------------------------------------------------------------+
                                |
              humans work Outline-first, intervene through Matrix
```

The LLM routing chain is:

```text
Worker / Manager -> Higress -> LiteLLM -> Model Provider
```

## Core Design Principles

### 1. HiClaw-native team structure

ClawCluster starts with one Manager coordinating specialized Workers. That gives a clear control point for decomposition, rerouting, escalation, and shutdown, and avoids ambiguous ownership when work stalls.

### 2. Outline-first human workflow

Outline remains the primary place for briefs, plans, and reviews. Matrix is the operational supervision bus. This prevents important project knowledge from drifting into chat-only history.

### 3. Gateway-enforced least privilege

Workers should hold revocable gateway credentials, not upstream secrets. Higress is therefore mandatory in the production design: it issues per-worker access, scopes routes, and centralizes policy.

### 4. Stateless, replaceable workers

Persistent task context belongs in shared storage, structured records, and Matrix rooms, not in container memory. A worker should be restartable without losing the task.

### 5. Structured work over chat-only coordination

Execution always runs from normalized `work_items`, `task_runs`, approvals, and artifacts. Chat adds context, but typed records are what make the system observable, budgetable, and governable.

### 6. Evidence-first publication

ClawCluster should publish only after it has produced reviewable artifacts, summaries, and trace links. Drafts may be autonomous; publication must be inspectable.

### 7. Human approval for irreversible actions

Publishing to GitLab, Outline, Dify, or n8n must flow through approval policy. Humans remain in control of irreversible writes and high-risk decisions.

### 8. Reuse EchoThink systems of record

ClawCluster is an execution layer, not a second source of truth. Code truth stays in GitLab, task truth in Supabase, knowledge truth in Graphiti, and long-form intent in Outline.

## Component Model

### Higress Gateway

Higress is the security boundary for ClawCluster, not an optional edge proxy. Every Manager and Worker should authenticate as a distinct Higress consumer. That enables per-worker tokens, route scoping, rate limits, and selective revocation.

Higress has two jobs. First, it fronts LLM access through `Worker -> Higress -> LiteLLM -> provider`. Second, it exposes approved MCP services to agents without placing real tool secrets inside worker containers. This is why Higress is not optional: without it, replaceable workers would need to carry the very credentials the architecture is designed to protect.

### Tuwunel Matrix

Tuwunel Matrix is the team communication bus. It is chosen because Matrix gives ClawCluster durable rooms, human visibility, and interruption as a first-class behavior. The point is not just messaging; it is auditable delegation.

The default room topology is one room per major work item. The room contains the Manager, whichever workers are assigned, and humans only when supervision is needed. Specialist rooms may exist for long-running coding or QA threads, but hidden worker-only side channels are discouraged. Room IDs should be mirrored into structured state for traceability.

### Element Web

Element Web is the supervision UI for Matrix. Operators, reviewers, and project owners use it when a task is blocked, risky, or executing overnight. It is not the primary authoring surface; it is the intervention surface.

### HiClaw Manager

The Manager is the coordination nucleus. It receives work notifications, reads the canonical work item, selects the right worker role, creates or reuses Matrix rooms, stages task specs into shared storage, monitors heartbeat, and decides when to escalate, retry, reroute, or stop a worker.

Worker lifecycle is explicitly Manager-owned: create identity, grant route permissions, watch health and idleness, and recreate when necessary. Escalation also lives here: when policy, ambiguity, or failure requires human judgment, the Manager turns that into a visible request in Matrix.

### Worker Containers

All workers are OpenClaw-based containers that consume shared task context, talk in Matrix, use Higress for LLM and MCP access, and write results back to MinIO-backed storage.

| Worker role | Responsibility | Typical outputs |
|-------------|----------------|-----------------|
| `planner-worker` | Break objectives into tasks, acceptance criteria, and status views | plans, breakdowns, ADR drafts, status summaries |
| `workflow-worker` | Draft Dify and n8n workflows from structured briefs | workflow JSON, validation notes, publish-ready drafts |
| `coding-worker` | Gather repo context, plan implementation, and coordinate code changes | patch plans, code deltas, MR-ready summaries |
| `qa-worker` | Validate outputs against acceptance criteria and risk posture | validation reports, review notes, release recommendations |
| `knowledge-worker` | Search and sync durable facts into Graphiti | context summaries, graph episodes, provenance links |

The `coding-worker` may delegate heavy editing to a manager-hosted coding CLI. ### Bridge Services

The bridge layer connects the HiClaw runtime to EchoThink's authoritative systems.

| Service | What it does | API surface | Connects |
|---------|--------------|-------------|----------|
| Intake bridge | Normalizes external events into canonical work items, stages `spec.md`, and notifies the Manager | `POST /webhooks/outline`, `POST /webhooks/gitlab`, `POST /work-items`, `GET /health` | Outline and GitLab -> Supabase, MinIO, HiClaw Manager |
| Publisher bridge | Performs approved external writes and records publication refs | `POST /publish`, `GET /publish/{task_run_id}/status`, `GET /health` | MinIO artifacts and task runs -> Outline, GitLab, Dify, n8n, Supabase |
| Policy bridge | Evaluates approval, budget, cost, token, and concurrency rules | `POST /policy/evaluate`, `POST /policy/approve`, `POST /policy/reject`, `GET /policy/pending`, `GET /health` | Workers and Manager -> Supabase approvals and budget policies |
| Observability bridge | Links traces, completes task runs, and optionally syncs Graphiti | `POST /trace/link`, `POST /trace/sync`, `POST /event/complete`, `GET /health` | HiClaw runtime -> Langfuse, Graphiti, Supabase |

### Shared Storage Layer

ClawCluster uses EchoThink MinIO as a HiClaw-compatible shared filesystem. The bucket is configured through `MINIO_HICLAW_BUCKET` and defaults to `clawcluster-sharedfs`; the operational object prefix is `hiclaw-storage/`.

```text
hiclaw-storage/
  agents/
    <role>/
      SOUL.md
      openclaw.json
      skills/
  shared/
    tasks/
      task-<id>/
        meta.json
        spec.md
        base/
        result.md
        artifacts/
        validation/
        publish/
    knowledge/
      episodes/
      exports/
```

Agent directories hold role configuration. Task directories hold normalized specs, working material, artifacts, validation evidence, and publication payloads. This storage layout is what allows worker restarts without losing the task.

## Data Model Summary

Structured ClawCluster state lives in the `clawcluster` schema in Supabase-backed PostgreSQL.

| Table | Purpose |
|-------|---------|
| `agent_profiles` | Defines manager and worker roles, approval class, and storage defaults |
| `skill_definitions` | Stores versioned skill contracts and runtime metadata |
| `agent_skill_bindings` | Maps roles to the skills they may execute |
| `work_items` | Canonical normalized requests for work |
| `task_runs` | Execution attempts with timing, trace, and cost metadata |
| `approvals` | Formal approval gate requests and decisions |
| `artifacts` | Durable references to outputs produced during a run |
| `hiclaw_workers` | Mirror of worker identities, room refs, tokens, and runtime state |
| `external_refs` | Links to Outline docs, GitLab MRs, Dify flows, n8n flows, and Matrix rooms |
| `budget_policies` | Budget, token, and concurrency limits by scope |

The most important modeling rule is simple: every intake path must normalize into a canonical work item before the Manager acts.

## Task Lifecycle

1. A human updates an Outline document, GitLab issue, GitLab MR, or manual request.
2. The intake bridge receives the event and verifies source authenticity.
3. It normalizes the payload into a `work_item`, inserts it into `clawcluster.work_items`, and stages `spec.md` into MinIO.
4. The intake bridge notifies the HiClaw Manager.
5. The Manager classifies the workload and selects the right worker role.
6. The Manager creates or reuses the Matrix room for the work item and records the room reference.
7. The Manager provisions or selects the worker runtime and ensures the worker has the correct Higress consumer identity.
8. The worker loads the task specification from shared storage and begins execution.
9. The worker uses Higress for LLM and MCP access, posts progress into Matrix, and writes artifacts back to MinIO.
10. If code-heavy editing is required, the `coding-worker` delegates implementation to the manager-hosted coding CLI while keeping the same run context.
11. The `qa-worker`, automated checks, or both validate the output.
12. The policy bridge evaluates risk, approval class, cost, and concurrency.
13. If human approval is required, the approval decision is recorded in `approvals`, while Matrix remains the live discussion surface.
14. The publisher bridge reads approved artifacts from MinIO and performs the external write to Outline, GitLab, Dify, or n8n.
15. The observability bridge links or syncs the Langfuse trace, updates `task_runs`, and optionally syncs durable knowledge into Graphiti.
16. The Manager observes completion, retires or idles the worker, and leaves the durable record in Supabase, Matrix, and MinIO.

## Skill Model

A skill YAML is the executable contract that says what a role can do, what it needs, what it produces, which tools it can call, and where it should run. HiClaw provides the team structure; skills provide the actual capability.

Each manifest defines metadata, category, `runtimeClass`, inputs, outputs, tool dependencies, guardrails, validation requirements, and publication targets. The v1 catalog contains 13 initial skills:

| Skill | Runtime | Purpose |
|-------|---------|---------|
| `doc.outline.read` | `worker-container` | Read and normalize Outline context |
| `doc.outline.write_draft` | `worker-container` | Write draft content back to Outline |
| `plan.breakdown` | `worker-container` | Convert objectives into tasks and dependencies |
| `plan.status_summarize` | `worker-container` | Produce decision-ready status updates |
| `graph.search` | `worker-container` | Retrieve Graphiti context |
| `graph.sync_episode` | `worker-container` | Write stable facts back to Graphiti |
| `workflow.dify.build` | `worker-container` | Build Dify workflow drafts |
| `workflow.n8n.build` | `worker-container` | Build n8n workflow drafts |
| `workflow.publish_draft` | `worker-container` | Publish approved workflow drafts |
| `code.repo.implement` | `manager-cli` | Implement bounded repo changes through the manager-hosted CLI |
| `code.repo.review` | `worker-container` | Review code changes for quality and risk |
| `code.repo.fix_from_review` | `manager-cli` | Apply targeted fixes from review feedback |
| `qa.run_validation` | `worker-container` | Run validation and recommend readiness |

The design shorthand often says `worker` versus `manager-cli`. In the checked-in manifests, the worker-side runtime is named `worker-container`, which is the concrete containerized worker class.

## Security and Credential Model

The security model is based on gateway-owned secrets. Workers should carry only bounded runtime identity: their Higress consumer token, room and storage references, and short-lived bearer tokens for internal bridge calls. They should not carry provider master credentials or broad external API secrets.

Higress and approved MCP configuration hold upstream credentials. That allows rotation, revocation, and route policy to happen centrally. The bridge services also enforce authenticated internal calls, so publishing, approval mutation, and observability updates are explicit, attributable actions.

Matrix visibility is part of the safety model. A risky action discussed in a human-visible room is materially safer than a hidden autonomous side effect. For that reason, room IDs should be treated as governance references and mirrored into structured state when publication or approval occurs.

## Network and Deployment Model

ClawCluster is deployed as its own HiClaw-based cluster. That separation isolates the execution plane, allows worker-specific scaling and policy, and prevents agent-runtime churn from destabilizing EchoThink's systems of record.

The cluster requires private connectivity to Outline, GitLab, Supabase, Hatchet, Graphiti, LiteLLM, MinIO, Langfuse, Dify, and n8n. Access should use private ingress, VPN, or equivalent non-public transport, with mTLS where practical. Worker containers should never expose public inbound ports.

The NetworkPolicy stance should be default-deny with narrow allowances. Workers should be allowed to egress only to DNS, Higress, Tuwunel Matrix, the Manager, bridge services, and approved storage or infra endpoints. Bridge services may talk to the EchoThink APIs they integrate with. The existing Helm chart already implements the most important first restriction: worker egress is limited to internal ClawCluster services plus the approved MinIO CIDR.

## System of Record Ownership

| State domain | Authoritative owner |
|--------------|---------------------|
| Long-form project intent and documentation | Outline |
| Structured work items and approval state | Supabase/Postgres |
| Durable workflow trigger records | Hatchet |
| Human-agent operational conversation | Tuwunel Matrix / Element Web |
| Source code, branches, merge requests, review history | GitLab |
| Shared task files and produced artifacts | MinIO |
| Temporal knowledge and stable fact history | Graphiti |
| LLM traces, latency, and cost analytics | Langfuse |
| Agent-facing routing and tool policy | Higress |

## Key File Locations

| Purpose | Path |
|---------|------|
| Main architecture document | `docs/architecture.md` |
| Local deployment topology | `docker-compose.yml` |
| ClawCluster schema migration | `migrations/supabase/001-clawcluster-schema.sql` |
| Manager runtime profile | `agents/manager/openclaw.json` |
| Worker role profiles | `agents/` |
| Skill manifests | `skills/` |
| Intake bridge API | `services/intake-bridge/src/api/routes.py` |
| Publisher bridge API | `services/publisher-bridge/src/api/routes.py` |
| Policy bridge API | `services/policy-bridge/src/api/routes.py` |
| Observability bridge API | `services/observability-bridge/src/api/routes.py` |
| MinIO bucket bootstrap script | `scripts/setup-minio-buckets.sh` |
| Health verification helper | `scripts/healthcheck.sh` |
| Helm chart and NetworkPolicy | `k8s/helm/clawcluster/` |
| Kustomize base manifests | `k8s/kustomize/base/` |

This document is intended to be the primary operator-facing reference for the ClawCluster repository, grounded in the upstream design in `echothink-infra/docs/clawcluster-design.md`.
