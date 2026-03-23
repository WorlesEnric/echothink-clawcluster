from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from src.handlers.base import PayloadNormalizationError
from src.models.webhooks import GitLabWebhookPayload, OutlineWebhookPayload
from src.models.work_item import SourceType, WorkItemCreate

router = APIRouter()
logger = logging.getLogger(__name__)

_OUTLINE_SIGNATURE_HEADERS = (
    "X-Outline-Signature-256",
    "X-Outline-Signature",
    "X-Signature-256",
)
_GITLAB_SIGNATURE_HEADERS = (
    "X-Gitlab-Signature-256",
    "X-Gitlab-Signature",
    "X-Hub-Signature-256",
)


class SignatureValidationError(ValueError):
    pass


def _extract_signature(request: Request, header_names: tuple[str, ...]) -> str:
    for header_name in header_names:
        signature = request.headers.get(header_name)
        if signature:
            return signature
    raise SignatureValidationError(f"Missing signature header; expected one of {', '.join(header_names)}")


def _verify_hmac_signature(body: bytes, signature: str, secret: str) -> None:
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    normalized = signature.strip().lower()
    if normalized.startswith("sha256="):
        normalized = normalized.removeprefix("sha256=")

    if not hmac.compare_digest(normalized, expected):
        raise SignatureValidationError("Webhook signature mismatch")


async def _persist_and_fanout(request: Request, work_item: WorkItemCreate) -> JSONResponse:
    supabase = request.app.state.supabase
    minio = request.app.state.minio
    manager = request.app.state.manager
    spec_markdown = work_item.render_spec_markdown()

    try:
        stored = await supabase.insert_work_item(work_item)
    except Exception as exc:
        logger.exception(
            "work_item_insert_failed",
            extra={"source_type": work_item.source_type.value, "source_ref": work_item.source_ref},
        )
        raise HTTPException(status_code=503, detail="Unable to persist work item") from exc

    tasks = {
        "storage": asyncio.create_task(
            minio.stage_work_item_spec(stored.id, spec_markdown)
        ),
        "manager": asyncio.create_task(manager.notify_work_item(stored, spec_markdown)),
    }
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    processing: dict[str, dict[str, Any]] = {}
    for name, result in zip(tasks, results, strict=True):
        if isinstance(result, Exception):
            logger.error(
                "downstream_delivery_failed",
                extra={"target": name, "work_item_id": stored.id, "error": str(result)},
            )
            processing[name] = {"status": "error", "detail": str(result)}
            continue

        processing[name] = {"status": "ok"}
        if name == "storage":
            processing[name]["path"] = result
        elif isinstance(result, dict) and result:
            processing[name]["response"] = result

    manager_response = processing.get("manager", {}).get("response")
    if processing.get("manager", {}).get("status") == "ok":
        try:
            sync_payload: dict[str, Any] = {"work_item_status": "assigned"}
            await supabase.update_work_item_status(stored.id, "assigned")

            matrix_room_id = ""
            if isinstance(manager_response, dict):
                matrix_room_id = str(manager_response.get("matrix_room_id") or "").strip()
            if matrix_room_id:
                await supabase.upsert_external_refs(stored.id, {"matrix_room_id": matrix_room_id})
                sync_payload["matrix_room_id"] = matrix_room_id

            processing["sync"] = {"status": "ok", **sync_payload}
        except Exception as exc:
            logger.error(
                "post_fanout_sync_failed",
                extra={"work_item_id": stored.id, "error": str(exc)},
            )
            processing["sync"] = {"status": "error", "detail": str(exc)}

    status_code = 201 if all(item["status"] == "ok" for item in processing.values()) else 202
    return JSONResponse(
        status_code=status_code,
        content={
            "work_item": stored.model_dump(mode="json"),
            "processing": processing,
        },
    )


@router.post("/webhooks/outline")
async def ingest_outline_webhook(request: Request) -> JSONResponse:
    body = await request.body()

    try:
        signature = _extract_signature(request, _OUTLINE_SIGNATURE_HEADERS)
        _verify_hmac_signature(body, signature, request.app.state.settings.webhook_secret)
        payload = OutlineWebhookPayload.model_validate_json(body)
        work_item = await request.app.state.outline_handler.to_work_item(payload)
    except SignatureValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ValidationError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid Outline webhook payload") from exc
    except PayloadNormalizationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return await _persist_and_fanout(request, work_item)


@router.post("/webhooks/gitlab")
async def ingest_gitlab_webhook(request: Request) -> JSONResponse:
    body = await request.body()

    try:
        signature = _extract_signature(request, _GITLAB_SIGNATURE_HEADERS)
        _verify_hmac_signature(body, signature, request.app.state.settings.webhook_secret)
        payload = GitLabWebhookPayload.model_validate_json(body)
        work_item = await request.app.state.gitlab_handler.to_work_item(payload)
    except SignatureValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ValidationError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid GitLab webhook payload") from exc
    except PayloadNormalizationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return await _persist_and_fanout(request, work_item)


@router.post("/work-items")
async def create_manual_work_item(work_item: WorkItemCreate, request: Request) -> JSONResponse:
    if work_item.source_type is not SourceType.manual:
        work_item = work_item.model_copy(update={"source_type": SourceType.manual})
    return await _persist_and_fanout(request, work_item)


@router.get("/health")
async def healthcheck(request: Request) -> JSONResponse:
    supabase = request.app.state.supabase
    minio = request.app.state.minio
    manager = request.app.state.manager

    dependency_names = ("database", "manager", "storage")
    results = await asyncio.gather(
        supabase.ping(),
        manager.ping(),
        minio.ping(),
        return_exceptions=True,
    )

    dependencies: dict[str, dict[str, Any]] = {}
    for name, result in zip(dependency_names, results, strict=True):
        if isinstance(result, Exception):
            dependencies[name] = {"status": "down", "detail": str(result)}
            continue
        dependencies[name] = {"status": "ok" if result else "down"}

    core_dependencies_ok = all(
        dependencies[name]["status"] == "ok" for name in ("database", "manager")
    )
    payload = {
        "status": "ok" if core_dependencies_ok else "degraded",
        "version": request.app.version,
        "dependencies": dependencies,
    }
    return JSONResponse(status_code=200 if core_dependencies_ok else 503, content=payload)
