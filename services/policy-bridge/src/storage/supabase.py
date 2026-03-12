import json
from datetime import date
from typing import Any
from uuid import UUID

from models.policy import ApprovalRecord, ApprovalStatus, BudgetPolicySnapshot, BudgetScopeType


class SupabaseStorage:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @classmethod
    async def create(cls, dsn: str) -> "SupabaseStorage":
        import asyncpg

        pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)
        return cls(pool=pool)

    async def close(self) -> None:
        await self._pool.close()

    async def create_approval_request(
        self,
        *,
        work_item_id: str,
        task_run_id: UUID | None,
        gate_name: str,
        requested_from: str,
        evidence_json: dict[str, Any],
        notes: str | None,
    ) -> ApprovalRecord:
        query = """
            INSERT INTO clawcluster.approvals (
                work_item_id,
                task_run_id,
                gate_name,
                requested_from,
                decision,
                evidence_json,
                notes
            )
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
            RETURNING *
        """
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                query,
                work_item_id,
                task_run_id,
                gate_name,
                requested_from,
                ApprovalStatus.PENDING.value,
                json.dumps(evidence_json),
                notes,
            )

        return self._approval_from_row(row)

    async def record_approval_decision(
        self,
        *,
        approval_id: UUID,
        decision: ApprovalStatus,
        decided_by: str,
        notes: str | None,
        evidence_json: dict[str, Any],
    ) -> ApprovalRecord | None:
        query = """
            UPDATE clawcluster.approvals
            SET decision = $2,
                decided_by = $3,
                decided_at = now(),
                notes = COALESCE($4, notes),
                evidence_json = COALESCE(evidence_json, '{}'::jsonb) || $5::jsonb
            WHERE id = $1
            RETURNING *
        """
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                query,
                approval_id,
                decision.value,
                decided_by,
                notes,
                json.dumps(evidence_json),
            )

        if row is None:
            return None
        return self._approval_from_row(row)

    async def list_pending_approvals(
        self,
        *,
        work_item_id: str | None = None,
        limit: int = 50,
    ) -> list[ApprovalRecord]:
        query = """
            SELECT *
            FROM clawcluster.approvals
            WHERE decision = 'pending'
              AND ($1::text IS NULL OR work_item_id = $1)
            ORDER BY created_at ASC
            LIMIT $2
        """
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(query, work_item_id, limit)

        return [self._approval_from_row(row) for row in rows]

    async def fetch_budget_policy(
        self,
        *,
        scope_type: BudgetScopeType,
        scope_id: str,
    ) -> BudgetPolicySnapshot | None:
        query = """
            SELECT scope_type, scope_id, daily_cost_limit_usd, per_task_cost_limit_usd,
                   token_limit_per_task, concurrency_limit, enabled
            FROM clawcluster.budget_policies
            WHERE enabled = true
              AND scope_type = $1
              AND scope_id = $2
        """
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(query, scope_type.value, scope_id)

        if row is None:
            return None

        return BudgetPolicySnapshot.model_validate(dict(row))

    async def get_daily_spend(
        self,
        *,
        scope_type: BudgetScopeType,
        scope_id: str,
        on_date: date,
    ) -> float:
        query = """
            SELECT COALESCE(SUM(tr.cost_usd), 0)::float8 AS total_spend
            FROM clawcluster.task_runs AS tr
            JOIN clawcluster.work_items AS wi ON wi.id = tr.work_item_id
            WHERE tr.created_at >= $3::date
              AND tr.created_at < ($3::date + INTERVAL '1 day')
              AND (
                    $1 = 'global'
                 OR ($1 = 'workspace' AND wi.workspace_id = $2)
                 OR ($1 = 'agent_profile' AND tr.agent_profile_id::text = $2)
                 OR ($1 = 'work_item_kind' AND wi.kind = $2)
              )
        """
        async with self._pool.acquire() as connection:
            total_spend = await connection.fetchval(query, scope_type.value, scope_id, on_date)
        return float(total_spend or 0.0)

    async def get_active_task_count(self, *, scope_type: BudgetScopeType, scope_id: str) -> int:
        query = """
            SELECT COUNT(*)::int AS active_task_count
            FROM clawcluster.task_runs AS tr
            JOIN clawcluster.work_items AS wi ON wi.id = tr.work_item_id
            WHERE tr.status IN ('pending', 'running')
              AND (
                    $1 = 'global'
                 OR ($1 = 'workspace' AND wi.workspace_id = $2)
                 OR ($1 = 'agent_profile' AND tr.agent_profile_id::text = $2)
                 OR ($1 = 'work_item_kind' AND wi.kind = $2)
              )
        """
        async with self._pool.acquire() as connection:
            count = await connection.fetchval(query, scope_type.value, scope_id)
        return int(count or 0)

    def _approval_from_row(self, row: Any) -> ApprovalRecord:
        payload = dict(row)
        payload["evidence_json"] = payload.get("evidence_json") or {}
        return ApprovalRecord.model_validate(payload)
