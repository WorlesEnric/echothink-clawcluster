# CLAUDE.md -- EchoThink ClawCluster

## Project Overview

EchoThink ClawCluster is the agent workforce and execution control plane for EchoThink, a self-hosted human-AI collaboration platform. It combines a HiClaw-style manager-plus-workers topology with the OpenClaw runtime, then layers in EchoThink-specific integration bridges, approval policy enforcement, publication flows, and observability hooks.

This repository is the operational source of truth for the ClawCluster deployment surface:

- local and single-node Docker Compose deployments
- future Kubernetes manifests and overlays for the dedicated ClawCluster cluster
- bridge service build contexts and runtime contracts
- operational runbooks, verification steps, and environment conventions

ClawCluster is deployed as a separate Kubernetes cluster from the main EchoThink infrastructure stack, but it is tightly integrated with the existing EchoThink services such as Outline, GitLab, Supabase/Postgres, Hatchet, Graphiti, LiteLLM, Langfuse, MinIO, Dify, and n8n.

## Directory Structure

```text
echothink-clawcluster/
├── CLAUDE.md                          # This file -- project conventions for Codex/Claude
├── README.md                          # Repository overview and operator quick start
├── Makefile                           # Common operational commands
├── .env.example                       # Complete environment variable contract
├── docker-compose.yml                 # Local and single-node deployment baseline
├── docs/
│   ├── architecture.md                # End-to-end control-plane architecture
│   ├── integrations/
│   │   ├── echothink-services.md      # Bridge contracts for external EchoThink systems
│   │   └── worker-profiles.md         # Role-specific worker capabilities and policies
│   └── operations/
│       ├── backup-restore.md          # Backup and disaster recovery procedures
│       ├── runbooks.md                # Incident response and operator runbooks
│       └── verification.md            # Post-deploy verification checklist
├── k8s/
│   ├── base/
│   │   ├── gateway/                   # Higress and edge routing manifests
│   │   ├── matrix/                    # Tuwunel + Element manifests
│   │   ├── manager/                   # HiClaw manager manifests
│   │   ├── workers/                   # Worker role Deployments and supporting config
│   │   └── bridges/                   # Integration bridge manifests
│   └── overlays/
│       ├── dev/                       # Developer-friendly defaults
│       ├── staging/                   # Pre-production soak configuration
│       └── production/                # Production replicas, policies, and ingress
├── scripts/
│   ├── bootstrap.sh                   # First-run workstation or host setup
│   ├── backup.sh                      # Backup orchestration helper
│   └── healthcheck.sh                 # Repository-level verification helper
└── services/
    ├── intake-bridge/                 # Intake bridge build context and app code
    ├── publisher-bridge/              # Publication and export bridge build context
    ├── policy-bridge/                 # Approval and policy enforcement bridge
    ├── observability-bridge/          # Tracing, metrics, and event export bridge
    ├── nginx/
    │   ├── conf.d/                    # Optional Nginx overrides and local snippets
    │   └── ssl/                       # Local TLS material for edge testing
    └── shared/
        ├── templates/                 # Shared config, prompt, or payload templates
        └── workers/                   # Worker bootstrap assets and common tooling
```

## How to Add a New Service

1. **Create the service directory** under `services/<service-name>/` if the service is locally built. Add its Dockerfile, runtime config, and README as needed.
2. **Add the Compose service** in `docker-compose.yml`. Attach it to `clawcluster_net`, apply `restart: unless-stopped`, define a healthcheck, and add a named volume following the naming pattern below.
3. **Add environment variables** to `.env.example`. Every configurable port, URL, secret, and feature flag must be declared there with a descriptive comment.
4. **Expose the service through the edge** if it needs north-south traffic. Update the Nginx routing block and, if appropriate, add Higress route policy configuration.
5. **Document the service contract** in `README.md` and the relevant file under `docs/` if it changes operational flows, external integrations, or backup expectations.
6. **Add Kubernetes manifests** under `k8s/base/` and overlay-specific changes under `k8s/overlays/` once the Compose baseline is proven.
7. **Update operator automation** by extending the `Makefile`, `scripts/healthcheck.sh`, or `scripts/backup.sh` if the service introduces a new verification or backup requirement.

## Naming Conventions

### Docker Compose Services

- Use lowercase, hyphenated service names: `hiclaw-manager`, `planner-worker`, `policy-bridge`
- Worker roles must end with `-worker`
- Bridge services must end with `-bridge`
- The Compose project name is `clawcluster`, which yields container names such as `clawcluster-hiclaw-manager-1`

### Environment Variables

- Use SCREAMING_SNAKE_CASE
- Prefix service-scoped variables with the service domain name:
  - `HIGRESS_*`
  - `TUWUNEL_*`
  - `ELEMENT_WEB_*`
  - `HICLAW_*`
  - `MINIO_*`
- Use `_URL` suffixes for service endpoints and `_API_KEY`, `_SECRET_KEY`, `_TOKEN`, or `_SECRET` for credentials
- Use `PORT` variables only for listener ports that are expected to be operator-configurable

### Named Volumes

- Pattern: `clawcluster_<service>_data`
- Examples:
  - `clawcluster_higress_data`
  - `clawcluster_tuwunel_data`
  - `clawcluster_hiclaw_manager_data`
  - `clawcluster_policy_bridge_data`

### Networks

- All services join the shared bridge network `clawcluster_net`
- Services must use the Docker Compose service name for east-west traffic, for example `http://hiclaw-manager:${HICLAW_MANAGER_PORT}`

## Docker Compose Conventions

### Base Expectations

- Every long-running service must set `restart: unless-stopped`
- Every service must define a `healthcheck`
- Service configuration must use `${VARIABLE}` interpolation from `.env`
- Persistent services must mount a named volume
- Local build contexts live under `services/`

### Dependency Ordering

- `element-web` depends on a healthy `tuwunel`
- all worker services depend on a healthy `hiclaw-manager`
- bridge services depend on a healthy `hiclaw-manager`
- `nginx` depends on the public-facing services it proxies

### Healthchecks

- Prefer HTTP health endpoints when the service exposes one
- Use `30s` intervals, `10s` timeouts, `5` retries, and `30s` start periods unless a service needs longer warm-up
- Worker containers that do not expose HTTP must still validate manager wiring and mounted state directories

### Compose File Style

- Keep comments concise and operationally useful
- Prefer YAML anchors for repeated defaults such as logging and restart behavior
- Keep host port exposure explicit; bind admin/debug ports only where operators need them

## Common Operations

```bash
# Show all available commands
make help

# Start the full stack in detached mode
make up

# Stop the stack and remove orphaned containers
make down

# Tail logs for one service
make logs SERVICE=hiclaw-manager

# Restart one worker profile
make restart SERVICE=planner-worker

# Rebuild a bridge image after code changes
make build SERVICE=policy-bridge

# Run static linting and compose validation
make lint

# Run repository verification checks
make test

# Run live endpoint checks against a running stack
make healthcheck

# Run migrations for services that define migration entrypoints
make migrate

# Create a timestamped backup snapshot
make backup
```

## Key File Locations

| Purpose | Path |
| --- | --- |
| Operator overview | `README.md` |
| Project conventions | `CLAUDE.md` |
| Environment contract | `.env.example` |
| Local deployment baseline | `docker-compose.yml` |
| Service build contexts | `services/` |
| Edge overrides and TLS material | `services/nginx/` |
| Kubernetes manifests | `k8s/` |
| Operational documentation | `docs/operations/` |
| Integration contracts | `docs/integrations/` |
| Health check helper | `scripts/healthcheck.sh` |
| Backup helper | `scripts/backup.sh` |

## Testing and Verification

### After any infrastructure change

1. **Validate Compose syntax**
   ```bash
   docker compose --env-file .env.example config >/dev/null
   ```
2. **Run repository checks**
   ```bash
   make lint
   make test
   ```
3. **Start the stack and verify health**
   ```bash
   make up
   make healthcheck
   ```
4. **Verify edge routing**
   ```bash
   curl -fsS http://localhost/healthz
   curl -fsS http://localhost/_matrix/client/versions
   curl -fsS http://localhost/api/intake/health
   curl -fsS http://localhost/api/policy/health
   ```
5. **Verify manager reachability and worker registration**
   ```bash
   curl -fsS http://localhost:${HICLAW_MANAGER_PORT}/health
   docker compose ps planner-worker workflow-worker coding-worker qa-worker knowledge-worker
   ```
6. **Verify external integration wiring** by checking bridge logs for successful connections to MinIO, GitLab, Supabase, Langfuse, and the other EchoThink endpoints.

### Before merging a PR

- `docker-compose.yml` renders cleanly with `docker compose config`
- `.env.example` is updated for every new runtime variable
- No real credentials, tokens, or certificates are committed
- Healthcheck and backup workflows still reflect the current service topology
- Documentation is updated if ports, routes, or operator procedures changed
