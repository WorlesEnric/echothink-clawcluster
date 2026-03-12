from __future__ import annotations

import json
import logging
from typing import Any, Iterable
from uuid import UUID

import asyncpg
from asyncpg.pool import Pool

from models.publish import PublishedArtifact, PublishStatus, PublishTarget

logger = logging.getLogger(__name__)

EXTERNAL_REF_COLUMNS = (
    "outline_doc_id",
    "gitlab_project_id",
    "gitlab_issue_iid",
    "gitlab_mr_iid",
    "dify_workflow_id",
    "n8n_workflow_id",
    "matrix_room_id",
    "hatchet_workflow_run_id",
)

TARGET_EXTERNAL_REF_FIELD: dict[PublishTarget, str] = {
    PublishTarget.OUTLINE: "outline_doc_id",
    PublishTarget.GITLAB_BRANCH: "gitlab_project_id",
    PublishTarget.GITLAB_MR: "gitlab_mr_iid",
    PublishTarget.DIFY: "dify_workflow_id",
    PublishTarget.N8N: "n8n_workflow_id",
}


class SupabaseRepository:
    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 5) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: Pool | None = None

    @property
    def is_connected(self) -> bool:
        return self._pool is not None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
            command_timeout=30,
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def get_external_refs(self, work_item_id: str) -> dict[str, Any]:
        query = """
            SELECT *
            FROM clawcluster.external_refs
            WHERE work_item_id = $1
        """
        record = await self._require_pool().fetchrow(query, work_item_id)
        if record is None:
            return {}
        return self._extract_external_refs(record)

    async def is_target_published(self, work_item_id: str, target: PublishTarget) -> bool:
        refs = await self.get_external_refs(work_item_id)
        return self.target_ref_present(refs, target)

    def target_ref_present(self, refs: dict[str, Any], target: PublishTarget) -> bool:
        field_name = TARGET_EXTERNAL_REF_FIELD[target]
        value = refs.get(field_name)
        return value not in (None, "", [], {})

    async def upsert_external_refs(self, work_item_id: str, refs: dict[str, Any]) -> dict[str, Any]:
        payload = {key: value for key, value in refs.items() if key in EXTERNAL_REF_COLUMNS and value is not None}
        if not payload:
            return await self.get_external_refs(work_item_id)

        columns = ["work_item_id", *payload.keys()]
        placeholders = ", ".join(f"${index}" for index in range(1, len(columns) + 1))
        values = [work_item_id, *payload.values()]
        updates = ", ".join(
            f"{column} = COALESCE(EXCLUDED.{column}, clawcluster.external_refs.{column})"
            for column in payload
        )
        query = f"""
            INSERT INTO clawcluster.external_refs ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT (work_item_id)
            DO UPDATE SET
                {updates},
                updated_at = now()
            RETURNING *
        """
        record = await self._require_pool().fetchrow(query, *values)
        if record is None:
            raise RuntimeError("Failed to upsert external refs")
        return self._extract_external_refs(record)

    async def update_work_item_status(self, work_item_id: str, status: str) -> None:
        query = """
            UPDATE clawcluster.work_items
            SET status = $2, updated_at = now()
            WHERE id = $1
        """
        await self._require_pool().execute(query, work_item_id, status)
        logger.info("work_item.status.updated", extra={"work_item_id": work_item_id, "status": status})

    async def record_artifacts(self, task_run_id: UUID, artifacts: Iterable[PublishedArtifact]) -> None:
        query = """
            INSERT INTO clawcluster.artifacts (task_run_id, kind, uri, checksum, size_bytes, metadata_json)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        """
        rows = [
            (
                task_run_id,
                artifact.kind,
                artifact.uri,
                artifact.checksum,
                artifact.size_bytes,
                json.dumps(artifact.metadata),
            )
            for artifact in artifacts
        ]
        if rows:
            await self._require_pool().executemany(query, rows)

    async def get_publish_status(self, task_run_id: UUID) -> PublishStatus | None:
        task_query = """
            SELECT
                tr.id AS task_run_id,
                tr.status AS task_run_status,
                wi.id AS work_item_id,
                wi.status AS work_item_status,
                er.outline_doc_id,
                er.gitlab_project_id,
                er.gitlab_issue_iid,
                er.gitlab_mr_iid,
                er.dify_workflow_id,
                er.n8n_workflow_id,
                er.matrix_room_id,
                er.hatchet_workflow_run_id
            FROM clawcluster.task_runs tr
            JOIN clawcluster.work_items wi ON wi.id = tr.work_item_id
            LEFT JOIN clawcluster.external_refs er ON er.work_item_id = wi.id
            WHERE tr.id = $1
        """
        task_record = await self._require_pool().fetchrow(task_query, task_run_id)
        if task_record is None:
            return None

        artifacts_query = """
            SELECT kind, uri, checksum, size_bytes, metadata_json
            FROM clawcluster.artifacts
            WHERE task_run_id = $1
            ORDER BY created_at ASC
        """
        artifact_records = await self._require_pool().fetch(artifacts_query, task_run_id)
        external_refs = self._extract_external_refs(task_record)
        return PublishStatus(
            work_item_id=task_record["work_item_id"],
            task_run_id=task_record["task_run_id"],
            work_item_status=task_record["work_item_status"],
            task_run_status=task_record["task_run_status"],
            external_refs=external_refs,
            published_targets=self._published_targets(external_refs),
            artifacts=[
                PublishedArtifact(
                    kind=record["kind"],
                    uri=record["uri"],
                    checksum=record["checksum"],
                    size_bytes=record["size_bytes"],
                    metadata=dict(record["metadata_json"] or {}),
                )
                for record in artifact_records
            ],
        )

    def _extract_external_refs(self, record: asyncpg.Record) -> dict[str, Any]:
        return {
            column: record[column]
            for column in EXTERNAL_REF_COLUMNS
            if column in record.keys() and record[column] is not None
        }

    def _published_targets(self, refs: dict[str, Any]) -> list[PublishTarget]:
        published_targets: list[PublishTarget] = []
        for target, column in TARGET_EXTERNAL_REF_FIELD.items():
            if refs.get(column) not in (None, "", [], {}):
                published_targets.append(target)
        return published_targets

    def _require_pool(self) -> Pool:
        if self._pool is None:
            raise RuntimeError("SupabaseRepository is not connected")
        return self._pool
