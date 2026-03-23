from __future__ import annotations

import json

import asyncpg

from src.config import Settings
from src.models.work_item import WorkItem, WorkItemCreate


class SupabaseClient:
    def __init__(self, settings: Settings) -> None:
        self._dsn = settings.supabase_dsn
        self._connect_timeout_seconds = settings.connect_timeout_seconds
        self._command_timeout_seconds = settings.request_timeout_seconds
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                dsn=self._dsn,
                min_size=1,
                max_size=5,
                init=self._initialize_connection,
                timeout=self._connect_timeout_seconds,
                command_timeout=self._command_timeout_seconds,
                server_settings={"application_name": "intake-bridge"},
            )
        return self._pool

    async def _initialize_connection(self, connection: asyncpg.Connection) -> None:
        await connection.set_type_codec(
            "json",
            schema="pg_catalog",
            encoder=json.dumps,
            decoder=json.loads,
            format="text",
        )
        await connection.set_type_codec(
            "jsonb",
            schema="pg_catalog",
            encoder=json.dumps,
            decoder=json.loads,
            format="text",
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def ping(self) -> bool:
        pool = await self.connect()
        async with pool.acquire() as connection:
            result = await connection.fetchval("SELECT 1")
        return result == 1

    async def insert_work_item(self, work_item: WorkItemCreate) -> WorkItem:
        pool = await self.connect()
        query = """
            INSERT INTO clawcluster.work_items (
                id,
                workspace_id,
                kind,
                source_type,
                source_ref,
                objective,
                acceptance_criteria,
                constraints_json,
                priority,
                risk_level,
                approval_policy,
                requested_by
            )
            VALUES (
                $1,
                $2,
                $3,
                $4,
                $5,
                $6,
                $7::jsonb,
                $8::jsonb,
                $9,
                $10,
                $11,
                $12
            )
            RETURNING
                id,
                workspace_id,
                kind,
                source_type,
                source_ref,
                objective,
                acceptance_criteria,
                constraints_json,
                status,
                priority,
                risk_level,
                approval_policy,
                requested_by,
                created_at,
                updated_at
        """

        async with pool.acquire() as connection:
            record = await connection.fetchrow(
                query,
                work_item.id,
                work_item.workspace_id,
                work_item.kind.value,
                work_item.source_type.value,
                work_item.source_ref,
                work_item.objective,
                work_item.acceptance_criteria,
                work_item.constraints_json,
                work_item.priority,
                work_item.risk_level.value,
                work_item.approval_policy.value,
                work_item.requested_by,
            )

        if record is None:
            raise RuntimeError("Supabase insert did not return a work item record")

        return WorkItem.model_validate(dict(record))

    async def update_work_item_status(self, work_item_id: str, status: str) -> None:
        pool = await self.connect()
        query = """
            UPDATE clawcluster.work_items
            SET status = $2,
                updated_at = now()
            WHERE id = $1
        """
        async with pool.acquire() as connection:
            await connection.execute(query, work_item_id, status)

    async def upsert_external_refs(self, work_item_id: str, refs: dict[str, object]) -> dict[str, object]:
        payload = {key: value for key, value in refs.items() if value is not None}
        if not payload:
            return {}

        pool = await self.connect()
        columns = ["work_item_id", *payload.keys()]
        placeholders = ", ".join(f"${index}" for index in range(1, len(columns) + 1))
        update_assignments = ", ".join(f"{column} = EXCLUDED.{column}" for column in payload)
        query = f"""
            INSERT INTO clawcluster.external_refs ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT (work_item_id)
            DO UPDATE SET
                {update_assignments},
                updated_at = now()
            RETURNING *
        """

        async with pool.acquire() as connection:
            record = await connection.fetchrow(query, work_item_id, *payload.values())

        if record is None:
            raise RuntimeError("Supabase upsert did not return an external_refs row")

        return {key: record[key] for key in payload if key in record and record[key] is not None}
