from __future__ import annotations

import json
from typing import Any

import asyncpg
from asyncpg.pool import Pool

from src.models.dispatch import DispatchRequest, DispatchSnapshot, OutboxRecord

_EXTERNAL_REF_COLUMNS = (
    "outline_doc_id",
    "gitlab_project_id",
    "gitlab_issue_iid",
    "gitlab_mr_iid",
    "dify_workflow_id",
    "n8n_workflow_id",
    "matrix_room_id",
    "hatchet_workflow_run_id",
)


class TaskCenterBridgeRepository:
    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 5) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: Pool | None = None

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                dsn=self._dsn,
                min_size=self._min_size,
                max_size=self._max_size,
                init=self._initialize_connection,
                command_timeout=30,
            )

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
        row = await self._require_pool().fetchval("SELECT 1")
        return row == 1

    async def ensure_schema(self) -> None:
        query = """
            CREATE TABLE IF NOT EXISTS clawcluster.task_center_refs (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                dispatch_id text NOT NULL UNIQUE,
                task_id text NOT NULL,
                task_node_id text,
                workspace_id text NOT NULL,
                work_item_id text NOT NULL UNIQUE REFERENCES clawcluster.work_items(id) ON DELETE CASCADE,
                state text NOT NULL DEFAULT 'accepted',
                dispatch_payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                bridge_response_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                sync_state_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT task_center_refs_dispatch_payload_json_type_check CHECK (jsonb_typeof(dispatch_payload_json) = 'object'),
                CONSTRAINT task_center_refs_bridge_response_json_type_check CHECK (jsonb_typeof(bridge_response_json) = 'object'),
                CONSTRAINT task_center_refs_sync_state_json_type_check CHECK (jsonb_typeof(sync_state_json) = 'object')
            );

            CREATE TABLE IF NOT EXISTS clawcluster.task_center_outbox (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                dispatch_id text NOT NULL REFERENCES clawcluster.task_center_refs(dispatch_id) ON DELETE CASCADE,
                event_type text NOT NULL,
                payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                dedupe_key text NOT NULL UNIQUE,
                delivery_status text NOT NULL DEFAULT 'pending',
                retry_count integer NOT NULL DEFAULT 0,
                last_attempt_at timestamptz,
                delivered_at timestamptz,
                last_error text,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT task_center_outbox_payload_json_type_check CHECK (jsonb_typeof(payload_json) = 'object'),
                CONSTRAINT task_center_outbox_delivery_status_check CHECK (delivery_status IN ('pending', 'delivered', 'failed', 'dead_letter'))
            );

            CREATE INDEX IF NOT EXISTS task_center_refs_work_item_id_idx
                ON clawcluster.task_center_refs (work_item_id);

            CREATE INDEX IF NOT EXISTS task_center_outbox_delivery_status_idx
                ON clawcluster.task_center_outbox (delivery_status, created_at);
        """
        await self._require_pool().execute(query)

    async def create_dispatch_ref(
        self,
        request: DispatchRequest,
        *,
        work_item_id: str,
        state: str,
        bridge_response: dict[str, Any],
    ) -> DispatchSnapshot:
        query = """
            INSERT INTO clawcluster.task_center_refs (
                dispatch_id,
                task_id,
                task_node_id,
                workspace_id,
                work_item_id,
                state,
                dispatch_payload_json,
                bridge_response_json
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb)
            ON CONFLICT (dispatch_id) DO NOTHING
        """
        await self._require_pool().execute(
            query,
            request.dispatch_id,
            request.task_id,
            request.task_node_id,
            request.workspace_id,
            work_item_id,
            state,
            request.model_dump(mode="json"),
            bridge_response,
        )
        snapshot = await self.fetch_dispatch_snapshot(request.dispatch_id)
        if snapshot is None:
            raise RuntimeError("Failed to load Task Center bridge correlation row")
        return snapshot

    async def fetch_dispatch_snapshot(self, dispatch_id: str) -> DispatchSnapshot | None:
        row = await self._require_pool().fetchrow(self._snapshot_query("WHERE ref.dispatch_id = $1"), dispatch_id)
        return self._snapshot_from_row(row) if row is not None else None

    async def list_dispatch_snapshots(self, limit: int) -> list[DispatchSnapshot]:
        rows = await self._require_pool().fetch(
            self._snapshot_query("ORDER BY ref.created_at ASC LIMIT $1"),
            limit,
        )
        return [self._snapshot_from_row(row) for row in rows]

    async def enqueue_outbox_event(
        self,
        *,
        dispatch_id: str,
        event_type: str,
        payload_json: dict[str, Any],
        dedupe_key: str,
    ) -> None:
        query = """
            INSERT INTO clawcluster.task_center_outbox (
                dispatch_id,
                event_type,
                payload_json,
                dedupe_key
            )
            VALUES ($1, $2, $3::jsonb, $4)
            ON CONFLICT (dedupe_key) DO NOTHING
        """
        await self._require_pool().execute(query, dispatch_id, event_type, payload_json, dedupe_key)

    async def list_pending_outbox(self, limit: int) -> list[OutboxRecord]:
        query = """
            SELECT id::text, dispatch_id, event_type, payload_json, retry_count
            FROM clawcluster.task_center_outbox
            WHERE delivery_status IN ('pending', 'failed')
              AND retry_count < 25
            ORDER BY created_at ASC
            LIMIT $1
        """
        rows = await self._require_pool().fetch(query, limit)
        return [OutboxRecord.model_validate(dict(row)) for row in rows]

    async def mark_outbox_delivered(self, outbox_id: str) -> None:
        query = """
            UPDATE clawcluster.task_center_outbox
            SET delivery_status = 'delivered',
                delivered_at = now(),
                last_attempt_at = now(),
                updated_at = now()
            WHERE id::text = $1
        """
        await self._require_pool().execute(query, outbox_id)

    async def mark_outbox_failed(self, outbox_id: str, error_message: str) -> None:
        query = """
            UPDATE clawcluster.task_center_outbox
            SET delivery_status = CASE WHEN retry_count + 1 >= 25 THEN 'dead_letter' ELSE 'failed' END,
                retry_count = retry_count + 1,
                last_attempt_at = now(),
                last_error = $2,
                updated_at = now()
            WHERE id::text = $1
        """
        await self._require_pool().execute(query, outbox_id, error_message[:2000])

    async def apply_delivery_sync(
        self,
        *,
        dispatch_id: str,
        event_type: str,
        payload_json: dict[str, Any],
    ) -> None:
        snapshot = await self.fetch_dispatch_snapshot(dispatch_id)
        if snapshot is None:
            return

        sync_state = dict(snapshot.sync_state or {})
        state = snapshot.state
        work_item_status = payload_json.get("status")

        if event_type == "dispatch.accepted":
            sync_state["accepted"] = True
            sync_state["processing"] = payload_json.get("metadata", {}).get("processing", {})
            state = "accepted"
        elif event_type == "dispatch.room_ready":
            matrix_room_id = payload_json.get("matrix_room_id")
            if matrix_room_id:
                sync_state["matrix_room_id"] = matrix_room_id
        elif event_type == "dispatch.started":
            sync_state["started_task_run_id"] = payload_json.get("task_run_id")
            state = "running"
        elif event_type == "dispatch.progress":
            sync_state["last_progress_status"] = work_item_status
            sync_state["last_progress_task_run_id"] = payload_json.get("task_run_id")
            state = "running"
        elif event_type == "dispatch.awaiting_approval":
            sync_state["approval_id"] = payload_json.get("approval_id")
            sync_state["approval_state"] = "pending"
            state = "awaiting_approval"
        elif event_type == "artifact.created":
            sync_state["artifact_count"] = len(payload_json.get("artifacts") or [])
        elif event_type == "dispatch.publish_state":
            sync_state["external_refs_hash"] = payload_json.get("metadata", {}).get("external_refs_hash")
        elif event_type in {"dispatch.completed", "dispatch.failed", "dispatch.cancelled"}:
            sync_state["terminal_status"] = work_item_status
            sync_state["task_run_id"] = payload_json.get("task_run_id")
            state = {
                "dispatch.completed": "completed",
                "dispatch.failed": "failed",
                "dispatch.cancelled": "cancelled",
            }[event_type]

        query = """
            UPDATE clawcluster.task_center_refs
            SET state = $2,
                sync_state_json = $3::jsonb,
                updated_at = now()
            WHERE dispatch_id = $1
        """
        await self._require_pool().execute(query, dispatch_id, state, sync_state)

    def _snapshot_query(self, suffix: str) -> str:
        return f"""
            SELECT
                ref.dispatch_id,
                ref.task_id,
                ref.task_node_id,
                ref.workspace_id,
                ref.work_item_id,
                ref.state,
                ref.dispatch_payload_json,
                ref.bridge_response_json,
                ref.sync_state_json,
                wi.status AS work_item_status,
                tr.id::text AS task_run_id,
                tr.status AS task_run_status,
                tr.result_summary AS task_run_result_summary,
                tr.error_message AS task_run_error_message,
                appr.id::text AS approval_id,
                appr.decision AS approval_decision,
                appr.notes AS approval_notes,
                er.matrix_room_id,
                jsonb_strip_nulls(
                    jsonb_build_object(
                        'outline_doc_id', er.outline_doc_id,
                        'gitlab_project_id', er.gitlab_project_id,
                        'gitlab_issue_iid', er.gitlab_issue_iid,
                        'gitlab_mr_iid', er.gitlab_mr_iid,
                        'dify_workflow_id', er.dify_workflow_id,
                        'n8n_workflow_id', er.n8n_workflow_id,
                        'matrix_room_id', er.matrix_room_id,
                        'hatchet_workflow_run_id', er.hatchet_workflow_run_id
                    )
                ) AS external_refs,
                COALESCE(arts.artifacts_json, '[]'::jsonb) AS artifacts
            FROM clawcluster.task_center_refs ref
            LEFT JOIN clawcluster.work_items wi
                ON wi.id = ref.work_item_id
            LEFT JOIN LATERAL (
                SELECT id, status, result_summary, error_message
                FROM clawcluster.task_runs
                WHERE work_item_id = ref.work_item_id
                ORDER BY COALESCE(started_at, created_at) DESC, created_at DESC
                LIMIT 1
            ) tr ON TRUE
            LEFT JOIN LATERAL (
                SELECT id, decision, notes
                FROM clawcluster.approvals
                WHERE work_item_id = ref.work_item_id
                ORDER BY created_at DESC
                LIMIT 1
            ) appr ON TRUE
            LEFT JOIN clawcluster.external_refs er
                ON er.work_item_id = ref.work_item_id
            LEFT JOIN LATERAL (
                SELECT COALESCE(
                    jsonb_agg(
                        jsonb_strip_nulls(
                            jsonb_build_object(
                                'id', id::text,
                                'kind', kind,
                                'uri', uri,
                                'checksum', checksum,
                                'size_bytes', size_bytes,
                                'metadata', metadata_json
                            )
                        )
                        ORDER BY created_at ASC
                    ),
                    '[]'::jsonb
                ) AS artifacts_json
                FROM clawcluster.artifacts
                WHERE tr.id IS NOT NULL AND task_run_id = tr.id
            ) arts ON TRUE
            {suffix}
        """

    def _snapshot_from_row(self, row: asyncpg.Record) -> DispatchSnapshot:
        payload = dict(row)
        external_refs = payload.get("external_refs") or {}
        if not isinstance(external_refs, dict):
            external_refs = {}
        artifacts = payload.get("artifacts") or []
        if not isinstance(artifacts, list):
            artifacts = []
        return DispatchSnapshot(
            dispatch_id=str(payload["dispatch_id"]),
            task_id=str(payload["task_id"]),
            task_node_id=payload.get("task_node_id"),
            workspace_id=str(payload["workspace_id"]),
            work_item_id=str(payload["work_item_id"]),
            state=str(payload["state"]),
            dispatch_payload=dict(payload.get("dispatch_payload_json") or {}),
            bridge_response=dict(payload.get("bridge_response_json") or {}),
            sync_state=dict(payload.get("sync_state_json") or {}),
            work_item_status=payload.get("work_item_status"),
            task_run_id=payload.get("task_run_id"),
            task_run_status=payload.get("task_run_status"),
            task_run_result_summary=payload.get("task_run_result_summary"),
            task_run_error_message=payload.get("task_run_error_message"),
            approval_id=payload.get("approval_id"),
            approval_decision=payload.get("approval_decision"),
            approval_notes=payload.get("approval_notes"),
            matrix_room_id=payload.get("matrix_room_id"),
            external_refs={key: value for key, value in external_refs.items() if key in _EXTERNAL_REF_COLUMNS and value is not None},
            artifacts=artifacts,
        )

    def _require_pool(self) -> Pool:
        if self._pool is None:
            raise RuntimeError("TaskCenterBridgeRepository is not connected")
        return self._pool
