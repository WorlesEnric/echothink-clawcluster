from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from src.clients.intake_bridge import IntakeBridgeClient
from src.clients.taskcenter import TaskCenterCallbackClient
from src.config import Settings
from src.models.dispatch import (
    DispatchAcceptedResponse,
    DispatchRequest,
    DispatchSnapshot,
    DispatchStatusResponse,
)
from src.storage.repository import TaskCenterBridgeRepository

_FAMILY_TO_KIND: dict[str, str] = {
    "planner": "plan.breakdown",
    "workflow": "workflow.author",
    "coding": "code.implement",
    "qa": "qa.validate",
    "knowledge": "knowledge.sync",
}

_TERMINAL_EVENT_BY_STATUS = {
    "succeeded": "dispatch.completed",
    "complete": "dispatch.completed",
    "failed": "dispatch.failed",
    "cancelled": "dispatch.cancelled",
}


class TaskCenterBridgeService:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: TaskCenterBridgeRepository,
        intake_bridge: IntakeBridgeClient,
        callback_client: TaskCenterCallbackClient,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._intake_bridge = intake_bridge
        self._callback_client = callback_client

    async def accept_dispatch(self, request: DispatchRequest) -> DispatchAcceptedResponse:
        existing = await self._repository.fetch_dispatch_snapshot(request.dispatch_id)
        if existing is not None:
            return self._accepted_response(existing)

        intake_payload = self._build_work_item_payload(request)
        bridge_response = await self._intake_bridge.create_work_item(intake_payload)
        work_item_json = dict(bridge_response.get("work_item") or {})
        work_item_id = str(work_item_json.get("id") or "").strip()
        if not work_item_id:
            raise RuntimeError("Intake bridge did not return a work item id")

        processing = dict(bridge_response.get("processing") or {})
        status = "accepted"
        if any(item.get("status") == "error" for item in processing.values()):
            status = "accepted_degraded"

        snapshot = await self._repository.create_dispatch_ref(
            request,
            work_item_id=work_item_id,
            state=status,
            bridge_response=bridge_response,
        )
        await self._enqueue_initial_events(snapshot)
        return self._accepted_response(snapshot)

    async def get_dispatch_status(self, dispatch_id: str) -> DispatchStatusResponse | None:
        snapshot = await self._repository.fetch_dispatch_snapshot(dispatch_id)
        if snapshot is None:
            return None

        approval_state = snapshot.approval_decision
        if approval_state == "pending":
            approval_state = "awaiting_approval"

        return DispatchStatusResponse(
            dispatch_id=snapshot.dispatch_id,
            task_id=snapshot.task_id,
            task_node_id=snapshot.task_node_id,
            workspace_id=snapshot.workspace_id,
            status=snapshot.state,
            correlation_ref=self._correlation_ref(snapshot.dispatch_id),
            work_item_id=snapshot.work_item_id,
            task_run_id=snapshot.task_run_id,
            work_item_status=snapshot.work_item_status,
            task_run_status=snapshot.task_run_status,
            approval_state=approval_state,
            matrix_room_id=snapshot.matrix_room_id,
            artifacts=snapshot.artifacts,
            external_refs=snapshot.external_refs,
            processing=snapshot.processing,
            sync_state=snapshot.sync_state,
        )

    async def reconcile_once(self) -> None:
        snapshots = await self._repository.list_dispatch_snapshots(limit=self._settings.reconcile_batch_size)
        for snapshot in snapshots:
            await self._reconcile_snapshot(snapshot)

    async def deliver_outbox_once(self) -> None:
        records = await self._repository.list_pending_outbox(limit=self._settings.outbox_batch_size)
        for record in records:
            try:
                await self._callback_client.post_event(record.payload_json)
            except Exception as error:
                await self._repository.mark_outbox_failed(record.id, str(error))
                continue

            await self._repository.mark_outbox_delivered(record.id)
            await self._repository.apply_delivery_sync(
                dispatch_id=record.dispatch_id,
                event_type=record.event_type,
                payload_json=record.payload_json,
            )

    async def _enqueue_initial_events(self, snapshot: DispatchSnapshot) -> None:
        payload = self._event_payload(
            snapshot,
            event_type="dispatch.accepted",
            status=snapshot.state,
            summary="ClawCluster accepted the dispatch and created a work item.",
            metadata={"processing": snapshot.processing},
        )
        await self._queue_event(snapshot.dispatch_id, "dispatch.accepted", payload, "accepted")

    async def _reconcile_snapshot(self, snapshot: DispatchSnapshot) -> None:
        sync_state = dict(snapshot.sync_state or {})

        if snapshot.matrix_room_id and sync_state.get("matrix_room_id") != snapshot.matrix_room_id:
            payload = self._event_payload(
                snapshot,
                event_type="dispatch.room_ready",
                status=snapshot.work_item_status or snapshot.state,
                summary="ClawCluster attached an execution room for this dispatch.",
            )
            payload["matrix_room_id"] = snapshot.matrix_room_id
            await self._queue_event(
                snapshot.dispatch_id,
                "dispatch.room_ready",
                payload,
                f"room:{snapshot.matrix_room_id}",
            )

        if snapshot.task_run_status == "running" and sync_state.get("started_task_run_id") != snapshot.task_run_id:
            payload = self._event_payload(
                snapshot,
                event_type="dispatch.started",
                status="running",
                summary="A ClawCluster worker started executing the dispatch.",
            )
            await self._queue_event(
                snapshot.dispatch_id,
                "dispatch.started",
                payload,
                f"started:{snapshot.task_run_id or snapshot.work_item_id}",
            )

        progress_status = snapshot.work_item_status or snapshot.task_run_status
        if progress_status in {"assigned", "in_progress", "approved", "blocked", "publishing"}:
            progress_key = json.dumps(
                {
                    "work_item_status": snapshot.work_item_status,
                    "task_run_status": snapshot.task_run_status,
                    "task_run_id": snapshot.task_run_id,
                },
                sort_keys=True,
            )
            if sync_state.get("last_progress_key") != progress_key:
                payload = self._event_payload(
                    snapshot,
                    event_type="dispatch.progress",
                    status=progress_status,
                    summary=f"ClawCluster updated work item status to {progress_status}.",
                )
                await self._queue_event(
                    snapshot.dispatch_id,
                    "dispatch.progress",
                    payload,
                    f"progress:{self._hash(progress_key)}",
                )

        if snapshot.approval_decision == "pending" and sync_state.get("approval_id") != snapshot.approval_id:
            payload = self._event_payload(
                snapshot,
                event_type="dispatch.awaiting_approval",
                status="awaiting_approval",
                summary="ClawCluster is waiting for an approval decision before proceeding.",
                metadata={"approval_notes": snapshot.approval_notes},
            )
            payload["approval_id"] = snapshot.approval_id
            await self._queue_event(
                snapshot.dispatch_id,
                "dispatch.awaiting_approval",
                payload,
                f"approval:{snapshot.approval_id}",
            )

        if snapshot.artifacts:
            delivered_count = int(sync_state.get("artifact_count") or 0)
            current_count = len(snapshot.artifacts)
            if current_count > delivered_count:
                payload = self._event_payload(
                    snapshot,
                    event_type="artifact.created",
                    status=snapshot.work_item_status or snapshot.task_run_status or snapshot.state,
                    summary=f"ClawCluster produced {current_count - delivered_count} new artifacts.",
                    artifacts=snapshot.artifacts,
                )
                await self._queue_event(
                    snapshot.dispatch_id,
                    "artifact.created",
                    payload,
                    f"artifacts:{snapshot.task_run_id or snapshot.work_item_id}:{current_count}",
                )

        if snapshot.external_refs:
            external_refs_hash = self._hash(json.dumps(snapshot.external_refs, sort_keys=True))
            if sync_state.get("external_refs_hash") != external_refs_hash:
                payload = self._event_payload(
                    snapshot,
                    event_type="dispatch.publish_state",
                    status=snapshot.work_item_status or snapshot.state,
                    summary="ClawCluster updated published external references.",
                    metadata={"external_refs_hash": external_refs_hash},
                )
                await self._queue_event(
                    snapshot.dispatch_id,
                    "dispatch.publish_state",
                    payload,
                    f"publish:{external_refs_hash}",
                )

        terminal_status = snapshot.task_run_status or snapshot.work_item_status
        event_type = _TERMINAL_EVENT_BY_STATUS.get(str(terminal_status))
        if event_type and sync_state.get("terminal_status") != terminal_status:
            summary = {
                "dispatch.completed": "ClawCluster completed the dispatch successfully.",
                "dispatch.failed": "ClawCluster reported a failed dispatch.",
                "dispatch.cancelled": "ClawCluster cancelled the dispatch.",
            }[event_type]
            payload = self._event_payload(
                snapshot,
                event_type=event_type,
                status=str(terminal_status),
                summary=summary,
            )
            await self._queue_event(
                snapshot.dispatch_id,
                event_type,
                payload,
                f"terminal:{snapshot.task_run_id or snapshot.work_item_id}:{terminal_status}",
            )

    async def _queue_event(
        self,
        dispatch_id: str,
        event_type: str,
        payload: dict[str, Any],
        dedupe_suffix: str,
    ) -> None:
        await self._repository.enqueue_outbox_event(
            dispatch_id=dispatch_id,
            event_type=event_type,
            payload_json=payload,
            dedupe_key=f"{dispatch_id}:{dedupe_suffix}",
        )

    def _accepted_response(self, snapshot: DispatchSnapshot) -> DispatchAcceptedResponse:
        return DispatchAcceptedResponse(
            dispatch_id=snapshot.dispatch_id,
            accepted=True,
            work_item_id=snapshot.work_item_id,
            status=snapshot.state,
            correlation_ref=self._correlation_ref(snapshot.dispatch_id),
            processing=snapshot.processing,
        )

    def _build_work_item_payload(self, request: DispatchRequest) -> dict[str, Any]:
        kind = self._resolve_work_item_kind(request)
        constraints_json = {
            "dispatch_id": request.dispatch_id,
            "task_id": request.task_id,
            "task_node_id": request.task_node_id,
            "workspace_id": request.workspace_id,
            "task_type": request.task_type,
            "execution_family": request.execution_family,
            "success_mode": request.success_mode,
            "source_title": request.title,
            "source_content": request.summary,
            "context": request.context,
            "linked_entities": request.linked_entities,
            "acceptance_specs": request.acceptance_specs,
            "spec_uri": request.spec_uri,
            "artifacts_prefix": request.artifacts_prefix,
        }
        return {
            "workspace_id": request.workspace_id,
            "kind": kind,
            "source_type": "manual",
            "source_ref": request.dispatch_id,
            "objective": request.objective or request.summary or request.title,
            "acceptance_criteria": self._acceptance_criteria(request),
            "constraints_json": {key: value for key, value in constraints_json.items() if value not in (None, "", [], {})},
            "priority": self._normalize_priority(request.priority),
            "risk_level": self._normalize_risk_level(request.risk_level),
            "approval_policy": self._normalize_approval_policy(request.approval_policy),
            "requested_by": request.requested_by or "taskcenter",
        }

    def _resolve_work_item_kind(self, request: DispatchRequest) -> str:
        family = (request.execution_family or "").strip().lower()
        if family in _FAMILY_TO_KIND:
            return _FAMILY_TO_KIND[family]

        task_type = (request.task_type or "").strip().lower()
        if "review" in task_type:
            return "code.review"
        if "qa" in task_type or "validate" in task_type or "test" in task_type:
            return "qa.validate"
        if "workflow" in task_type or "automation" in task_type:
            return "workflow.author"
        if "plan" in task_type:
            return "plan.breakdown"
        if "knowledge" in task_type or "sync" in task_type:
            return "knowledge.sync"
        return "code.implement"

    def _acceptance_criteria(self, request: DispatchRequest) -> list[str]:
        criteria = []
        for spec in request.acceptance_specs:
            check_name = str(spec.get("check_name") or spec.get("name") or "").strip()
            check_type = str(spec.get("check_type") or "").strip()
            if check_name:
                criteria.append(check_name)
            elif check_type:
                criteria.append(f"Pass {check_type} acceptance check")
        return criteria

    def _normalize_priority(self, priority: int) -> int:
        return max(1, min(100, int(priority or 50)))

    def _normalize_risk_level(self, risk_level: str | None) -> str:
        normalized = (risk_level or "medium").strip().lower()
        if normalized in {"low", "medium", "high", "critical"}:
            return normalized
        return "medium"

    def _normalize_approval_policy(self, approval_policy: str | None) -> str:
        normalized = (approval_policy or "medium").strip().lower()
        if normalized in {"none", "low", "medium", "high", "critical"}:
            return normalized
        if normalized == "manual":
            return "critical"
        return "medium"

    def _event_payload(
        self,
        snapshot: DispatchSnapshot,
        *,
        event_type: str,
        status: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "event_type": event_type,
            "dispatch_id": snapshot.dispatch_id,
            "task_id": snapshot.task_id,
            "task_node_id": snapshot.task_node_id,
            "work_item_id": snapshot.work_item_id,
            "task_run_id": snapshot.task_run_id,
            "workspace_id": snapshot.workspace_id,
            "timestamp": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
            "status": status,
            "summary": summary,
            "correlation_ref": self._correlation_ref(snapshot.dispatch_id),
            "actor_ref": "clawcluster/task-center-bridge",
            "entity_refs": self._entity_refs(snapshot),
            "result": self._result_payload(snapshot),
            "metadata": self._metadata_payload(snapshot, extra=metadata or {}),
        }
        if artifacts is not None:
            payload["artifacts"] = artifacts
        if snapshot.matrix_room_id:
            payload["matrix_room_id"] = snapshot.matrix_room_id
        return payload

    def _entity_refs(self, snapshot: DispatchSnapshot) -> list[str]:
        linked_entities = snapshot.dispatch_payload.get("linked_entities") or []
        refs: list[str] = []
        for entity in linked_entities:
            if not isinstance(entity, dict):
                continue
            entity_id = str(entity.get("entity_id") or "").strip()
            if entity_id:
                refs.append(entity_id)
        return refs

    def _result_payload(self, snapshot: DispatchSnapshot) -> dict[str, Any]:
        result = {
            "work_item_status": snapshot.work_item_status,
            "task_run_status": snapshot.task_run_status,
            "result_summary": snapshot.task_run_result_summary,
            "error_message": snapshot.task_run_error_message,
            "approval_decision": snapshot.approval_decision,
        }
        return {key: value for key, value in result.items() if value not in (None, "", [], {})}

    def _metadata_payload(self, snapshot: DispatchSnapshot, *, extra: dict[str, Any]) -> dict[str, Any]:
        metadata = {
            "processing": snapshot.processing,
            "external_refs": snapshot.external_refs,
            "approval_id": snapshot.approval_id,
        }
        metadata.update(extra)
        return {key: value for key, value in metadata.items() if value not in (None, "", [], {})}

    def _correlation_ref(self, dispatch_id: str) -> str:
        return f"dispatch:{dispatch_id}"

    def _hash(self, value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
