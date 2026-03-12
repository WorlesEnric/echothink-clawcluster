from __future__ import annotations

import logging
import secrets
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from models.publish import PublishRequest, PublishResult, PublishStatus

router = APIRouter()
logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(auto_error=False)


def get_container(request: Request) -> Any:
    return request.app.state.container


def require_publish_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> None:
    settings = request.app.state.container.settings
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    expected_token = settings.worker_jwt_secret.get_secret_value()
    if not secrets.compare_digest(credentials.credentials, expected_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token")


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    container = get_container(request)
    return {
        "status": "ok",
        "service": "publisher-bridge",
        "database_connected": container.supabase.is_connected,
        "supported_targets": list(container.registry._publishers.keys()),
    }


@router.post("/publish", response_model=PublishResult)
async def publish(
    publish_request: PublishRequest,
    request: Request,
    _: None = Depends(require_publish_token),
) -> PublishResult:
    container = get_container(request)
    logger.info(
        "publish.request.received",
        extra={
            "work_item_id": publish_request.work_item_id,
            "task_run_id": str(publish_request.task_run_id),
            "target": publish_request.target.value,
        },
    )

    existing_refs = await container.supabase.get_external_refs(publish_request.work_item_id)
    if container.supabase.target_ref_present(existing_refs, publish_request.target):
        await container.supabase.update_work_item_status(publish_request.work_item_id, "complete")
        logger.info(
            "publish.request.skipped",
            extra={
                "work_item_id": publish_request.work_item_id,
                "target": publish_request.target.value,
            },
        )
        return PublishResult(
            work_item_id=publish_request.work_item_id,
            task_run_id=publish_request.task_run_id,
            target=publish_request.target,
            success=True,
            status="skipped",
            idempotent=True,
            message="Publish target already exists in external_refs.",
            external_refs=existing_refs,
        )

    await container.supabase.update_work_item_status(publish_request.work_item_id, "publishing")
    try:
        publisher = container.registry.get(publish_request.target)
        publish_result = await publisher.publish(publish_request)
        merged_refs = await container.supabase.upsert_external_refs(
            publish_request.work_item_id,
            publish_result.external_refs,
        )
        if publish_result.artifacts:
            await container.supabase.record_artifacts(publish_request.task_run_id, publish_result.artifacts)
        await container.supabase.update_work_item_status(publish_request.work_item_id, "complete")

        logger.info(
            "publish.request.completed",
            extra={
                "work_item_id": publish_request.work_item_id,
                "task_run_id": str(publish_request.task_run_id),
                "target": publish_request.target.value,
            },
        )
        return publish_result.model_copy(update={"external_refs": merged_refs})
    except ValueError as exc:
        await container.supabase.update_work_item_status(publish_request.work_item_id, "failed")
        logger.exception(
            "publish.request.invalid",
            extra={
                "work_item_id": publish_request.work_item_id,
                "task_run_id": str(publish_request.task_run_id),
                "target": publish_request.target.value,
            },
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        await container.supabase.update_work_item_status(publish_request.work_item_id, "failed")
        logger.exception(
            "publish.request.failed",
            extra={
                "work_item_id": publish_request.work_item_id,
                "task_run_id": str(publish_request.task_run_id),
                "target": publish_request.target.value,
            },
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Failed to publish artifact") from exc


@router.get("/publish/{task_run_id}/status", response_model=PublishStatus)
async def publish_status(task_run_id: UUID, request: Request) -> PublishStatus:
    container = get_container(request)
    publish_status = await container.supabase.get_publish_status(task_run_id)
    if publish_status is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task run not found")
    return publish_status
