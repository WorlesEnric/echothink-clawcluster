from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.models.dispatch import DispatchAcceptedResponse, DispatchRequest, DispatchStatusResponse, HealthResponse
from src.services.bridge import TaskCenterBridgeService

router = APIRouter()


def get_service(request: Request) -> TaskCenterBridgeService:
    return request.app.state.bridge_service


@router.post("/api/v1/dispatches", response_model=DispatchAcceptedResponse)
async def create_dispatch(
    payload: DispatchRequest,
    service: TaskCenterBridgeService = Depends(get_service),
) -> DispatchAcceptedResponse:
    try:
        return await service.accept_dispatch(payload)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error


@router.get("/api/v1/dispatches/{dispatch_id}", response_model=DispatchStatusResponse)
async def get_dispatch(
    dispatch_id: str,
    service: TaskCenterBridgeService = Depends(get_service),
) -> DispatchStatusResponse:
    response = await service.get_dispatch_status(dispatch_id)
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dispatch not found")
    return response


@router.get("/health", response_model=HealthResponse)
async def healthcheck(request: Request) -> HealthResponse:
    repository = request.app.state.repository
    intake_bridge = request.app.state.intake_bridge

    dependencies = {}
    try:
        dependencies["database"] = {"status": "ok" if await repository.ping() else "down"}
    except Exception as error:
        dependencies["database"] = {"status": "down", "detail": str(error)}

    try:
        dependencies["intake_bridge"] = {"status": "ok" if await intake_bridge.ping() else "down"}
    except Exception as error:
        dependencies["intake_bridge"] = {"status": "down", "detail": str(error)}

    status_value = "ok" if all(dep["status"] == "ok" for dep in dependencies.values()) else "degraded"
    return HealthResponse(status=status_value, dependencies=dependencies)
