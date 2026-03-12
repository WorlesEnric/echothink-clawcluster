# Scaling Guide

This document describes when and how to scale the core components of EchoThink ClawCluster. It covers single-host Docker Compose operations, Kubernetes scaling patterns, and the service-specific signals that should drive those decisions.

## Table of Contents

- [General Scaling Principles](#general-scaling-principles)
- [HiClaw Manager](#hiclaw-manager)
- [Worker Containers](#worker-containers)
- [Bridge Services](#bridge-services)
- [Higress Gateway](#higress-gateway)
- [Tuwunel Matrix](#tuwunel-matrix)
- [MinIO Shared Storage](#minio-shared-storage)
- [Monitoring Metrics for Scaling Decisions](#monitoring-metrics-for-scaling-decisions)
- [Kubernetes HPA Reference](#kubernetes-hpa-reference)

---

## General Scaling Principles

1. **Scale coordination and execution separately.** The HiClaw Manager, bridge services, and Worker containers have different failure modes and different scaling signals. A bigger worker pool does not help if the Manager is memory-bound or policy approvals are blocked.
2. **Use backlog and concurrency policy, not CPU alone.** For ClawCluster, the first scaling signal is usually pending work in `clawcluster.task_runs` compared with the `concurrency_limit` set in `clawcluster.budget_policies`. CPU and memory validate the decision; they should not be the only reason to scale.
3. **Keep Worker replicas specialized and disposable.** Workers scale safely because they are stateless in Kubernetes and narrowly scoped by worker class. Increase `planner-worker` without touching `coding-worker` if planning demand is the only bottleneck.
4. **Protect the human-visible control plane first.** Tuwunel, Supabase, MinIO, and Higress preserve the evidence trail that explains what the agents did. Scale them conservatively and verify durability before chasing raw throughput.

---

## HiClaw Manager

The HiClaw Manager is the coordination brain of ClawCluster. It reads work items, chooses Worker classes, manages Matrix room usage, and keeps overall task state moving.

### Vertical scaling (first step)

Increase Manager memory before increasing replicas.

Why memory comes first:

- larger task queues keep more normalized work-item state in process;
- broader context windows increase the size of active prompts and room summaries;
- long-running coordination loops hold more references to Workers, artifacts, and approvals.

Operational guidance:

- start from the current baseline in `values.yaml` (`512Mi` request, `1Gi` limit);
- raise memory when resident set size stays above 70-75% during normal queue processing;
- raise CPU only after confirming the Manager is CPU-bound rather than blocked on Supabase, MinIO, Matrix, or Higress;
- increase `hiclawManager.config.maxConcurrentTasks` only after memory headroom is proven.

### Replicas and active-passive HA

For most ClawCluster deployments, **one active Manager is the correct topology**.

Reasons:

- coordination state is logically centralized;
- task routing should have one clear owner;
- multi-active Managers can double-assign work unless leader election and task claiming are explicit.

Consider active-passive HA only when all of the following are true:

- ClawCluster is expected to keep operating through a Manager node failure;
- task claiming is idempotent or protected by the database;
- the standby instance can see the same workspace and gateway configuration;
- failover procedures are tested in staging.

The current production overlay already contains an HPA for `hiclaw-manager`, but treat that as an infrastructure placeholder rather than proof that multi-active coordination is safe. Do not run multiple active Managers without explicit leader-awareness.

---

## Worker Containers

Workers are the horizontal scaling surface of ClawCluster. Each Worker type scales independently because each one handles a different workload family.

### Each Worker type scales independently

Current Worker classes and default resource profiles are:

| Worker | Default requests | Default limits | Scaling guidance |
|--------|------------------|----------------|------------------|
| `planner-worker` | `250m` CPU / `256Mi` memory | `500m` CPU / `512Mi` memory | Light planning and decomposition; scale out first when intake grows |
| `workflow-worker` | `250m` CPU / `256Mi` memory | `500m` CPU / `512Mi` memory | Light-to-medium workflow drafting; scale with concurrent Dify/n8n work |
| `coding-worker` | `500m` CPU / `512Mi` memory | `1` CPU / `1Gi` memory | Heaviest Worker; raise memory for large repos and broad diff contexts |
| `qa-worker` | `250m` CPU / `256Mi` memory | `500m` CPU / `512Mi` memory | Validation-oriented; scale when review queues or evidence checks pile up |
| `knowledge-worker` | `250m` CPU / `256Mi` memory | `500m` CPU / `512Mi` memory | Usually light, but scale for batch sync or indexing workloads |

### When to add Workers

Use workload state first, then infrastructure metrics.

Key signals:

- queue depth in `clawcluster.task_runs` for `pending` and `running` work;
- active task count compared with `clawcluster.budget_policies.concurrency_limit`;
- per-worker-class task duration compared with baseline;
- repeat backlog for one Worker class while others remain idle.

A useful operational query pair is:

```sql
SELECT wi.kind, COUNT(*) AS active_tasks
FROM clawcluster.task_runs AS tr
JOIN clawcluster.work_items AS wi ON wi.id = tr.work_item_id
WHERE tr.status IN ('pending', 'running')
GROUP BY wi.kind
ORDER BY active_tasks DESC;

SELECT scope_type, scope_id, concurrency_limit
FROM clawcluster.budget_policies
WHERE enabled = true
ORDER BY scope_type, scope_id;
```

The practical rule is: scale a Worker type when active work for that kind regularly approaches or exceeds the concurrency budget assigned to that workload class.

### Horizontal scaling via Helm values

In Helm, scale Workers in the `workers` section of `k8s/helm/clawcluster/values.yaml`:

```yaml
workers:
  - name: planner-worker
    replicas: 3
    resources:
      requests:
        cpu: 250m
        memory: 256Mi
      limits:
        cpu: 500m
        memory: 512Mi

  - name: coding-worker
    replicas: 2
    resources:
      requests:
        cpu: 500m
        memory: 1Gi
      limits:
        cpu: "2"
        memory: 2Gi
```

In Kubernetes, Worker runtime storage is `emptyDir`, which is exactly what makes scale-out safe: persistent task context stays in Matrix, MinIO, and Supabase rather than inside the Worker pod.

### Per-Worker resource guidance

- **`planner-worker`**: scale horizontally before vertically; the workload is usually I/O-bound on context retrieval and summarization.
- **`workflow-worker`**: keep memory modest, but scale replicas when multiple workflow drafts are generated at once.
- **`coding-worker`**: increase memory first for large repositories, broad search/replace work, or artifact-heavy code review loops.
- **`qa-worker`**: scale with review backlog; often cheaper to add another replica than to raise CPU.
- **`knowledge-worker`**: usually follows publication bursts or batch synchronization jobs; scale for burst throughput, not constant warm capacity.

---

## Bridge Services

The four bridge services are the ClawCluster stateless integration layer:

- `intake-bridge`
- `publisher-bridge`
- `policy-bridge`
- `observability-bridge`

All four are horizontally scalable. In Helm they default to one replica, and the production kustomize overlay already provides HPAs for the four bridge Deployments.

### Horizontal scaling model

Bridge services all share the same base resource profile:

| Service | Default requests | Default limits | Default HPA range |
|---------|------------------|----------------|-------------------|
| `intake-bridge` | `100m` CPU / `128Mi` memory | `500m` CPU / `256Mi` memory | `2-6` |
| `publisher-bridge` | `100m` CPU / `128Mi` memory | `500m` CPU / `256Mi` memory | `2-6` |
| `policy-bridge` | `100m` CPU / `128Mi` memory | `500m` CPU / `256Mi` memory | `2-6` |
| `observability-bridge` | `100m` CPU / `128Mi` memory | `500m` CPU / `256Mi` memory | `2-6` |

### When to scale each bridge

| Bridge | Scale trigger | Typical action |
|--------|---------------|----------------|
| `intake-bridge` | Webhook volume rises, `201/202` latency grows, or work-item creation backlog appears | Add replicas first; inspect Manager and Supabase second |
| `publisher-bridge` | Multiple concurrent publish jobs block on downstream APIs | Add replicas and watch downstream rate limits |
| `policy-bridge` | Pending approval backlog grows or policy evaluation latency rises | Add replicas after confirming Supabase is healthy |
| `observability-bridge` | Trace-linking backlog or artifact-link write latency grows | Add replicas if Langfuse/Supabase are not the bottleneck |

### Deployment guidance

- Keep bridges stateless and idempotent.
- Scale bridges before raising resource limits if the bottleneck is request concurrency.
- If 5xx errors increase because of downstream tools, scaling the bridge alone will not fix the issue; correct the upstream dependency first.

---

## Higress Gateway

Higress is the gateway between ClawCluster agents and the EchoThink tool/model estate. It fronts LLM access, MCP exposure, and identity-aware routing.

### Single instance by default

A single Higress instance is usually sufficient for early production ClawCluster workloads because:

- the heaviest token-generation work happens upstream in LiteLLM and model providers;
- ClawCluster request volume is usually moderate compared with the rest of EchoThink;
- route policy and consumer state are easier to reason about with a single gateway instance.

### When to scale Higress

Scale Higress only after verifying that the bottleneck is the gateway itself rather than the upstream model/tool service.

Signals that justify scaling or tuning:

- proxy-added latency exceeds 50 ms over upstream service latency;
- request queueing appears in front of LLM or MCP routes;
- 5xx responses increase while upstream services remain healthy;
- open upstream connection pools stay saturated.

### Upstream keepalive and connection pool tuning

Before adding replicas, tune connection reuse:

- keep persistent upstream connections warm to LiteLLM and frequently used MCP services;
- avoid per-request connection setup for hot paths such as Manager-to-LLM traffic;
- increase connection pool size only when upstreams can actually absorb more concurrency;
- scale the gateway only after keepalive reuse and pool sizing are no longer enough.

For Kubernetes, increasing `higress.replicas` is the last step, not the first step.

---

## Tuwunel Matrix

Tuwunel is the human-visible communication plane for ClawCluster. It is deployed as a Kubernetes `StatefulSet` with a PVC and also depends on the PostgreSQL database configured by `TUWUNEL_DB_URL`.

### Do not scale horizontally by default

Do **not** scale Tuwunel horizontally just because room traffic increases.

Reasons:

- Matrix homeserver semantics are stateful;
- the pod already owns persistent local state through a PVC;
- horizontal scale changes the persistence and coordination model, not just request concurrency.

Unless you have a documented multi-node Matrix design, keep `replicas: 1`.

### Vertical scaling

For Tuwunel, memory is usually the first knob.

Watch for growth driven by:

- connected users and service accounts;
- active room count;
- retained room history;
- media/cache churn;
- the backing PostgreSQL database behind `TUWUNEL_DB_URL`.

Raise memory before CPU when room count and connected-user count grow together.

### Archive policy for completed work-item rooms

When room count becomes large, introduce an archive policy for completed work-item rooms:

1. post a final task summary in the room;
2. ensure the relevant external refs are linked in Supabase;
3. keep the room read-only for an agreed retention period;
4. export or snapshot room metadata if your compliance policy requires it;
5. tombstone or clean up only after the audit requirement is met.

That keeps Matrix usable without silently deleting the only visible execution trail.

---

## MinIO Shared Storage

ClawCluster does not run its own separate MinIO instance. It uses the existing EchoThink MinIO service and stores shared files in the bucket referenced by `MINIO_HICLAW_BUCKET`, under the `hiclaw-storage/` prefix.

### Scaling model

Because MinIO is an upstream shared service, scale the EchoThink MinIO deployment itself rather than adding a second ClawCluster-specific object store.

The ClawCluster-specific operational concern is usually not throughput but retention:

- task specs and outputs accumulate under `hiclaw-storage/shared/tasks/`;
- agent configs under `hiclaw-storage/agents/` should normally be retained;
- old generated artifacts can dominate object count and storage growth.

### Bucket lifecycle policy for old task artifacts

Use a lifecycle rule for task outputs, not for agent configs:

```bash
mc alias set clawcluster ${MINIO_ENDPOINT} ${MINIO_ACCESS_KEY} ${MINIO_SECRET_KEY}

mc ilm rule add \
  clawcluster/${MINIO_HICLAW_BUCKET} \
  --prefix "hiclaw-storage/shared/tasks/" \
  --expire-days 30
```

Do **not** apply that rule to `hiclaw-storage/agents/` unless you are certain agent configs are fully versioned elsewhere.

---

## Monitoring Metrics for Scaling Decisions

Track these metrics to know when to scale. Use Prometheus, Grafana, the gateway dashboard, MinIO metrics, and direct SQL queries against the `clawcluster` schema.

### HiClaw Manager

| Metric | Warning Threshold | Action |
|--------|-------------------|--------|
| Pending work items waiting for assignment | > 2x `maxConcurrentTasks` for 10 minutes | Raise memory first, then review Manager concurrency settings |
| RSS memory / memory limit | > 75% sustained | Increase Manager memory |
| `/health` latency | > 500 ms p95 | Inspect Supabase, MinIO, Matrix, and Higress dependencies before scaling |
| Worker heartbeat failures | > 3 consecutive misses for any hot Worker class | Fix dependency path; do not solve heartbeat loss with blind replica increases |
| Container restarts | > 1 restart per day | Investigate OOM or config drift before scaling |

### Worker Containers

| Metric | Warning Threshold | Action |
|--------|-------------------|--------|
| Active `task_runs` per Worker kind | Approaches `concurrency_limit` for 10 minutes | Add replicas for that Worker class |
| Pending queue depth per Worker kind | Sustained growth over one operating window | Scale the specific Worker type rather than the whole pool |
| CPU usage | > 80% sustained | Add a replica or raise CPU for that Worker type |
| Memory usage / OOM events | > 75% sustained or any OOM kill | Raise memory; for `coding-worker`, do this before adding replicas |
| Median task duration | > 2x baseline | Inspect upstream dependencies and repo size; then scale if the workload is healthy |

### Intake Bridge

| Metric | Warning Threshold | Action |
|--------|-------------------|--------|
| Webhook request rate | Sustained above normal baseline with rising latency | Add replicas |
| Work-item creation latency | > 500 ms p95 | Check Supabase write path, then scale if healthy |
| `202 Accepted` responses due to degraded downstreams | > 10% of requests | Investigate Manager and MinIO dependencies |
| HTTP 5xx rate | > 1% | Fix upstream dependency failures before scaling |
| Container restarts | > 1 restart per day | Raise memory or fix bad payload handling |

### Publisher Bridge

| Metric | Warning Threshold | Action |
|--------|-------------------|--------|
| Publish queue depth | Sustained growth | Add replicas |
| Downstream API latency | > 1 s p95 | Check Outline/GitLab/Dify/n8n health before scaling |
| Artifact fetch failures from MinIO | > 0.5% | Fix storage path or MinIO access first |
| HTTP 5xx rate | > 1% | Investigate downstream credentials or rate limits |
| Retry count per publish job | > 3 average retries | Scale only after downstream bottlenecks are addressed |

### Policy Bridge

| Metric | Warning Threshold | Action |
|--------|-------------------|--------|
| Pending approvals backlog | Sustained growth over one approval window | Add replicas and review approval staffing |
| Policy evaluation latency | > 300 ms p95 | Inspect Supabase query latency |
| Rejections due to concurrency limits | Sudden increase against normal baseline | Add the relevant Worker type or relax budget policy intentionally |
| Database pool saturation | Pool exhausted or frequent wait events | Increase DB pool carefully or reduce per-pod concurrency |
| Matrix notification failures | > 0 | Fix Matrix reachability before scaling |

### Observability Bridge

| Metric | Warning Threshold | Action |
|--------|-------------------|--------|
| Trace-link backlog | Sustained growth | Add replicas |
| Artifact-link write latency | > 500 ms p95 | Inspect Supabase and MinIO first |
| Langfuse ingestion failures | > 0.5% | Fix Langfuse path before scaling |
| HTTP 5xx rate | > 1% | Investigate upstream dependency failures |
| Container restarts | > 1 restart per day | Raise memory or fix malformed payload handling |

### Higress Gateway

| Metric | Warning Threshold | Action |
|--------|-------------------|--------|
| Proxy-added latency | > 50 ms over upstream latency | Tune keepalive and connection pools |
| Open upstream connections / pool size | > 80% sustained | Increase pool size if upstreams can absorb it |
| Request queue depth | Sustained growth | Add replicas only after confirming upstream health |
| HTTP 5xx rate | > 1% with healthy upstreams | Investigate gateway saturation or policy misconfiguration |
| CPU usage | > 70% sustained | Scale vertically first, then add replicas if required |

### Tuwunel Matrix

| Metric | Warning Threshold | Action |
|--------|-------------------|--------|
| `/_matrix/client/versions` latency | > 500 ms p95 | Inspect Tuwunel pod and backing PostgreSQL |
| RSS memory / limit | > 70% sustained | Increase memory |
| Connected clients | Rapid growth over established baseline | Re-check memory sizing and event retention |
| Active room count | Large sustained growth | Introduce room archive policy for completed work |
| PostgreSQL errors or persistence lag | Any sustained error rate | Scale or repair the database behind `TUWUNEL_DB_URL` |

### MinIO Shared Storage

| Metric | Warning Threshold | Action |
|--------|-------------------|--------|
| Read latency for `hiclaw-storage/` paths | > 100 ms p95 | Check upstream MinIO capacity and network path |
| 5xx or timeout rate | > 0.5% | Fix MinIO health before adding Worker replicas |
| Disk usage / tenant capacity | > 80% | Expand upstream MinIO storage |
| Object count under `hiclaw-storage/shared/tasks/` | Grows beyond retention target | Tighten lifecycle rules |
| Failed backup or mirror jobs | > 0 | Fix the backup path before relying on new scale-out |

---

## Kubernetes HPA Reference

When running on Kubernetes, use Horizontal Pod Autoscalers for the stateless bridge services and for Worker Deployments. The production overlay already includes bridge HPAs in `k8s/kustomize/overlays/production/hpa.yaml`; the Worker examples below match that same YAML style.

If you deploy with Helm, replace the Deployment names with your release-prefixed names (for example, `<release>-planner-worker`).

### Intake Bridge HPA

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: intake-bridge
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: intake-bridge
  minReplicas: 2
  maxReplicas: 6
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

### Publisher Bridge HPA

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: publisher-bridge
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: publisher-bridge
  minReplicas: 2
  maxReplicas: 6
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

### Policy Bridge HPA

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: policy-bridge
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: policy-bridge
  minReplicas: 2
  maxReplicas: 6
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

### Observability Bridge HPA

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: observability-bridge
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: observability-bridge
  minReplicas: 2
  maxReplicas: 6
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

### Planner Worker HPA

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: planner-worker
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: planner-worker
  minReplicas: 1
  maxReplicas: 6
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

### Workflow Worker HPA

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: workflow-worker
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: workflow-worker
  minReplicas: 1
  maxReplicas: 6
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

### Coding Worker HPA

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: coding-worker
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: coding-worker
  minReplicas: 1
  maxReplicas: 8
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

### QA Worker HPA

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: qa-worker
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: qa-worker
  minReplicas: 1
  maxReplicas: 6
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

### Knowledge Worker HPA

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: knowledge-worker
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: knowledge-worker
  minReplicas: 1
  maxReplicas: 4
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```
