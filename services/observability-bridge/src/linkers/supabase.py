from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from models.events import TaskCompleteEvent, TaskRunState, TaskRunStatus, TraceMetrics


class SupabaseTaskRunStore:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @classmethod
    async def create(cls, dsn: str) -> "SupabaseTaskRunStore":
        import asyncpg

        pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)
        return cls(pool=pool)

    async def close(self) -> None:
        await self._pool.close()

    async def update_trace_link(self, *, task_run_id: UUID, trace_id: str) -> TaskRunState | None:
        query = """
            UPDATE clawcluster.task_runs
            SET langfuse_trace_id = $2
            WHERE id = $1
            RETURNING id, work_item_id, status, langfuse_trace_id, cost_usd, token_count, ended_at, result_summary, error_message
        """
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(query, task_run_id, trace_id)

        return self._task_run_from_row(row) if row is not None else None

    async def get_trace_id(self, *, task_run_id: UUID) -> str | None:
        query = "SELECT langfuse_trace_id FROM clawcluster.task_runs WHERE id = $1"
        async with self._pool.acquire() as connection:
            trace_id = await connection.fetchval(query, task_run_id)
        return str(trace_id) if trace_id else None

    async def update_trace_metrics(
        self,
        *,
        task_run_id: UUID,
        metrics: TraceMetrics,
    ) -> TaskRunState | None:
        query = """
            UPDATE clawcluster.task_runs
            SET langfuse_trace_id = COALESCE($2, langfuse_trace_id),
                cost_usd = COALESCE($3, cost_usd),
                token_count = COALESCE($4, token_count)
            WHERE id = $1
            RETURNING id, work_item_id, status, langfuse_trace_id, cost_usd, token_count, ended_at, result_summary, error_message
        """
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                query,
                task_run_id,
                metrics.trace_id,
                metrics.cost_usd,
                metrics.token_count,
            )

        return self._task_run_from_row(row) if row is not None else None

    async def complete_task_run(self, event: TaskCompleteEvent) -> TaskRunState | None:
        ended_at = event.completed_at or datetime.now(tz=UTC)
        query = """
            UPDATE clawcluster.task_runs
            SET status = $2,
                ended_at = $3,
                result_summary = COALESCE($4, result_summary),
                error_message = COALESCE($5, error_message),
                langfuse_trace_id = COALESCE($6, langfuse_trace_id)
            WHERE id = $1
            RETURNING id, work_item_id, status, langfuse_trace_id, cost_usd, token_count, ended_at, result_summary, error_message
        """
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                query,
                event.task_run_id,
                event.status.value,
                ended_at,
                event.result_summary,
                event.error_message,
                event.trace_id,
            )

        return self._task_run_from_row(row) if row is not None else None

    def _task_run_from_row(self, row: Any) -> TaskRunState:
        payload = dict(row)
        return TaskRunState(
            task_run_id=payload["id"],
            work_item_id=payload.get("work_item_id"),
            status=TaskRunStatus(payload["status"]) if payload.get("status") else None,
            trace_id=payload.get("langfuse_trace_id"),
            cost_usd=float(payload["cost_usd"]) if payload.get("cost_usd") is not None else None,
            token_count=payload.get("token_count"),
            ended_at=payload.get("ended_at"),
            result_summary=payload.get("result_summary"),
            error_message=payload.get("error_message"),
        )
