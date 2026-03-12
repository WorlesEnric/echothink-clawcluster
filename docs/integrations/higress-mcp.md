# Higress and MCP Integration

This document explains how ClawCluster integrates Higress into the production execution path, how per-worker identity maps to gateway consumers, and how MCP exposure should be managed when integrating ClawCluster with EchoThink.

## Table of Contents

- [Why Higress Is Mandatory](#why-higress-is-mandatory)
- [Gateway Routing Chain](#gateway-routing-chain)
- [Per-Worker Consumer Token Model](#per-worker-consumer-token-model)
- [MCP Exposure Pattern](#mcp-exposure-pattern)
- [LiteLLM Route Configuration](#litellm-route-configuration)
- [Admin API](#admin-api-port-9000)
- [NetworkPolicy Enforcement](#networkpolicy-enforcement)
- [Environment Variables](#environment-variables-for-higress-configuration)

---

## Why Higress Is Mandatory

Higress is not an optional convenience proxy in ClawCluster. It is the security boundary that makes the HiClaw execution model acceptable in production.

The production requirement is:

- Workers authenticate as distinct gateway consumers.
- Workers do not hold provider-master credentials.
- Route policy is enforced centrally.
- MCP exposure is bounded per Worker identity.
- Revocation happens at the gateway rather than by rebuilding every Worker image.

This matters because the ClawCluster runtime is intentionally elastic and replaceable. Workers are expected to start, stop, and restart. If every Worker held raw upstream credentials, a single compromised container would immediately become a provider compromise. Higress prevents that failure mode by making the gateway own trust, policy, and revocation.

In short:

```text
worker compromise != provider compromise
```

That is the core security reason Higress is mandatory.

### Production expectation versus current repository state

The production design is stricter than the current checked-in bootstrap configs.

- The design requires upstream secrets to stay at the gateway or MCP layer.
- The repository already defines per-worker `higressConsumerToken` identities.
- The repository already routes GitLab MCP through Higress for the Manager, Coding Worker, and QA Worker.
- Some other MCP definitions in the checked-in agent configs still point directly at upstream services or use local `stdio` MCP servers.

Treat those direct MCP definitions as transitional bootstrap wiring. The production target remains gateway-owned least privilege.

---

## Gateway Routing Chain

The mandatory model-routing chain is:

```text
Worker / Manager -> Higress (8080/8443) -> LiteLLM -> model providers
```

The purpose of each hop is:

| Hop | Responsibility |
|-----|----------------|
| Worker or Manager | Authenticates as a bounded ClawCluster runtime identity |
| Higress | Applies consumer auth, route policy, MCP exposure, and revocation boundaries |
| LiteLLM | Applies model routing, fallback, budget logic, and EchoThink-wide model governance |
| Model provider | Executes the actual inference request |

This routing chain is explicitly aligned with the ClawCluster design decision that EchoThink keeps LiteLLM as the main model gateway while Higress remains the agent-facing security and policy layer.

### Listener ports

The checked-in deployment defaults are:

| Listener | Default port | Purpose |
|----------|--------------|---------|
| HTTP | `8080` | In-cluster worker and manager gateway traffic |
| HTTPS | `8443` | TLS-enabled gateway traffic where used |
| Admin | `9000` | Administrative plane for route and consumer management |

In Kubernetes, the Higress service exposes these ports internally and optionally exposes HTTP/HTTPS externally through a `LoadBalancer` or ingress path.

---

## Per-Worker Consumer Token Model

Every Manager and Worker should have its own Higress consumer identity.

In the repository, this shows up as a `higressConsumerToken` field in each agent’s `openclaw.json` plus per-role environment variables such as `HIGRESS_CONSUMER_TOKEN_MANAGER`.

### Identity mapping

| Runtime identity | Token variable in agent config | Current role of the token |
|------------------|--------------------------------|----------------------------|
| Manager | `HIGRESS_CONSUMER_TOKEN_MANAGER` | Gateway auth for Manager-owned model and MCP access |
| Planner Worker | `HIGRESS_CONSUMER_TOKEN_PLANNER_WORKER` | Reserved for planner route policy and future Higress-mediated tool scope |
| Workflow Worker | `HIGRESS_CONSUMER_TOKEN_WORKFLOW_WORKER` | Reserved for workflow route policy and future Higress-mediated tool scope |
| Coding Worker | `HIGRESS_CONSUMER_TOKEN_CODING_WORKER` | Used for GitLab-over-Higress plus Worker gateway identity |
| QA Worker | `HIGRESS_CONSUMER_TOKEN_QA_WORKER` | Used for GitLab-over-Higress plus Worker gateway identity |
| Knowledge Worker | `HIGRESS_CONSUMER_TOKEN_KNOWLEDGE_WORKER` | Reserved for knowledge route policy and future Higress-mediated tool scope |

### Why per-worker tokens matter

Per-worker consumer identity enables:

- **least privilege** — the planner does not need the same tool surface as the coding worker
- **auditability** — requests can be correlated to a specific Worker class or Manager action
- **rate limiting** — heavy coding traffic can be throttled independently of light planning traffic
- **revocation** — one compromised Worker can be cut off without taking the whole cluster offline

### How identity is represented in the runtime

The identity model spans several layers:

| Layer | Representation |
|-------|----------------|
| Agent config | `higressConsumerToken` in `agents/*/openclaw.json` |
| Supabase mirror | `clawcluster.hiclaw_workers.higress_consumer_id` |
| Runtime routing | `gatewayUrl` set to the Higress service on port `8080` |
| Network policy | Workers are allowed to egress to Higress but not to arbitrary internet destinations |

This gives operations a clean line from Worker runtime identity to gateway policy and back to durable execution records.

---

## MCP Exposure Pattern

Higress is the preferred place to expose agent-facing MCP tools that should not be directly reachable with raw upstream credentials.

### Current checked-in state

The repository currently shows three categories of MCP exposure:

| Tool family | Current state | Notes |
|-------------|---------------|-------|
| GitLab | Routed through Higress for Manager, Coding Worker, and QA Worker | Endpoint pattern: `http://higress.clawcluster.svc/mcp/gitlab` |
| GitHub | Not yet wired in checked-in agent configs | Still part of the design intent for Higress-managed MCP exposure |
| Filesystem / shared storage | Not currently routed through Higress; handled through MinIO-backed shared storage and `mcp-server-s3` `stdio` configs | This is operationally the shared-filesystem layer rather than a Higress-routed MCP surface today |

### Recommended production pattern

Use Higress MCP exposure for any tool surface that should be centrally governed and revocable. In practice, that means:

| Tool category | Recommended production pattern |
|---------------|--------------------------------|
| GitLab | Expose through Higress with per-consumer scope by project, operation type, and rate limit |
| GitHub | Expose through Higress when GitHub-backed workflows are added so Workers never hold broad GitHub PATs |
| Filesystem | Prefer MinIO-backed shared storage or a tightly scoped filesystem MCP exposed through Higress only when strictly necessary |

### Direct MCP definitions that still exist

The checked-in agent configs still contain direct or local MCP surfaces for services such as:

- Outline
- Graphiti
- Supabase
- MinIO (`stdio` S3 MCP)
- Matrix
- Dify
- n8n

Those are useful for bootstrap and development, but they do not yet satisfy the end-state “gateway owns the trust boundary” model. For production hardening, move high-value MCP surfaces behind Higress one by one, starting with the tools that carry the greatest write risk.

---

## LiteLLM Route Configuration

Higress is the agent-facing gateway. LiteLLM remains the EchoThink-side model router. That means Higress upstreams should point at EchoThink’s LiteLLM deployment rather than directly at model vendors.

### Operational model

The desired route model is:

1. Define one or more Higress upstreams that target `LITELLM_URL`.
2. Attach route policy to those upstreams by consumer identity.
3. Keep provider routing, fallback, and budget logic in LiteLLM.
4. Keep provider-master credentials out of Worker containers.

### What is present in the repository

The repository provides the surrounding integration contract, but not a fully checked-in Higress route object set.

Present today:

- `externalServices.litellm.url` in Helm values
- `LITELLM_URL` in Manager, Worker, and bridge environment contracts
- Higress bootstrap config for cluster name, domain, and listener ports

Not checked in today:

- concrete Higress consumer definitions
- concrete Higress route policies
- concrete Higress upstream declarations pointing at LiteLLM

That means production operators should treat route and consumer definitions as runtime-managed configuration owned through the Higress Admin API on port `9000`.

### Practical guidance

When defining Higress-to-LiteLLM routes:

- point all model traffic at EchoThink LiteLLM, not directly at Anthropic, OpenAI, or other providers
- segment routes by Worker class where cost or risk differs materially
- prefer named model groups or stable LiteLLM endpoints rather than provider-specific raw URLs
- ensure route policy can be revoked independently per consumer token

---

## Admin API (port 9000)

The Higress Admin API is the control plane for gateway configuration.

### What it is used for

Use port `9000` for:

- route policy creation and updates
- upstream registration and modification
- consumer creation, rotation, and revocation
- MCP exposure configuration
- emergency traffic controls such as deny rules or rate-limit changes

This is not a Worker data-plane port. Workers should talk to the gateway listeners on `8080` or `8443`; operators and automation should use the admin plane on `9000`.

### Exposure model in this repository

The repository exposes the admin plane in three ways depending on deployment mode:

| Deployment mode | Admin exposure |
|-----------------|----------------|
| Docker Compose | Direct host port mapping on `9000` |
| Nginx sidecar/front door | Proxied under `/higress-admin/` on `gateway.${DOMAIN}` |
| Kubernetes | Internal service port `9000`; optionally reachable through ingress or admin tooling |

Health checks for the Higress container are already wired to `http://127.0.0.1:9000/`.

---

## NetworkPolicy Enforcement

Workers are not intended to have unrestricted egress.

The checked-in Helm `NetworkPolicy` template enforces the first production-hardening rule: Worker pods may talk only to approved internal ClawCluster services, DNS, and the approved MinIO CIDR.

### Effective worker egress policy

| Allowed destination class | What this enables |
|---------------------------|-------------------|
| DNS on TCP/UDP `53` | Name resolution |
| ClawCluster internal services | Higress, Tuwunel Matrix, Element Web, HiClaw Manager, and all four bridges |
| MinIO CIDR on ports `9000` and `443` | Shared filesystem and artifact access |

The internal-ports allowlist currently includes:

- `80`
- `8080`
- `8443`
- `9000`
- `6167`
- `8088`
- `8100`
- `8101`
- `8102`
- `8103`

In practical terms, workers can reach:

- Higress
- Matrix
- the Manager
- the bridge services
- MinIO

They cannot, by policy, reach arbitrary external destinations unless additional rules are added.

### Why this matters to the Higress model

Network policy is what makes the “Higress is mandatory” statement enforceable rather than aspirational.

If workers can only reach Higress, Matrix, MinIO, and internal services, then:

- LLM traffic must go through the gateway
- MCP traffic can be forced through approved surfaces
- incident response can revoke access centrally
- accidental secret sprawl becomes much harder

---

## Environment Variables for Higress Configuration

Higress configuration is split between bootstrap environment, upstream/gateway integration variables, and per-worker identity variables.

### Bootstrap and listener variables

| Variable | Required | Purpose | Source |
|----------|----------|---------|--------|
| `DOMAIN` | Yes | Gateway domain and bootstrap identity | `.env.example`, Higress deployment env |
| `CLUSTER_NAME` | Yes | Cluster identity used in bootstrap config | `.env.example`, Higress deployment env |
| `HIGRESS_HTTP_PORT` | Yes | Public or host-mapped HTTP listener | `.env.example`, Compose |
| `HIGRESS_HTTPS_PORT` | Yes | Public or host-mapped HTTPS listener | `.env.example`, Compose |
| `HIGRESS_ADMIN_PORT` | Yes | Admin API listener | `.env.example`, Compose |
| `HIGRESS_BOOTSTRAP_CONFIG` | Internal deployment variable | Path to the gateway bootstrap YAML | Kubernetes Higress deployment |

### Upstream route integration variables

| Variable | Required | Purpose | Notes |
|----------|----------|---------|-------|
| `LITELLM_URL` | Yes for model routing | EchoThink LiteLLM base URL | Higress upstreams should point here |
| `LITELLM_API_KEY` | Usually yes | Credential used when the gateway or downstream clients authenticate to LiteLLM | Keep this out of Worker containers in the target production model |
| `GITLAB_URL` | Required for GitLab MCP publishing and route setup | GitLab base URL | Used today by GitLab MCP definitions |
| `GITLAB_TOKEN` or gateway-managed equivalent | Required for GitLab write/read operations | GitLab credential | Prefer gateway-owned secret management over Worker-owned copies |

### Per-worker consumer identity variables

These variables are not listed in the repository root `.env.example`, but they are referenced directly by the checked-in agent configs and are part of the operational identity model.

| Variable | Runtime |
|----------|---------|
| `HIGRESS_CONSUMER_TOKEN_MANAGER` | Manager |
| `HIGRESS_CONSUMER_TOKEN_PLANNER_WORKER` | Planner Worker |
| `HIGRESS_CONSUMER_TOKEN_WORKFLOW_WORKER` | Workflow Worker |
| `HIGRESS_CONSUMER_TOKEN_CODING_WORKER` | Coding Worker |
| `HIGRESS_CONSUMER_TOKEN_QA_WORKER` | QA Worker |
| `HIGRESS_CONSUMER_TOKEN_KNOWLEDGE_WORKER` | Knowledge Worker |

### Related variables that support the same trust boundary

| Variable | Why it matters |
|----------|----------------|
| `WORKER_JWT_SECRET` | Authenticates calls into internal bridge services |
| `MATRIX_HOMESERVER_URL` | Keeps Matrix interaction on the approved internal bus |
| `MINIO_ENDPOINT` | Anchors the shared-filesystem boundary on approved storage |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | Power shared-storage access until storage access is fully delegated through tighter identity controls |

### Minimum production recommendation

For a production EchoThink integration, treat the following as the minimum Higress contract:

1. set `DOMAIN`, `CLUSTER_NAME`, and the three listener ports
2. define per-worker consumer tokens
3. point gateway model routes at `LITELLM_URL`
4. keep upstream write credentials at the gateway or MCP layer wherever possible
5. enforce Worker egress so bypassing Higress is not possible in normal operation

That combination is what turns Higress from a nominal proxy into the actual security and policy boundary for ClawCluster.
