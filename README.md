# EchoThink ClawCluster

EchoThink ClawCluster is the agent workforce and execution control plane for EchoThink. It runs HiClaw-style manager and worker roles on top of OpenClaw, adds EchoThink integration bridges, and exposes the stack through a dedicated edge layer for local, single-node, and future Kubernetes deployments.

## Architecture

```text
                           ┌─────────────────────────────┐
                           │        External Users        │
                           └──────────────┬──────────────┘
                                          │
                                   80 / 443 via Nginx
                                          │
                              ┌───────────▼───────────┐
                              │      Higress Edge      │
                              └───────┬─────┬─────────┘
                                      │     │
                    ┌─────────────────┘     └──────────────────┐
                    │                                            │
          ┌─────────▼─────────┐                      ┌───────────▼──────────┐
          │  Tuwunel Matrix   │◄──── Element Web ───►│  Human Collaboration  │
          └───────────────────┘                      └───────────────────────┘
                    │
                    │                      ┌──────────────────────────────────┐
                    └─────────────────────►│        HiClaw Manager            │
                                           │        (OpenClaw runtime)        │
                                           └───────┬─────────┬─────────┬──────┘
                                                   │         │         │
                        ┌──────────────────────────┘         │         └──────────────────────────┐
                        │                                    │                                    │
              ┌─────────▼─────────┐                ┌─────────▼─────────┐                ┌─────────▼─────────┐
              │  Planner Worker   │                │ Workflow Worker   │                │  Coding Worker    │
              └───────────────────┘                └───────────────────┘                └───────────────────┘
                        │                                    │                                    │
              ┌─────────▼─────────┐                ┌─────────▼─────────┐                ┌─────────▼─────────┐
              │    QA Worker      │                │ Knowledge Worker  │                │ Shared FS / MinIO │
              └───────────────────┘                └───────────────────┘                └───────────────────┘
                                                   │
                               ┌───────────────────▼───────────────────┐
                               │ Integration / Policy / Publish / Obs │
                               │        Bridge Services Layer          │
                               └───────────────────┬───────────────────┘
                                                   │
        ┌──────────────────────────────────────────┼───────────────────────────────────────────┐
        │                                          │                                           │
┌───────▼────────┐ ┌────────────▼──────────┐ ┌─────▼──────┐ ┌────────▼────────┐ ┌────────────▼──────────┐
│    Outline     │ │       GitLab          │ │ Supabase   │ │    Hatchet      │ │ Graphiti / LiteLLM / │
│                 │ │                       │ │ / Postgres │ │                 │ │ Langfuse / Dify / n8n│
└────────────────┘ └───────────────────────┘ └────────────┘ └─────────────────┘ └───────────────────────┘
```

## Prerequisites

- Docker Engine 24+ with Docker Compose v2
- GNU Make 4+
- At least 4 CPU cores and 8 GB RAM for local integration testing
- Reachability from this stack to the existing EchoThink infrastructure endpoints
- A completed `.env` file derived from `.env.example`

## Quick Start

1. Copy the environment template and fill in all URLs, secrets, and tokens.
   ```bash
   cp .env.example .env
   ```
2. Review `docker-compose.yml` and confirm that the bridge build contexts under `services/` match your implementation layout.
3. Validate the rendered configuration.
   ```bash
   docker compose --env-file .env config >/dev/null
   ```
4. Build local bridge images and start the stack.
   ```bash
   make build
   make up
   ```
5. Verify health and routing.
   ```bash
   make healthcheck
   ```

## Directory Layout

```text
.
├── CLAUDE.md
├── README.md
├── Makefile
├── .env.example
├── docker-compose.yml
├── docs/
├── k8s/
├── scripts/
└── services/
```

## Docs

- Operator conventions: `CLAUDE.md`
- Runtime configuration contract: `.env.example`
- Local deployment topology: `docker-compose.yml`
- Common day-2 operations: `Makefile`

## Notes

- This repository is intentionally separate from `echothink-infra`; it consumes that platform as an upstream dependency rather than replacing it.
- `WORKER_REPLICAS` documents the intended worker fan-out. In plain Docker Compose, scale individual worker roles with `docker compose up --scale planner-worker=<n>` when needed.
- The four bridge services are locally built from `services/` so that EchoThink-specific policies and integrations stay versioned with the control plane.
