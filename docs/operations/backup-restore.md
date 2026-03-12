# Backup and Restore Guide

This document covers backup strategies, restore procedures, and disaster recovery for the stateful parts of EchoThink ClawCluster.

## Table of Contents

- [Backup Overview](#backup-overview)
- [Tuwunel Matrix Data](#tuwunel-matrix-data)
- [MinIO Shared Storage](#minio-shared-storage)
- [Supabase `clawcluster` Schema](#supabase-clawcluster-schema)
- [Higress Gateway Config](#higress-gateway-config)
- [Full Automated Backup](#full-automated-backup)
- [Disaster Recovery Runbook](#disaster-recovery-runbook)
- [Backup Verification](#backup-verification)

---

## Backup Overview

| Component | Data at Risk | Backup Method | Frequency | Retention |
|-----------|--------------|---------------|-----------|-----------|
| Tuwunel Matrix local state | Room history context, user sessions, local media/cache, homeserver state under `clawcluster_tuwunel_data` | Docker volume archive (`tar` via `alpine`, same approach as `scripts/backup.sh`) | Daily and before upgrades | 14 days |
| MinIO shared storage | `hiclaw-storage/agents/...`, `hiclaw-storage/shared/tasks/...`, generated artifacts | `mc mirror` of the shared storage prefix in `MINIO_HICLAW_BUCKET` | Daily | 14 days |
| Supabase `clawcluster` schema | Structured work items, task runs, approvals, artifacts, worker refs, external refs, budget policies | `pg_dump --schema=clawcluster` against the Supabase PostgreSQL database | Every 6 hours | 30 days |
| Higress gateway state | Route policies, consumer definitions, MCP exposure/bootstrap config | Docker volume archive for `/var/lib/higress` and ConfigMap export in Kubernetes | Daily and before config changes | 14 days |

All backups should be stored on a separate disk, object store, or remote rsync/NFS target. Never keep the only backup copy on the same host that runs ClawCluster.

Two implementation details matter for production operations:

- `scripts/backup.sh` is a Docker Compose volume backup script. It captures local Docker volumes and optionally `.env`; it does **not** back up the external EchoThink MinIO data or the Supabase PostgreSQL schema.
- Tuwunel is configured with `TUWUNEL_DB_URL`, so a complete Matrix recovery may also require restoring the PostgreSQL database behind that DSN. The procedures below cover the ClawCluster-managed Tuwunel volume and call out the database dependency explicitly.

---

## Tuwunel Matrix Data

Tuwunel is the ClawCluster communication bus. It carries Manager-to-Worker coordination, human interventions, escalation messages, and the room history that makes agent work visible and auditable.

### What is at risk

If Tuwunel state is lost, the following information may be unrecoverable or expensive to reconstruct:

- room history for active and completed work items;
- user accounts, service accounts, and room membership state;
- encryption-related material and session keys, if encrypted rooms are used;
- local homeserver state under `/data` such as media, caches, and runtime state.

Operationally, treat Matrix data as evidence. Even when an artifact is republished into Outline or GitLab, the Matrix room usually retains the step-by-step operational context that explains why a decision was made.

### Manual backup using the `backup.sh` volume approach

The local Compose deployment stores Tuwunel data in the `clawcluster_tuwunel_data` Docker volume. `scripts/backup.sh` backs it up by mounting that volume into a temporary `alpine` container and streaming a tarball.

To back up only the Tuwunel volume with the same technique:

```bash
mkdir -p ./backups/tuwunel

docker run --rm \
  -v clawcluster_tuwunel_data:/data \
  alpine \
  tar czf - /data > ./backups/tuwunel/clawcluster_tuwunel_data.tar.gz
```

To create the normal multi-volume archive, use the project script:

```bash
# Backup all ClawCluster runtime volumes to ./backups
./scripts/backup.sh

# Backup all volumes to a dedicated mount
./scripts/backup.sh /mnt/backups/clawcluster
```

If you need the environment file as part of an encrypted off-host backup set:

```bash
./scripts/backup.sh --include-secrets /mnt/backups/clawcluster
```

Only use `--include-secrets` when the target location is encrypted and access-controlled.

### Why losing room history matters

Matrix room history is not just chat.

It is the operational audit trail for ClawCluster:

- the Manager’s delegation decisions live there;
- Worker escalations and blocked-state explanations live there;
- human interventions and clarifications live there;
- incident notes often appear there before they are formalized elsewhere.

If room history disappears, operators lose the most direct record of why an approval was requested, why a task was re-routed, or why a risky action was paused. For regulated or high-accountability workflows, that is a meaningful incident in its own right.

### Restore procedure

To restore only the Tuwunel volume:

```bash
docker compose stop tuwunel element-web nginx

docker volume create clawcluster_tuwunel_data

docker run --rm -i \
  -v clawcluster_tuwunel_data:/data \
  alpine \
  sh -c 'rm -rf /data/* /data/.[!.]* /data/..?* 2>/dev/null || true; tar xzf - -C /' \
  < ./backups/tuwunel/clawcluster_tuwunel_data.tar.gz

docker compose up -d tuwunel element-web nginx
```

If the backup was created with `./scripts/backup.sh`, the full archive restore flow is:

```bash
./scripts/backup.sh --restore ./backups/clawcluster_backup_20260313_120000.tar.gz
```

After the local volume restore, confirm whether the database referenced by `TUWUNEL_DB_URL` also needs restoration. If Tuwunel’s PostgreSQL database was lost, volume restore alone is not sufficient for a full Matrix recovery.

---

## MinIO Shared Storage

ClawCluster uses the existing EchoThink MinIO deployment as its shared filesystem. In the current implementation, the bucket is configured through `MINIO_HICLAW_BUCKET` (default: `clawcluster-sharedfs`) and the ClawCluster object layout lives under the `hiclaw-storage/` prefix.

### What is stored

| Path | Typical contents | Why it matters |
|------|------------------|----------------|
| `hiclaw-storage/agents/<agent>/` | Agent configuration such as `SOUL.md` and `openclaw.json` | Recreates runtime identity and tool layout for Manager and Workers |
| `hiclaw-storage/shared/tasks/task-*/spec.md` | Normalized task specs written by intake flows | Allows Workers to resume or re-read work context |
| `hiclaw-storage/shared/tasks/task-*/result.*` | Drafts, workflow JSON, result documents, generated evidence | Required for publication, review, and replay |
| `hiclaw-storage/shared/tasks/task-*/artifacts/` | Supporting artifacts and intermediate files | Preserves the evidence chain for completed work |
| `hiclaw-storage/shared/knowledge/` | Durable knowledge and future shared context | Supports long-lived operational memory |

The bootstrap script `scripts/setup-minio-buckets.sh` ensures the bucket exists, applies a private policy, and creates placeholder objects under:

- `hiclaw-storage/agents/.keep`
- `hiclaw-storage/shared/tasks/.keep`

### Backup using `mc mirror`

From a host with the MinIO client installed:

```bash
# Configure a host-side alias to the EchoThink MinIO endpoint
mc alias set clawcluster ${MINIO_ENDPOINT} ${MINIO_ACCESS_KEY} ${MINIO_SECRET_KEY}

# Mirror only the ClawCluster shared-storage prefix
mc mirror \
  clawcluster/${MINIO_HICLAW_BUCKET}/hiclaw-storage/ \
  ./backups/minio/hiclaw-storage/
```

To send the backup to remote S3-compatible storage instead of local disk:

```bash
mc alias set backup https://backup-s3.example.com BACKUP_KEY BACKUP_SECRET
mc mirror \
  clawcluster/${MINIO_HICLAW_BUCKET}/hiclaw-storage/ \
  backup/clawcluster/hiclaw-storage/
```

If you prefer time-bounded copies, run the mirror on a schedule and retain dated directories rather than using `--remove`.

### Restore procedure

If the bucket or prefix is missing, bootstrap it first:

```bash
./scripts/setup-minio-buckets.sh
```

Then mirror the backup back into the ClawCluster prefix:

```bash
mc alias set clawcluster ${MINIO_ENDPOINT} ${MINIO_ACCESS_KEY} ${MINIO_SECRET_KEY}

mc mirror \
  ./backups/minio/hiclaw-storage/ \
  clawcluster/${MINIO_HICLAW_BUCKET}/hiclaw-storage/
```

After restore, verify at least one agent config path and one task path:

```bash
mc ls clawcluster/${MINIO_HICLAW_BUCKET}/hiclaw-storage/agents/
mc ls clawcluster/${MINIO_HICLAW_BUCKET}/hiclaw-storage/shared/tasks/
```

---

## Supabase `clawcluster` Schema

EchoThink remains the system of record for structured work and approval state. For ClawCluster, that structured state lives in the `clawcluster` schema inside the Supabase-backed PostgreSQL database.

### Tables at risk

The minimum production backup set should include these tables:

- `work_items`
- `task_runs`
- `approvals`
- `artifacts`
- `hiclaw_workers`
- `external_refs`
- `budget_policies`

The design also defines `agent_profiles`, `skill_definitions`, and `agent_skill_bindings`; include them if they are populated in the environment you are backing up.

### Backup with `pg_dump`

Use a PostgreSQL DSN for the Supabase database. Do **not** point `pg_dump` at the HTTP `SUPABASE_URL`; use a direct Postgres connection string such as `SUPABASE_DB_DSN`.

```bash
export SUPABASE_DB_DSN='postgresql://postgres:replace-me@postgres.echothink.internal:5432/supabase'
mkdir -p ./backups/supabase

pg_dump \
  --dbname="$SUPABASE_DB_DSN" \
  --format=custom \
  --schema=clawcluster \
  --file=./backups/supabase/clawcluster_$(date +%Y%m%d_%H%M%S).dump
```

For a quick validity check without restoring:

```bash
pg_restore --list ./backups/supabase/clawcluster_20260313_120000.dump > /dev/null
```

### Restore procedure

Before restoring, stop services that write to the ClawCluster schema:

```bash
docker compose stop hiclaw-manager intake-bridge publisher-bridge policy-bridge observability-bridge
```

Then recreate the schema and restore the dump:

```bash
export SUPABASE_DB_DSN='postgresql://postgres:replace-me@postgres.echothink.internal:5432/supabase'

psql "$SUPABASE_DB_DSN" -c 'DROP SCHEMA IF EXISTS clawcluster CASCADE;'
psql "$SUPABASE_DB_DSN" -c 'CREATE SCHEMA clawcluster;'

pg_restore \
  --dbname="$SUPABASE_DB_DSN" \
  --schema=clawcluster \
  --clean \
  --if-exists \
  --no-owner \
  --no-privileges \
  ./backups/supabase/clawcluster_20260313_120000.dump
```

After restore, run a quick integrity check:

```bash
psql "$SUPABASE_DB_DSN" -c 'SELECT COUNT(*) AS work_items FROM clawcluster.work_items;'
psql "$SUPABASE_DB_DSN" -c 'SELECT COUNT(*) AS approvals FROM clawcluster.approvals;'
```

Bring the writers back only after the schema is consistent:

```bash
docker compose up -d hiclaw-manager intake-bridge publisher-bridge policy-bridge observability-bridge
```

---

## Higress Gateway Config

Higress is the ClawCluster gateway for LLM and MCP traffic. It is where per-Worker routing, consumer identity, and gateway bootstrap configuration converge.

### What is in the config volume

Treat the following as recoverable Higress state:

- route policies used to separate Manager and Worker traffic;
- consumer definitions and tokens used for Worker identity;
- MCP exposure and upstream gateway mappings;
- bootstrap listener configuration (`gateway.yaml`) in Kubernetes ConfigMaps;
- runtime gateway data stored in `/var/lib/higress` for Compose deployments.

In Docker Compose, Higress persists local state in the `clawcluster_higress_data` volume mounted at `/var/lib/higress`.

In Kubernetes, the bootstrap file is mounted from a ConfigMap (`higress-config` in kustomize, `<release>-higress` in Helm). Helm defaults to `higress.storage.enabled=false`, which means the data directory is `emptyDir` unless you explicitly enable persistent storage.

### Backup via Docker volume

```bash
mkdir -p ./backups/higress

docker run --rm \
  -v clawcluster_higress_data:/data \
  alpine \
  tar czf - /data > ./backups/higress/clawcluster_higress_data.tar.gz
```

### Backup via ConfigMap export

For a kustomize deployment:

```bash
kubectl -n clawcluster get configmap higress-config -o yaml > ./backups/higress/higress-configmap.yaml
```

For a Helm deployment, export the rendered ConfigMap name for the release in use:

```bash
kubectl -n clawcluster get configmap <release>-higress -o yaml > ./backups/higress/higress-configmap.yaml
```

### Restore procedure

For Docker Compose volume restore:

```bash
docker compose stop higress nginx

docker volume create clawcluster_higress_data

docker run --rm -i \
  -v clawcluster_higress_data:/data \
  alpine \
  sh -c 'rm -rf /data/* /data/.[!.]* /data/..?* 2>/dev/null || true; tar xzf - -C /' \
  < ./backups/higress/clawcluster_higress_data.tar.gz

docker compose up -d higress nginx
```

For Kubernetes ConfigMap restore:

```bash
kubectl apply -f ./backups/higress/higress-configmap.yaml
kubectl -n clawcluster rollout restart deployment/higress
```

If you expect Higress runtime data to survive pod recreation in Kubernetes, enable persistent storage before the next incident.

---

## Full Automated Backup

`scripts/backup.sh` is the project’s local runtime backup script. It creates a timestamped archive named `clawcluster_backup_<timestamp>.tar.gz` and can restore that archive back into the expected Docker volumes.

### What the script captures

The script currently backs up these Docker volumes:

- `clawcluster_higress_data`
- `clawcluster_tuwunel_data`
- `clawcluster_hiclaw_manager_data`
- `clawcluster_planner_worker_data`
- `clawcluster_workflow_worker_data`
- `clawcluster_coding_worker_data`
- `clawcluster_qa_worker_data`
- `clawcluster_knowledge_worker_data`

It can also include `.env` when `--include-secrets` is provided.

It does **not** back up:

- the external EchoThink MinIO shared-storage data;
- the Supabase PostgreSQL `clawcluster` schema;
- the PostgreSQL database behind `TUWUNEL_DB_URL`;
- bridge service volumes, which are treated as disposable runtime state.

### Usage examples

```bash
# Full local volume backup into ./backups
./scripts/backup.sh

# Full local volume backup into a dedicated mount
./scripts/backup.sh /mnt/backups/clawcluster

# Include .env in the archive (only for encrypted backup targets)
./scripts/backup.sh --include-secrets /mnt/backups/clawcluster
```

### Restore mode

```bash
./scripts/backup.sh --restore ./backups/clawcluster_backup_20260313_120000.tar.gz
```

The restore flow validates the archive, extracts it into a temporary directory, recreates the known volumes, and restores `.env` if the archive contains one.

### Recommended cron schedule

Add the following to the host crontab (`crontab -e`) and adjust paths for your environment:

```cron
# Local ClawCluster runtime volume backup every night at 01:00
0 1 * * * cd /srv/echothink-clawcluster && ./scripts/backup.sh /backups/clawcluster/volumes >> /var/log/clawcluster-backup.log 2>&1

# Supabase clawcluster schema backup every 6 hours
15 */6 * * * export SUPABASE_DB_DSN='postgresql://postgres:replace-me@postgres.echothink.internal:5432/supabase'; pg_dump --dbname="$SUPABASE_DB_DSN" --format=custom --schema=clawcluster --file=/backups/clawcluster/supabase/clawcluster_$(date +\%Y\%m\%d_\%H\%M\%S).dump >> /var/log/clawcluster-backup.log 2>&1

# MinIO shared storage mirror every night at 01:30
30 1 * * * cd /srv/echothink-clawcluster && set -a && . ./.env && set +a && mc alias set clawcluster "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null 2>&1 && mc mirror clawcluster/${MINIO_HICLAW_BUCKET}/hiclaw-storage/ /backups/clawcluster/minio/hiclaw-storage/ >> /var/log/clawcluster-backup.log 2>&1

# Cleanup volume archives older than 14 days
0 3 * * * find /backups/clawcluster/volumes -name 'clawcluster_backup_*.tar.gz' -mtime +14 -delete
```

---

## Disaster Recovery Runbook

### Scenario: Complete host failure

**Recovery time objective (RTO):** 2 hours
**Recovery point objective (RPO):** Depends on schedule; with the sample cron above, up to 6 hours for Supabase schema state and up to 24 hours for local volumes and MinIO shared-storage content.

#### Steps:

1. **Provision a new host** with Docker, Docker Compose, `mc`, `pg_dump`, `pg_restore`, and network access to EchoThink MinIO and PostgreSQL.

2. **Clone the repository** and place it at the expected path on the new host.

3. **Restore the environment and secrets** by recovering `.env`, CA material, and any operator credentials from secure storage. If `.env` was included in an encrypted `backup.sh` archive, restore it to the project root now, then load it into the shell:
   ```bash
   set -a && . ./.env && set +a
   ```

4. **Copy the latest backup set** to the new host. A complete ClawCluster recovery set should include:
   - the latest `clawcluster_backup_<timestamp>.tar.gz` volume archive;
   - the latest MinIO `hiclaw-storage/` mirror;
   - the latest Supabase `clawcluster` schema dump;
   - any exported Higress ConfigMap YAML, if Kubernetes is used.

5. **Restore local Docker volumes while the stack is still down:**
   ```bash
   ./scripts/backup.sh --restore /backups/clawcluster/volumes/clawcluster_backup_20260313_120000.tar.gz
   ```

6. **Restore MinIO shared storage** and recreate the bucket if necessary:
   ```bash
   mc alias set clawcluster ${MINIO_ENDPOINT} ${MINIO_ACCESS_KEY} ${MINIO_SECRET_KEY}
   ./scripts/setup-minio-buckets.sh
   mc mirror /backups/clawcluster/minio/hiclaw-storage/ clawcluster/${MINIO_HICLAW_BUCKET}/hiclaw-storage/
   ```

7. **Restore the Supabase `clawcluster` schema** using the latest `pg_dump` archive. If Tuwunel uses a dedicated PostgreSQL database through `TUWUNEL_DB_URL`, restore that database through the owning PostgreSQL operations process as well.

8. **Bring up Tuwunel first** so the communication bus is available before the control plane reconnects:
   ```bash
   docker compose up -d tuwunel
   curl -fsS http://127.0.0.1:6167/_matrix/client/versions > /dev/null
   ```

9. **Bring up Higress second** so Manager and Workers have their gateway path available:
   ```bash
   docker compose up -d higress
   curl -fsS http://127.0.0.1:${HIGRESS_HTTP_PORT:-8080}/health > /dev/null
   ```

10. **Bring up the HiClaw Manager** and confirm its health endpoint responds:
    ```bash
    docker compose up -d hiclaw-manager
    curl -fsS http://127.0.0.1:${HICLAW_MANAGER_PORT:-8088}/health > /dev/null
    ```

11. **Bring up the bridge services** in this order: `intake-bridge`, `publisher-bridge`, `policy-bridge`, `observability-bridge`.
    ```bash
    docker compose up -d intake-bridge publisher-bridge policy-bridge observability-bridge
    ```

12. **Bring up Worker containers last** so they only start after Matrix, gateway, manager, and policy flows are available:
    ```bash
    docker compose up -d planner-worker workflow-worker coding-worker qa-worker knowledge-worker
    ```

13. **Bring up the remaining UI and edge services** (`element-web` and `nginx`) and confirm external entry points are back:
    ```bash
    docker compose up -d element-web nginx
    ```

14. **Run the health check script** and do a manual spot check of one known work item, one Matrix room, one MinIO task prefix, and one Supabase approval record:
    ```bash
    ./scripts/healthcheck.sh
    ```

15. **Update DNS or private ingress records** if the replacement host uses a new address.

The intended recovery order is:

1. `tuwunel`
2. `higress`
3. `hiclaw-manager`
4. bridge services
5. Worker containers
6. `element-web` and edge ingress

That order ensures the team communication plane and gateway are available before task processing resumes.

---

## Backup Verification

Backups are only useful if they can be restored. Verify backups regularly.

### Weekly verification checklist

1. **Pick a random local volume archive** from the last 7 days and verify it is readable:
   ```bash
   tar -tzf /backups/clawcluster/volumes/clawcluster_backup_YYYYMMDD_HHMMSS.tar.gz > /dev/null
   ```

2. **Inspect the embedded manifest** in the archive and confirm the expected volumes were captured.

3. **Test a scratch restore of one local volume** such as `clawcluster_tuwunel_data` or `clawcluster_higress_data` on a non-production host.

4. **Verify the Supabase schema dump** can be listed by `pg_restore`:
   ```bash
   pg_restore --list /backups/clawcluster/supabase/clawcluster_YYYYMMDD_HHMMSS.dump > /dev/null
   ```

5. **Spot-check MinIO shared storage** by confirming at least one object exists under both:
   - `hiclaw-storage/agents/`
   - `hiclaw-storage/shared/tasks/`

6. **Verify Higress recovery artifacts** by confirming the latest Docker volume archive exists and the latest ConfigMap export can be applied cleanly on a staging cluster.

7. **Run a monthly full restore rehearsal** on a staging host and confirm `./scripts/healthcheck.sh` passes after recovery.

### Automated verification

Add a lightweight weekly verification job to cron:

```cron
0 5 * * 0 tar -tzf $(ls -1t /backups/clawcluster/volumes/clawcluster_backup_*.tar.gz | head -1) > /dev/null && pg_restore --list $(ls -1t /backups/clawcluster/supabase/clawcluster_*.dump | head -1) > /dev/null
```
